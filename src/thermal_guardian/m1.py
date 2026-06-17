"""M1 helpers for load generation and switch-event acceptance checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin
import argparse
import csv
import json
import sys
import time
from typing import Any, Callable

import requests

from thermal_guardian.config import RouterConfig, load_config
from thermal_guardian.controller import RouteEvent
from thermal_guardian.logger import CsvLogger
from thermal_guardian.monitor import FakeMonitor, MonitorSnapshot
from thermal_guardian.router import CHAT_COMPLETIONS_PATH, PROMPT_ID_HEADER, RouterRuntime


LOAD_REQUEST_FIELDS = [
    "ts",
    "prompt_id",
    "ok",
    "status_code",
    "latency_ms",
    "tokens_out",
    "model",
    "detail",
]
DEFAULT_LOAD_OUTPUT = "logs/m1_load_requests.csv"
DEFAULT_SUMMARY_OUTPUT = "logs/m1_summary.json"
REAL_SWITCH_EVENTS = {RouteEvent.SWITCH_TO_Q4.value, RouteEvent.SWITCH_TO_Q8.value}


@dataclass(frozen=True)
class LoadRunRow:
    ts: float
    prompt_id: str
    ok: bool
    status_code: int | None
    latency_ms: float
    tokens_out: int
    model: str
    detail: str

    def as_csv_row(self) -> dict[str, str]:
        return {
            "ts": f"{self.ts:.6f}",
            "prompt_id": self.prompt_id,
            "ok": str(self.ok).lower(),
            "status_code": "" if self.status_code is None else str(self.status_code),
            "latency_ms": f"{self.latency_ms:.3f}",
            "tokens_out": str(self.tokens_out),
            "model": self.model,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class EventRow:
    ts: float
    temp_c: float
    clock_hz: int
    throttled_hex: str
    state: str
    event: str


def build_load_payload(
    *,
    model: str,
    prompt: str,
    max_tokens: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }


def run_load_run(
    *,
    router_url: str,
    output: str | Path = DEFAULT_LOAD_OUTPUT,
    duration_sec: float = 60.0,
    request_count: int | None = None,
    interval_sec: float = 0.0,
    timeout_sec: float = 120.0,
    prompt: str = "Reply with the single word ok.",
    max_tokens: int = 64,
    model: str = "thermal-guardian",
    prompt_id_prefix: str = "m1-load",
    session: requests.Session | None = None,
    now_func: Callable[[], float] = time.time,
    monotonic_func: Callable[[], float] = time.monotonic,
    sleep_func: Callable[[float], None] = time.sleep,
) -> list[LoadRunRow]:
    if duration_sec < 0:
        raise ValueError("duration_sec must be non-negative")
    if request_count is not None and request_count <= 0:
        raise ValueError("request_count must be positive when provided")
    if interval_sec < 0:
        raise ValueError("interval_sec must be non-negative")
    if timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive")
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")

    client = session or requests.Session()
    output_path = Path(output)
    url = urljoin(router_url.rstrip("/") + "/", CHAT_COMPLETIONS_PATH.lstrip("/"))
    deadline = monotonic_func() + duration_sec
    rows: list[LoadRunRow] = []
    sent = 0

    while request_count is None or sent < request_count:
        if request_count is None and sent > 0 and monotonic_func() >= deadline:
            break

        sent += 1
        prompt_id = f"{prompt_id_prefix}-{sent:06d}"
        payload = build_load_payload(model=model, prompt=prompt, max_tokens=max_tokens)
        start = time.perf_counter()
        try:
            response = client.post(
                url,
                json=payload,
                headers={PROMPT_ID_HEADER: prompt_id},
                timeout=timeout_sec,
            )
            latency_ms = (time.perf_counter() - start) * 1000.0
            response_json = _response_json_or_empty(response)
            tokens_out = _extract_completion_tokens(response_json)
            response_model = response_json.get("model")
            row = LoadRunRow(
                ts=now_func(),
                prompt_id=prompt_id,
                ok=response.status_code == 200,
                status_code=response.status_code,
                latency_ms=latency_ms,
                tokens_out=tokens_out,
                model=response_model if isinstance(response_model, str) else "",
                detail=response.text.strip()[:200],
            )
        except requests.RequestException as exc:
            row = LoadRunRow(
                ts=now_func(),
                prompt_id=prompt_id,
                ok=False,
                status_code=None,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                tokens_out=0,
                model="",
                detail=str(exc)[:200],
            )

        rows.append(row)
        append_load_rows(output_path, [row])
        print(
            f"{row.prompt_id}: ok={row.ok} status={row.status_code or 'error'} "
            f"latency_ms={row.latency_ms:.3f} tokens_out={row.tokens_out}"
        )

        if request_count is None and monotonic_func() >= deadline:
            break
        if interval_sec > 0:
            sleep_func(interval_sec)

    return rows


def append_load_rows(path: str | Path, rows: list[LoadRunRow]) -> None:
    _append_csv_rows(path, LOAD_REQUEST_FIELDS, [row.as_csv_row() for row in rows])


def read_event_rows(path: str | Path) -> list[EventRow]:
    rows: list[EventRow] = []
    with Path(path).open("r", encoding="utf-8", newline="") as fp:
        for raw in csv.DictReader(fp):
            rows.append(
                EventRow(
                    ts=float(raw.get("ts") or 0.0),
                    temp_c=float(raw.get("temp_c") or 0.0),
                    clock_hz=int(raw.get("clock_hz") or 0),
                    throttled_hex=raw.get("throttled_hex") or "0x0",
                    state=raw.get("state") or "",
                    event=raw.get("event") or "",
                )
            )
    return rows


def analyze_events(
    rows: list[EventRow],
    *,
    min_switch_interval_sec: float,
) -> dict[str, Any]:
    switch_rows = [row for row in rows if row.event in REAL_SWITCH_EVENTS]
    intervals = [
        max(0.0, later.ts - earlier.ts)
        for earlier, later in zip(switch_rows, switch_rows[1:])
    ]
    observed_min_interval = min(intervals) if intervals else None
    oscillation_detected = any(interval < min_switch_interval_sec for interval in intervals)
    failure_reasons: list[str] = []
    if not switch_rows:
        failure_reasons.append("no switch_to_q4 or switch_to_q8 events found")
    if oscillation_detected:
        failure_reasons.append("adjacent switch events are closer than min_switch_interval_sec")

    return {
        "ok": not failure_reasons,
        "total_events": len(rows),
        "switch_event_count": len(switch_rows),
        "switch_to_q4_count": sum(1 for row in rows if row.event == RouteEvent.SWITCH_TO_Q4.value),
        "switch_to_q8_count": sum(1 for row in rows if row.event == RouteEvent.SWITCH_TO_Q8.value),
        "cooldown_blocked_count": sum(
            1 for row in rows if row.event == RouteEvent.COOLDOWN_BLOCKED.value
        ),
        "required_min_switch_interval_sec": min_switch_interval_sec,
        "observed_min_switch_interval_sec": observed_min_interval,
        "oscillation_detected": oscillation_detected,
        "failure_reasons": failure_reasons,
    }


def run_analyze_events(
    *,
    events: str | Path,
    config: RouterConfig,
    output: str | Path = DEFAULT_SUMMARY_OUTPUT,
) -> dict[str, Any]:
    rows = read_event_rows(events)
    summary = analyze_events(rows, min_switch_interval_sec=config.min_switch_interval_sec)
    summary["events_path"] = str(events)
    summary["config_min_switch_interval_sec"] = config.min_switch_interval_sec
    write_json(output, summary)
    print(
        "m1 analyze-events: "
        f"ok={summary['ok']} switches={summary['switch_event_count']} "
        f"cooldown_blocked={summary['cooldown_blocked_count']} "
        f"oscillation={summary['oscillation_detected']}"
    )
    return summary


def parse_temperature_sequence(value: str) -> list[float]:
    temps = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not temps:
        raise ValueError("temperature sequence must contain at least one value")
    return temps


def build_fake_snapshots(
    temps: list[float],
    *,
    start_ts: float = 1.0,
    step_sec: float = 11.0,
    clock_hz: int = 1_500_000_000,
) -> list[MonitorSnapshot]:
    return [
        MonitorSnapshot(
            ts=start_ts + index * step_sec,
            temp_c=temp,
            clock_hz=clock_hz,
            throttled_hex="0x0",
        )
        for index, temp in enumerate(temps)
    ]


def run_fake_switch(
    *,
    config: RouterConfig,
    log_dir: str | Path,
    temps: list[float] | None = None,
) -> list[Any]:
    selected_temps = temps or [
        config.temp_down_c - 5.0,
        config.temp_up_c + 2.0,
        config.temp_up_c - 1.0,
        config.temp_down_c - 2.0,
    ]
    step_sec = max(config.min_switch_interval_sec + 1.0, 1.0)
    snapshots = build_fake_snapshots(selected_temps, step_sec=step_sec)
    runtime_config = RouterConfig.from_dict(
        {
            **config.__dict__,
            "dry_run": True,
            "log_dir": str(log_dir),
            "monitor_interval_sec": 1.0,
        }
    )
    runtime = RouterRuntime(
        runtime_config,
        monitor=FakeMonitor(snapshots),
        logger=CsvLogger(log_dir),
    )
    decisions = [runtime.sample_controller() for _ in snapshots]
    for decision in decisions:
        print(
            f"fake-switch: ts={decision.snapshot.ts:.3f} "
            f"temp_c={decision.snapshot.temp_c:.1f} state={decision.target.value} "
            f"event={decision.event.value}"
        )
    return decisions


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def _extract_completion_tokens(data: dict[str, Any]) -> int:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return 0
    value = usage.get("completion_tokens")
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _response_json_or_empty(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="M1 helpers for thermal-guardian.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    load_parser = subparsers.add_parser(
        "load-run",
        help="Send repeated OpenAI-compatible chat requests to the router.",
    )
    load_parser.add_argument("--router-url", required=True)
    load_parser.add_argument("--output", default=DEFAULT_LOAD_OUTPUT)
    load_parser.add_argument("--duration-sec", type=float, default=60.0)
    load_parser.add_argument("--request-count", type=int, default=None)
    load_parser.add_argument("--interval-sec", type=float, default=0.0)
    load_parser.add_argument("--timeout-sec", type=float, default=120.0)
    load_parser.add_argument("--prompt", default="Reply with the single word ok.")
    load_parser.add_argument("--max-tokens", type=int, default=64)
    load_parser.add_argument("--model", default="thermal-guardian")
    load_parser.add_argument("--prompt-id-prefix", default="m1-load")

    analyze_parser = subparsers.add_parser(
        "analyze-events",
        help="Check events.csv for M1 switch evidence and oscillation.",
    )
    analyze_parser.add_argument("--events", required=True)
    analyze_parser.add_argument("--config", required=True)
    analyze_parser.add_argument("--output", default=DEFAULT_SUMMARY_OUTPUT)

    fake_parser = subparsers.add_parser(
        "fake-switch",
        help="Generate switch events locally using FakeMonitor and dry-run router logic.",
    )
    fake_parser.add_argument("--config", required=True)
    fake_parser.add_argument("--log-dir", required=True)
    fake_parser.add_argument("--temps", default=None)

    args = parser.parse_args(argv)

    if args.command == "load-run":
        rows = run_load_run(
            router_url=args.router_url,
            output=args.output,
            duration_sec=args.duration_sec,
            request_count=args.request_count,
            interval_sec=args.interval_sec,
            timeout_sec=args.timeout_sec,
            prompt=args.prompt,
            max_tokens=args.max_tokens,
            model=args.model,
            prompt_id_prefix=args.prompt_id_prefix,
        )
        if not all(row.ok for row in rows):
            raise SystemExit(1)
        return

    if args.command == "analyze-events":
        summary = run_analyze_events(
            events=args.events,
            config=load_config(args.config),
            output=args.output,
        )
        if not summary["ok"]:
            raise SystemExit(1)
        return

    if args.command == "fake-switch":
        temps = parse_temperature_sequence(args.temps) if args.temps else None
        run_fake_switch(
            config=load_config(args.config),
            log_dir=args.log_dir,
            temps=temps,
        )
        return

    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main(sys.argv[1:])
