"""M2-lite fixed-workload evaluation helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from urllib.parse import urljoin
import argparse
import csv
import json
import math
import platform
import statistics
import subprocess
import sys
import threading
import time
from typing import Any, Callable

import requests

from thermal_guardian.monitor import MonitorSnapshot, VcgencmdMonitor
from thermal_guardian.router import CHAT_COMPLETIONS_PATH, PROMPT_ID_HEADER


M2_MODES = ("q8_fixed", "q4_fixed", "controller")
REQUEST_FIELDS = [
    "ts",
    "prompt_id",
    "mode",
    "cooling",
    "ok",
    "status_code",
    "latency_ms",
    "tokens_out",
    "tokens_per_sec",
    "model",
    "url",
    "detail",
]
TELEMETRY_FIELDS = [
    "ts",
    "elapsed_sec",
    "mode",
    "cooling",
    "temp_c",
    "clock_hz",
    "throttled_hex",
]
MANUAL_POWER_FIELDS = [
    "run_dir",
    "condition",
    "run_id",
    "mwh",
    "elapsed_time",
    "voltage_v",
    "current_a",
    "power_w",
    "max_voltage_v",
    "max_current_a",
    "max_power_w",
    "meter_cpu_c",
    "photo_path",
    "note",
]
POWER_SUMMARY_FIELDS = [
    "condition",
    "run_dir",
    "requests",
    "tokens_out_total",
    "median_latency_ms",
    "iqr_latency_ms",
    "median_tokens_per_sec",
    "iqr_tokens_per_sec",
    "max_temp_c",
    "throttle_seen",
    "safety_stop",
    "mwh",
    "j_per_token",
    "note",
]
DEFAULT_SUMMARY_OUTPUT = "logs/m2_summary.json"
DEFAULT_PLOT_OUTPUT = "logs/m2_main_graph.svg"
DEFAULT_POWER_SUMMARY_OUTPUT = "logs/power_summary.csv"


@dataclass(frozen=True)
class M2Config:
    q8_url: str = "http://127.0.0.1:8081"
    q4_url: str = "http://127.0.0.1:8082"
    router_url: str = "http://127.0.0.1:8080"
    output_root: str = "data/m2"
    duration_sec: float = 600.0
    request_count: int | None = None
    interval_sec: float = 0.0
    arrival_interval_sec: float | None = None
    timeout_sec: float = 120.0
    max_consecutive_failures: int = 3
    prompt: str = "Reply with one short sentence about edge inference."
    max_tokens: int = 64
    model: str = "thermal-guardian"
    sampling_interval_sec: float = 2.0
    safety_temp_c: float = 82.0
    stop_on_throttle: bool = False
    cooling: str = "fan_on"
    prompt_id_prefix: str = "m2-lite"
    power_meter_model: str = ""
    room_temp_c: float | None = None

    def __post_init__(self) -> None:
        if self.duration_sec < 0:
            raise ValueError("duration_sec must be non-negative")
        if self.request_count is not None and self.request_count <= 0:
            raise ValueError("request_count must be positive when provided")
        if self.interval_sec < 0:
            raise ValueError("interval_sec must be non-negative")
        if self.arrival_interval_sec is not None:
            if self.arrival_interval_sec <= 0:
                raise ValueError("arrival_interval_sec must be positive when provided")
            if self.interval_sec > 0:
                raise ValueError(
                    "interval_sec and arrival_interval_sec are mutually exclusive"
                )
        if self.timeout_sec <= 0:
            raise ValueError("timeout_sec must be positive")
        if self.max_consecutive_failures <= 0:
            raise ValueError("max_consecutive_failures must be positive")
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if self.sampling_interval_sec <= 0:
            raise ValueError("sampling_interval_sec must be positive")
        if self.safety_temp_c <= 0:
            raise ValueError("safety_temp_c must be positive")
        if not self.cooling:
            raise ValueError("cooling must not be empty")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "M2Config":
        names = {field.name for field in fields(cls)}
        unknown = sorted(set(data) - names)
        if unknown:
            raise ValueError(f"unknown M2 config keys: {', '.join(unknown)}")
        return cls(**data)

    def url_for_mode(self, mode: str) -> str:
        if mode == "q8_fixed":
            return self.q8_url
        if mode == "q4_fixed":
            return self.q4_url
        if mode == "controller":
            return self.router_url
        raise ValueError(f"unknown M2 mode: {mode}")


@dataclass(frozen=True)
class M2RequestRow:
    ts: float
    prompt_id: str
    mode: str
    cooling: str
    ok: bool
    status_code: int | None
    latency_ms: float
    tokens_out: int
    tokens_per_sec: float
    model: str
    url: str
    detail: str

    def as_csv_row(self) -> dict[str, str]:
        return {
            "ts": f"{self.ts:.6f}",
            "prompt_id": self.prompt_id,
            "mode": self.mode,
            "cooling": self.cooling,
            "ok": str(self.ok).lower(),
            "status_code": "" if self.status_code is None else str(self.status_code),
            "latency_ms": f"{self.latency_ms:.3f}",
            "tokens_out": str(self.tokens_out),
            "tokens_per_sec": f"{self.tokens_per_sec:.6f}",
            "model": self.model,
            "url": self.url,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class M2TelemetryRow:
    ts: float
    elapsed_sec: float
    mode: str
    cooling: str
    temp_c: float
    clock_hz: int
    throttled_hex: str

    def as_csv_row(self) -> dict[str, str]:
        return {
            "ts": f"{self.ts:.6f}",
            "elapsed_sec": f"{self.elapsed_sec:.3f}",
            "mode": self.mode,
            "cooling": self.cooling,
            "temp_c": f"{self.temp_c:.3f}",
            "clock_hz": str(self.clock_hz),
            "throttled_hex": self.throttled_hex,
        }


@dataclass(frozen=True)
class M2RunResult:
    output_dir: Path
    request_rows: list[M2RequestRow]
    telemetry_rows: list[M2TelemetryRow]
    manifest: dict[str, Any]

    @property
    def ok(self) -> bool:
        return bool(self.request_rows) and all(row.ok for row in self.request_rows) and not bool(
            self.manifest.get("safety_stop")
        )


def load_m2_config(path: str | Path) -> M2Config:
    with Path(path).open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError("M2 config root must be a JSON object")
    return M2Config.from_dict(data)


def build_chat_payload(config: M2Config) -> dict[str, Any]:
    return {
        "model": config.model,
        "messages": [{"role": "user", "content": config.prompt}],
        "max_tokens": config.max_tokens,
        "temperature": 0.0,
    }


def run_m2(
    *,
    config: M2Config,
    mode: str,
    output_dir: str | Path | None = None,
    session: requests.Session | None = None,
    monitor: Any | None = None,
    background_telemetry: bool = True,
    now_func: Callable[[], float] = time.time,
    monotonic_func: Callable[[], float] = time.monotonic,
    sleep_func: Callable[[float], None] = time.sleep,
) -> M2RunResult:
    if mode not in M2_MODES:
        raise ValueError(f"mode must be one of: {', '.join(M2_MODES)}")

    client = session or requests.Session()
    selected_url = _chat_url(config.url_for_mode(mode))
    run_dir = Path(output_dir) if output_dir is not None else _default_output_dir(config, mode)
    run_dir.mkdir(parents=True, exist_ok=True)
    requests_path = run_dir / "requests.csv"
    telemetry_path = run_dir / "telemetry.csv"
    manifest_path = run_dir / "manifest.json"

    start_ts = now_func()
    manifest = build_manifest(
        config=config,
        mode=mode,
        selected_url=selected_url,
        output_dir=run_dir,
        started_ts=start_ts,
    )
    write_json(manifest_path, manifest)

    request_rows: list[M2RequestRow] = []
    telemetry_rows: list[M2TelemetryRow] = []
    telemetry_lock = threading.Lock()
    stop_event = threading.Event()
    safety_reason: list[str] = []
    selected_monitor = monitor or VcgencmdMonitor()

    _sample_and_store_telemetry(
        monitor=selected_monitor,
        path=telemetry_path,
        rows=telemetry_rows,
        lock=telemetry_lock,
        config=config,
        mode=mode,
        start_ts=start_ts,
        safety_reason=safety_reason,
        stop_event=stop_event,
    )

    telemetry_thread: threading.Thread | None = None
    if background_telemetry and not stop_event.is_set():
        telemetry_thread = threading.Thread(
            target=_telemetry_loop,
            kwargs={
                "monitor": selected_monitor,
                "path": telemetry_path,
                "rows": telemetry_rows,
                "lock": telemetry_lock,
                "config": config,
                "mode": mode,
                "start_ts": start_ts,
                "safety_reason": safety_reason,
                "stop_event": stop_event,
            },
            name="thermal-guardian-m2-telemetry",
            daemon=True,
        )
        telemetry_thread.start()

    loop_start = monotonic_func()
    deadline = loop_start + config.duration_sec
    sent = 0
    consecutive_failures = 0
    try:
        while config.request_count is None or sent < config.request_count:
            if stop_event.is_set():
                break
            if config.request_count is None and sent > 0 and monotonic_func() >= deadline:
                break

            # Open-loop pacing: dispatch request N at loop_start + N*interval,
            # independent of how fast the backend is. This holds the arrival rate
            # fixed, so a faster backend cannot silently do more work per minute
            # (the closed-loop coupling found in the look-ahead investigation).
            if config.arrival_interval_sec is not None:
                wait = (loop_start + sent * config.arrival_interval_sec) - monotonic_func()
                if wait > 0:
                    sleep_func(wait)
                if config.request_count is None and sent > 0 and monotonic_func() >= deadline:
                    break

            sent += 1
            prompt_id = f"{config.prompt_id_prefix}-{mode}-{sent:06d}"
            row = _send_one_request(
                client=client,
                url=selected_url,
                payload=build_chat_payload(config),
                prompt_id=prompt_id,
                config=config,
                mode=mode,
                now_func=now_func,
            )
            request_rows.append(row)
            _append_csv_rows(requests_path, REQUEST_FIELDS, [row.as_csv_row()])
            print(
                f"{prompt_id}: mode={mode} ok={row.ok} status={row.status_code or 'error'} "
                f"latency_ms={row.latency_ms:.3f} tokens_out={row.tokens_out}"
            )
            if row.ok:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= config.max_consecutive_failures and not safety_reason:
                    safety_reason.append(
                        "consecutive request failures "
                        f"{consecutive_failures} >= max_consecutive_failures "
                        f"{config.max_consecutive_failures}"
                    )
                    stop_event.set()

            if not background_telemetry:
                _sample_and_store_telemetry(
                    monitor=selected_monitor,
                    path=telemetry_path,
                    rows=telemetry_rows,
                    lock=telemetry_lock,
                    config=config,
                    mode=mode,
                    start_ts=start_ts,
                    safety_reason=safety_reason,
                    stop_event=stop_event,
                )
            if config.request_count is None and monotonic_func() >= deadline:
                break
            if config.interval_sec > 0:
                sleep_func(config.interval_sec)
    finally:
        stop_event.set()
        if telemetry_thread is not None:
            telemetry_thread.join(timeout=config.sampling_interval_sec + 1.0)

    with telemetry_lock:
        telemetry_count = len(telemetry_rows)
    manifest.update(
        {
            "finished_ts": now_func(),
            "request_count": len(request_rows),
            "ok_count": sum(1 for row in request_rows if row.ok),
            "failed_count": sum(1 for row in request_rows if not row.ok),
            "telemetry_count": telemetry_count,
            "safety_stop": bool(safety_reason),
            "safety_reason": safety_reason[0] if safety_reason else "",
        }
    )
    write_json(manifest_path, manifest)
    print(
        f"m2 run: mode={mode} output_dir={run_dir} requests={len(request_rows)} "
        f"telemetry={telemetry_count} safety_stop={str(bool(safety_reason)).lower()}"
    )
    return M2RunResult(run_dir, request_rows, telemetry_rows, manifest)


def build_manifest(
    *,
    config: M2Config,
    mode: str,
    selected_url: str,
    output_dir: Path,
    started_ts: float,
) -> dict[str, Any]:
    return {
        "schema": "thermal-guardian-m2-lite-v1",
        "started_ts": started_ts,
        "finished_ts": None,
        "mode": mode,
        "cooling": config.cooling,
        "selected_url": selected_url,
        "output_dir": str(output_dir),
        "config": asdict(config),
        "git_commit": _read_git_commit(),
        "os": platform.platform(),
        "kernel": platform.release(),
        "cpu_governor": _read_cpu_governor(),
        "power_meter_model": config.power_meter_model,
        "room_temp_c": config.room_temp_c,
        "request_count": 0,
        "ok_count": 0,
        "failed_count": 0,
        "telemetry_count": 0,
        "safety_stop": False,
        "safety_reason": "",
        "claim_note": "M2-lite records comparison inputs only; it is not a performance or J/token claim.",
    }


def summarize_runs(input_dirs: list[str | Path], *, output: str | Path) -> dict[str, Any]:
    run_summaries = [_summarize_one_run(Path(input_dir)) for input_dir in input_dirs]
    by_mode: dict[str, list[dict[str, Any]]] = {}
    for summary in run_summaries:
        by_mode.setdefault(str(summary.get("mode", "unknown")), []).append(summary)

    mode_summaries = {
        mode: {
            "run_count": len(rows),
            "median_latency_ms_median": _median_or_none(
                [row.get("median_latency_ms") for row in rows]
            ),
            "tokens_per_sec_median": _median_or_none(
                [row.get("median_tokens_per_sec") for row in rows]
            ),
            "max_temp_c": _max_or_none([row.get("max_temp_c") for row in rows]),
            "any_throttle_seen": any(bool(row.get("throttle_seen")) for row in rows),
            "any_safety_stop": any(bool(row.get("safety_stop")) for row in rows),
        }
        for mode, rows in sorted(by_mode.items())
    }
    summary = {
        "schema": "thermal-guardian-m2-summary-v1",
        "ok": all(bool(run.get("ok")) for run in run_summaries),
        "run_count": len(run_summaries),
        "runs": run_summaries,
        "by_mode": mode_summaries,
        "claim_note": "This summary is M2-lite evidence preparation, not a J/token or long-run stability claim.",
    }
    write_json(output, summary)
    print(f"m2 summarize: runs={len(run_summaries)} ok={str(summary['ok']).lower()} output={output}")
    return summary


def build_power_summary(
    input_dirs: list[str | Path],
    *,
    manual_power: str | Path,
    output: str | Path = DEFAULT_POWER_SUMMARY_OUTPUT,
) -> list[dict[str, str]]:
    manual_rows = _read_manual_power_rows(Path(manual_power))
    output_rows: list[dict[str, str]] = []

    for input_dir in input_dirs:
        run_dir = Path(input_dir)
        key = _power_row_key(run_dir)
        manual = manual_rows.get(key)
        if manual is None:
            raise ValueError(f"missing manual power row for run_dir: {run_dir}")

        run_summary = _summarize_one_run(run_dir)
        condition = str(run_summary.get("mode") or "")
        manual_condition = manual.get("condition", "")
        if manual_condition and condition and manual_condition != condition:
            raise ValueError(
                f"manual condition {manual_condition!r} does not match run mode {condition!r} "
                f"for run_dir: {run_dir}"
            )

        mwh = _parse_required_float(manual.get("mwh", ""), field="mwh", run_dir=run_dir)
        tokens_out_total = int(run_summary.get("tokens_total") or 0)
        j_per_token = None
        if tokens_out_total > 0:
            j_per_token = (mwh * 3.6) / tokens_out_total

        output_rows.append(
            {
                "condition": condition or manual_condition,
                "run_dir": str(run_dir),
                "requests": str(run_summary.get("request_count") or 0),
                "tokens_out_total": str(tokens_out_total),
                "median_latency_ms": _format_optional_float(
                    run_summary.get("median_latency_ms"), digits=3
                ),
                "iqr_latency_ms": _format_optional_float(
                    run_summary.get("iqr_latency_ms"), digits=3
                ),
                "median_tokens_per_sec": _format_optional_float(
                    run_summary.get("median_tokens_per_sec"), digits=6
                ),
                "iqr_tokens_per_sec": _format_optional_float(
                    run_summary.get("iqr_tokens_per_sec"), digits=6
                ),
                "max_temp_c": _format_optional_float(run_summary.get("max_temp_c"), digits=3),
                "throttle_seen": str(bool(run_summary.get("throttle_seen"))).lower(),
                "safety_stop": str(bool(run_summary.get("safety_stop"))).lower(),
                "mwh": _format_required_float(mwh),
                "j_per_token": _format_optional_float(j_per_token, digits=6),
                "note": manual.get("note", ""),
            }
        )

    _write_csv_rows(output, POWER_SUMMARY_FIELDS, output_rows)
    print(
        f"m2 power-summary: runs={len(output_rows)} manual_power={manual_power} output={output}"
    )
    return output_rows


def plot_run(*, input_dir: str | Path, output: str | Path = DEFAULT_PLOT_OUTPUT) -> Path:
    run_dir = Path(input_dir)
    requests = _read_request_rows(run_dir / "requests.csv")
    telemetry = _read_telemetry_rows(run_dir / "telemetry.csv")
    svg = _build_svg_plot(requests=requests, telemetry=telemetry, title=run_dir.name)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg, encoding="utf-8")
    print(f"m2 plot: output={output_path}")
    return output_path


def _send_one_request(
    *,
    client: requests.Session,
    url: str,
    payload: dict[str, Any],
    prompt_id: str,
    config: M2Config,
    mode: str,
    now_func: Callable[[], float],
) -> M2RequestRow:
    start = time.perf_counter()
    try:
        response = client.post(
            url,
            json=payload,
            headers={PROMPT_ID_HEADER: prompt_id},
            timeout=config.timeout_sec,
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        response_json = _response_json_or_empty(response)
        tokens_out = _extract_completion_tokens(response_json)
        response_model = response_json.get("model")
        detail = response.text.strip()[:200]
        return M2RequestRow(
            ts=now_func(),
            prompt_id=prompt_id,
            mode=mode,
            cooling=config.cooling,
            ok=response.status_code == 200,
            status_code=response.status_code,
            latency_ms=latency_ms,
            tokens_out=tokens_out,
            tokens_per_sec=_tokens_per_sec(tokens_out, latency_ms),
            model=response_model if isinstance(response_model, str) else "",
            url=url,
            detail=detail,
        )
    except requests.RequestException as exc:
        latency_ms = (time.perf_counter() - start) * 1000.0
        return M2RequestRow(
            ts=now_func(),
            prompt_id=prompt_id,
            mode=mode,
            cooling=config.cooling,
            ok=False,
            status_code=None,
            latency_ms=latency_ms,
            tokens_out=0,
            tokens_per_sec=0.0,
            model="",
            url=url,
            detail=str(exc)[:200],
        )


def _telemetry_loop(
    *,
    monitor: Any,
    path: Path,
    rows: list[M2TelemetryRow],
    lock: threading.Lock,
    config: M2Config,
    mode: str,
    start_ts: float,
    safety_reason: list[str],
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(config.sampling_interval_sec):
        _sample_and_store_telemetry(
            monitor=monitor,
            path=path,
            rows=rows,
            lock=lock,
            config=config,
            mode=mode,
            start_ts=start_ts,
            safety_reason=safety_reason,
            stop_event=stop_event,
        )


def _sample_and_store_telemetry(
    *,
    monitor: Any,
    path: Path,
    rows: list[M2TelemetryRow],
    lock: threading.Lock,
    config: M2Config,
    mode: str,
    start_ts: float,
    safety_reason: list[str],
    stop_event: threading.Event,
) -> M2TelemetryRow:
    snapshot: MonitorSnapshot = monitor.snapshot()
    row = M2TelemetryRow(
        ts=snapshot.ts,
        elapsed_sec=max(0.0, snapshot.ts - start_ts),
        mode=mode,
        cooling=config.cooling,
        temp_c=snapshot.temp_c,
        clock_hz=snapshot.clock_hz,
        throttled_hex=snapshot.throttled_hex,
    )
    with lock:
        rows.append(row)
        _append_csv_rows(path, TELEMETRY_FIELDS, [row.as_csv_row()])
    if row.temp_c >= config.safety_temp_c and not safety_reason:
        safety_reason.append(f"temp_c {row.temp_c:.1f} >= safety_temp_c {config.safety_temp_c:.1f}")
        stop_event.set()
    if config.stop_on_throttle and int(row.throttled_hex, 16) != 0 and not safety_reason:
        safety_reason.append(f"throttled_hex {row.throttled_hex} != 0x0")
        stop_event.set()
    return row


def _summarize_one_run(run_dir: Path) -> dict[str, Any]:
    requests = _read_request_rows(run_dir / "requests.csv")
    telemetry = _read_telemetry_rows(run_dir / "telemetry.csv")
    manifest = _read_json_or_empty(run_dir / "manifest.json")
    latency_values = [row["latency_ms"] for row in requests if row["ok"]]
    token_speed_values = [row["tokens_per_sec"] for row in requests if row["ok"]]
    max_temp_c = _max_or_none([row["temp_c"] for row in telemetry])
    throttle_seen = any(int(row["throttled_hex"], 16) != 0 for row in telemetry)
    safety_stop = bool(manifest.get("safety_stop"))
    return {
        "run_dir": str(run_dir),
        "mode": manifest.get("mode") or _first_string(requests, "mode") or "unknown",
        "cooling": manifest.get("cooling") or _first_string(requests, "cooling") or "",
        "ok": bool(requests) and all(row["ok"] for row in requests) and not safety_stop,
        "request_count": len(requests),
        "ok_count": sum(1 for row in requests if row["ok"]),
        "failed_count": sum(1 for row in requests if not row["ok"]),
        "tokens_total": sum(row["tokens_out"] for row in requests),
        "median_latency_ms": _median_or_none(latency_values),
        "iqr_latency_ms": _iqr_or_none(latency_values),
        "median_tokens_per_sec": _median_or_none(token_speed_values),
        "iqr_tokens_per_sec": _iqr_or_none(token_speed_values),
        "max_temp_c": max_temp_c,
        "throttle_seen": throttle_seen,
        "safety_stop": safety_stop,
        "safety_reason": manifest.get("safety_reason", ""),
    }


def _build_svg_plot(
    *,
    requests: list[dict[str, Any]],
    telemetry: list[dict[str, Any]],
    title: str,
) -> str:
    width = 960
    height = 720
    margin_left = 72
    margin_right = 24
    margin_top = 56
    panel_height = 170
    panel_gap = 36
    plot_width = width - margin_left - margin_right

    all_ts = [row["ts"] for row in requests] + [row["ts"] for row in telemetry]
    if not all_ts:
        all_ts = [0.0, 1.0]
    start_ts = min(all_ts)
    max_elapsed = max(1.0, max(ts - start_ts for ts in all_ts))

    temp_points = [(row["ts"] - start_ts, row["temp_c"]) for row in telemetry]
    clock_points = [(row["ts"] - start_ts, row["clock_hz"] / 1_000_000_000) for row in telemetry]
    token_points = [(row["ts"] - start_ts, row["tokens_per_sec"]) for row in requests]
    panels = [
        ("Temperature C", temp_points, "#d12f2f"),
        ("ARM clock GHz", clock_points, "#2f6fd1"),
        ("Tokens per sec", token_points, "#218b45"),
    ]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{margin_left}" y="30" font-family="sans-serif" font-size="20" fill="#222">M2-lite graph: {_xml_escape(title)}</text>',
    ]
    for index, (label, points, color) in enumerate(panels):
        y_top = margin_top + index * (panel_height + panel_gap)
        parts.extend(
            _svg_panel(
                label=label,
                points=points,
                color=color,
                x=margin_left,
                y=y_top,
                width=plot_width,
                height=panel_height,
                max_elapsed=max_elapsed,
            )
        )
    axis_y = margin_top + 3 * (panel_height + panel_gap) - panel_gap + 24
    parts.append(
        f'<text x="{margin_left + plot_width / 2:.1f}" y="{axis_y}" '
        'font-family="sans-serif" font-size="12" text-anchor="middle" fill="#555">elapsed seconds</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _svg_panel(
    *,
    label: str,
    points: list[tuple[float, float]],
    color: str,
    x: int,
    y: int,
    width: int,
    height: int,
    max_elapsed: float,
) -> list[str]:
    values = [value for _, value in points]
    min_value = min(values) if values else 0.0
    max_value = max(values) if values else 1.0
    if math.isclose(min_value, max_value):
        min_value -= 1.0
        max_value += 1.0

    def scale_x(elapsed: float) -> float:
        return x + (elapsed / max_elapsed) * width

    def scale_y(value: float) -> float:
        return y + height - ((value - min_value) / (max_value - min_value)) * height

    polyline = " ".join(f"{scale_x(elapsed):.1f},{scale_y(value):.1f}" for elapsed, value in points)
    parts = [
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" fill="#fafafa" stroke="#ccc"/>',
        f'<text x="{x - 10}" y="{y + 18}" font-family="sans-serif" font-size="12" text-anchor="end" fill="#333">{_xml_escape(label)}</text>',
        f'<text x="{x - 10}" y="{y + 12}" font-family="sans-serif" font-size="10" text-anchor="end" fill="#777">{max_value:.2f}</text>',
        f'<text x="{x - 10}" y="{y + height}" font-family="sans-serif" font-size="10" text-anchor="end" fill="#777">{min_value:.2f}</text>',
    ]
    if polyline:
        parts.append(
            f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="2"/>'
        )
        for elapsed, value in points:
            parts.append(
                f'<circle cx="{scale_x(elapsed):.1f}" cy="{scale_y(value):.1f}" r="2.5" fill="{color}"/>'
            )
    else:
        parts.append(
            f'<text x="{x + width / 2:.1f}" y="{y + height / 2:.1f}" '
            'font-family="sans-serif" font-size="12" text-anchor="middle" fill="#777">no data</text>'
        )
    parts.append(
        f'<text x="{x}" y="{y + height + 16}" font-family="sans-serif" font-size="10" fill="#777">0</text>'
    )
    parts.append(
        f'<text x="{x + width}" y="{y + height + 16}" font-family="sans-serif" font-size="10" text-anchor="end" fill="#777">{max_elapsed:.1f}</text>'
    )
    return parts


def _read_request_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as fp:
        for raw in csv.DictReader(fp):
            rows.append(
                {
                    "ts": float(raw.get("ts") or 0.0),
                    "prompt_id": raw.get("prompt_id") or "",
                    "mode": raw.get("mode") or "",
                    "cooling": raw.get("cooling") or "",
                    "ok": (raw.get("ok") or "").lower() == "true",
                    "status_code": int(raw["status_code"]) if raw.get("status_code") else None,
                    "latency_ms": float(raw.get("latency_ms") or 0.0),
                    "tokens_out": int(raw.get("tokens_out") or 0),
                    "tokens_per_sec": float(raw.get("tokens_per_sec") or 0.0),
                }
            )
    return rows


def _read_telemetry_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as fp:
        for raw in csv.DictReader(fp):
            rows.append(
                {
                    "ts": float(raw.get("ts") or 0.0),
                    "elapsed_sec": float(raw.get("elapsed_sec") or 0.0),
                    "mode": raw.get("mode") or "",
                    "cooling": raw.get("cooling") or "",
                    "temp_c": float(raw.get("temp_c") or 0.0),
                    "clock_hz": int(raw.get("clock_hz") or 0),
                    "throttled_hex": raw.get("throttled_hex") or "0x0",
                }
            )
    return rows


def _read_manual_power_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        raise ValueError(f"manual power file not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        fieldnames = reader.fieldnames or []
        missing = [field for field in MANUAL_POWER_FIELDS if field not in fieldnames]
        if missing:
            raise ValueError(f"manual power file missing columns: {', '.join(missing)}")
        rows: dict[str, dict[str, str]] = {}
        for raw in reader:
            run_dir = raw.get("run_dir", "")
            if not run_dir:
                raise ValueError("manual power row has empty run_dir")
            key = _power_row_key(Path(run_dir))
            if key in rows:
                raise ValueError(f"duplicate manual power row for run_dir: {run_dir}")
            rows[key] = {field: raw.get(field, "") for field in MANUAL_POWER_FIELDS}
    return rows


def _read_json_or_empty(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    return data if isinstance(data, dict) else {}


def _append_csv_rows(
    path: str | Path,
    fields: list[str],
    rows: list[dict[str, str]],
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_header = not output.exists() or output.stat().st_size == 0
    with output.open("a", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def _write_csv_rows(path: str | Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _response_json_or_empty(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_completion_tokens(data: dict[str, Any]) -> int:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return 0
    value = usage.get("completion_tokens")
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _tokens_per_sec(tokens_out: int, latency_ms: float) -> float:
    if tokens_out <= 0 or latency_ms <= 0:
        return 0.0
    return tokens_out / (latency_ms / 1000.0)


def _parse_required_float(value: str, *, field: str, run_dir: Path) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be numeric for run_dir: {run_dir}") from None
    if number < 0:
        raise ValueError(f"{field} must be non-negative for run_dir: {run_dir}")
    return number


def _format_required_float(value: float) -> str:
    return f"{value:g}"


def _format_optional_float(value: Any, *, digits: int) -> str:
    if value is None:
        return ""
    return f"{float(value):.{digits}f}"


def _power_row_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def _chat_url(base_url: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", CHAT_COMPLETIONS_PATH.lstrip("/"))


def _default_output_dir(config: M2Config, mode: str) -> Path:
    stamp = time.strftime("%Y-%m-%d/%H%M%S")
    return Path(config.output_root) / stamp / f"{mode}_{config.cooling}"


def _median_or_none(values: list[Any]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return None
    return float(statistics.median(numbers))


def _max_or_none(values: list[Any]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return None
    return max(numbers)


def _iqr_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return 0.0
    if len(values) == 2:
        return float(max(values) - min(values))
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return float(q3 - q1)


def _first_string(rows: list[dict[str, Any]], key: str) -> str | None:
    for row in rows:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _read_git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _read_cpu_governor() -> str:
    path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _with_cli_overrides(config: M2Config, args: argparse.Namespace) -> M2Config:
    overrides: dict[str, Any] = {}
    for name in (
        "duration_sec",
        "request_count",
        "interval_sec",
        "arrival_interval_sec",
        "timeout_sec",
        "max_consecutive_failures",
        "prompt",
        "max_tokens",
        "model",
        "sampling_interval_sec",
        "safety_temp_c",
        "stop_on_throttle",
        "cooling",
        "prompt_id_prefix",
    ):
        value = getattr(args, name, None)
        if value is not None:
            overrides[name] = value
    return replace(config, **overrides)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="M2-lite helpers for thermal-guardian.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run one fixed-workload M2-lite condition and record CSV evidence.",
    )
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--mode", choices=M2_MODES, required=True)
    run_parser.add_argument("--output-dir", default=None)
    run_parser.add_argument("--duration-sec", type=float, default=None)
    run_parser.add_argument("--request-count", type=int, default=None)
    run_parser.add_argument("--interval-sec", type=float, default=None)
    run_parser.add_argument("--arrival-interval-sec", type=float, default=None)
    run_parser.add_argument("--timeout-sec", type=float, default=None)
    run_parser.add_argument("--prompt", default=None)
    run_parser.add_argument("--max-tokens", type=int, default=None)
    run_parser.add_argument("--model", default=None)
    run_parser.add_argument("--sampling-interval-sec", type=float, default=None)
    run_parser.add_argument("--safety-temp-c", type=float, default=None)
    run_parser.add_argument("--stop-on-throttle", action="store_true", default=None)
    run_parser.add_argument("--cooling", choices=["fan_on", "fan_off"], default=None)
    run_parser.add_argument("--prompt-id-prefix", default=None)

    summarize_parser = subparsers.add_parser(
        "summarize",
        help="Summarize one or more M2-lite run directories.",
    )
    summarize_parser.add_argument("--input", action="append", required=True)
    summarize_parser.add_argument("--output", default=DEFAULT_SUMMARY_OUTPUT)

    plot_parser = subparsers.add_parser(
        "plot",
        help="Write an SVG graph for one M2-lite run directory.",
    )
    plot_parser.add_argument("--input", required=True)
    plot_parser.add_argument("--output", default=DEFAULT_PLOT_OUTPUT)

    power_parser = subparsers.add_parser(
        "power-summary",
        help="Join M2 run summaries with manual USB power-meter readings.",
    )
    power_parser.add_argument("--input", action="append", required=True)
    power_parser.add_argument("--manual-power", required=True)
    power_parser.add_argument("--output", default=DEFAULT_POWER_SUMMARY_OUTPUT)

    args = parser.parse_args(argv)

    if args.command == "run":
        result = run_m2(
            config=_with_cli_overrides(load_m2_config(args.config), args),
            mode=args.mode,
            output_dir=args.output_dir,
        )
        if not result.ok:
            raise SystemExit(1)
        return

    if args.command == "summarize":
        summary = summarize_runs(args.input, output=args.output)
        if not summary["ok"]:
            raise SystemExit(1)
        return

    if args.command == "plot":
        plot_run(input_dir=args.input, output=args.output)
        return

    if args.command == "power-summary":
        try:
            build_power_summary(
                args.input,
                manual_power=args.manual_power,
                output=args.output,
            )
        except ValueError as exc:
            raise SystemExit(f"power-summary: {exc}") from exc
        return

    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main(sys.argv[1:])
