#!/usr/bin/env python3
"""Run the M3 thermal-continuity protocol on the Raspberry Pi.

This driver intentionally excludes power-meter handling. M3 asks whether the
controller can preserve service continuity under thermal pressure by stepping
down from Q8 to Q4 before throttling or a safety stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import argparse
import csv
import json
import re
import socket
import subprocess
import sys
import threading
import time
from typing import Any


@dataclass(frozen=True)
class RunSummary:
    label: str
    mode: str
    run_dir: Path
    request_count: int
    ok_count: int
    failed_count: int
    tokens_out_total: int
    start_temp_c: float | None
    peak_temp_c: float | None
    final_temp_c: float | None
    time_to_ceiling_sec: float | None
    time_to_throttle_sec: float | None
    seconds_above_ceiling: float
    seconds_throttled: float
    throttle_seen: bool
    first_throttled_hex: str | None
    final_throttled_hex: str | None
    safety_stop: bool
    safety_reason: str
    survived_full_window: bool
    q4_fraction: float | None
    switch_to_q4_count: int | None
    switch_to_q8_count: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "mode": self.mode,
            "run_dir": str(self.run_dir),
            "request_count": self.request_count,
            "ok_count": self.ok_count,
            "failed_count": self.failed_count,
            "tokens_out_total": self.tokens_out_total,
            "start_temp_c": self.start_temp_c,
            "peak_temp_c": self.peak_temp_c,
            "final_temp_c": self.final_temp_c,
            "time_to_ceiling_sec": self.time_to_ceiling_sec,
            "time_to_throttle_sec": self.time_to_throttle_sec,
            "seconds_above_ceiling": round(self.seconds_above_ceiling, 3),
            "seconds_throttled": round(self.seconds_throttled, 3),
            "throttle_seen": self.throttle_seen,
            "first_throttled_hex": self.first_throttled_hex,
            "final_throttled_hex": self.final_throttled_hex,
            "safety_stop": self.safety_stop,
            "safety_reason": self.safety_reason,
            "survived_full_window": self.survived_full_window,
            "q4_fraction": self.q4_fraction,
            "switch_to_q4_count": self.switch_to_q4_count,
            "switch_to_q8_count": self.switch_to_q8_count,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--m0-config", default="m0.local.json")
    parser.add_argument("--m2-config", default="m2.local.json")
    parser.add_argument("--router-base-config", default="config.example.json")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--arrival-interval-sec", type=float, default=4.0)
    parser.add_argument("--smoke-duration-sec", type=float, default=600.0)
    parser.add_argument("--duration-sec", type=float, default=1200.0)
    parser.add_argument("--safety-temp-c", type=float, default=82.0)
    parser.add_argument("--min-start-temp-c", type=float, default=None)
    parser.add_argument("--max-start-temp-c", type=float, default=50.0)
    parser.add_argument("--start-gate-poll-sec", type=float, default=10.0)
    parser.add_argument("--cooldown-timeout-sec", type=float, default=1800.0)
    parser.add_argument(
        "--pmic-log",
        action="store_true",
        help="Log vcgencmd pmic_read_adc samples to each run's pmic.csv. Never gates pass/fail.",
    )
    parser.add_argument("--pmic-interval-sec", type=float, default=2.0)
    parser.add_argument("--ceiling-c", type=float, default=None)
    parser.add_argument("--ceiling-margin-c", type=float, default=3.0)
    parser.add_argument("--min-ceiling-c", type=float, default=65.0)
    parser.add_argument("--max-ceiling-c", type=float, default=78.0)
    parser.add_argument("--temp-down-delta-c", type=float, default=4.0)
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument(
        "--fan-off-confirmed",
        action="store_true",
        help="Required: confirms the active fan is physically disconnected.",
    )
    parser.add_argument(
        "--stop-after-smoke",
        action="store_true",
        help="Only run the Q4 fan-off smoke and write the initial summary.",
    )
    args = parser.parse_args()

    if not args.fan_off_confirmed:
        raise SystemExit("Refusing to run fan-off M3 without --fan-off-confirmed.")
    if args.n <= 0:
        raise SystemExit("--n must be positive")
    if args.pmic_interval_sec <= 0:
        raise SystemExit("--pmic-interval-sec must be positive")
    if (
        args.min_start_temp_c is not None
        and args.min_start_temp_c > args.max_start_temp_c
    ):
        raise SystemExit("--min-start-temp-c must be <= --max-start-temp-c")
    if args.start_gate_poll_sec <= 0:
        raise SystemExit("--start-gate-poll-sec must be positive")

    output_root = Path(args.output_root or f"data/m2/{args.date}/m3_thermal_continuity")
    output_root.mkdir(parents=True, exist_ok=True)

    _ensure_backends(args.m0_config)

    summaries: list[RunSummary] = []
    q4_smoke = _run_m2_condition(
        label="step0_q4_smoke_001",
        mode="q4_fixed",
        output_root=output_root,
        m2_config=args.m2_config,
        duration_sec=args.smoke_duration_sec,
        arrival_interval_sec=args.arrival_interval_sec,
        safety_temp_c=args.safety_temp_c,
        min_start_temp_c=args.min_start_temp_c,
        max_start_temp_c=args.max_start_temp_c,
        start_gate_poll_sec=args.start_gate_poll_sec,
        cooldown_timeout_sec=args.cooldown_timeout_sec,
        pmic_log=args.pmic_log,
        pmic_interval_sec=args.pmic_interval_sec,
        prompt_id_prefix="m3-smoke",
    )
    q4_smoke_summary = summarize_run(
        label="step0_q4_smoke_001",
        run_dir=q4_smoke,
        ceiling_c=args.ceiling_c or args.min_ceiling_c,
        expected_duration_sec=args.smoke_duration_sec,
    )
    summaries.append(q4_smoke_summary)

    if _q4_smoke_failed(q4_smoke_summary):
        protocol = _build_protocol(
            args=args,
            output_root=output_root,
            status="q4_smoke_failed",
            ceiling_c=None,
            temp_down_c=None,
        )
        protocol["stop_reason"] = _terminal_reason(
            q4_smoke_summary,
            current_throttled=_get_throttled(),
        )
        _write_terminal_marker(output_root, protocol["stop_reason"])
        _write_json(output_root / "m3_protocol.json", protocol)
        _write_summary(output_root, protocol, summaries)
        print(json.dumps(protocol, indent=2))
        print(f"summary_json={output_root / 'm3_summary.json'}")
        return

    try:
        ceiling_c = _choose_ceiling(args=args, q4_smoke_summary=q4_smoke_summary)
    except SystemExit as exc:
        protocol = _build_protocol(
            args=args,
            output_root=output_root,
            status="q4_ceiling_invalid",
            ceiling_c=None,
            temp_down_c=None,
        )
        protocol["stop_reason"] = str(exc)
        _write_terminal_marker(output_root, protocol["stop_reason"])
        _write_json(output_root / "m3_protocol.json", protocol)
        _write_summary(output_root, protocol, summaries)
        print(json.dumps(protocol, indent=2))
        print(f"summary_json={output_root / 'm3_summary.json'}")
        return
    temp_down_c = ceiling_c - args.temp_down_delta_c
    if temp_down_c >= ceiling_c:
        raise SystemExit("computed temp_down_c must be below ceiling")

    protocol = _build_protocol(
        args=args,
        output_root=output_root,
        status="smoke_only" if args.stop_after_smoke else "running",
        ceiling_c=ceiling_c,
        temp_down_c=temp_down_c,
    )
    _write_json(output_root / "m3_protocol.json", protocol)
    _write_summary(output_root, protocol, summaries)

    if args.stop_after_smoke:
        print(json.dumps(protocol, indent=2))
        print(f"summary_json={output_root / 'm3_summary.json'}")
        return

    all_run_specs: list[tuple[str, str]] = []
    for index in range(1, args.n + 1):
        all_run_specs.append(("q8_fixed", f"q8_fixed_{index:03d}"))
        all_run_specs.append(("controller", f"controller_{index:03d}"))
        all_run_specs.append(("q4_fixed", f"q4_fixed_{index:03d}"))

    for mode, label in all_run_specs:
        if label == "q4_fixed_001" and q4_smoke_summary.survived_full_window:
            # Keep the Step 0 smoke separate from the fixed-Q4 reference because
            # it may use a shorter duration; do not silently reuse it.
            pass
        run_was_preexisting = _is_complete_run(output_root / label)
        if mode == "controller":
            run_dir = _run_controller_condition(
                label=label,
                output_root=output_root,
                router_base_config=args.router_base_config,
                m2_config=args.m2_config,
                duration_sec=args.duration_sec,
                arrival_interval_sec=args.arrival_interval_sec,
                safety_temp_c=args.safety_temp_c,
                min_start_temp_c=args.min_start_temp_c,
                max_start_temp_c=args.max_start_temp_c,
                start_gate_poll_sec=args.start_gate_poll_sec,
                cooldown_timeout_sec=args.cooldown_timeout_sec,
                pmic_log=args.pmic_log,
                pmic_interval_sec=args.pmic_interval_sec,
                ceiling_c=ceiling_c,
                temp_down_c=temp_down_c,
            )
        else:
            run_dir = _run_m2_condition(
                label=label,
                mode=mode,
                output_root=output_root,
                m2_config=args.m2_config,
                duration_sec=args.duration_sec,
                arrival_interval_sec=args.arrival_interval_sec,
                safety_temp_c=args.safety_temp_c,
                min_start_temp_c=args.min_start_temp_c,
                max_start_temp_c=args.max_start_temp_c,
                start_gate_poll_sec=args.start_gate_poll_sec,
                cooldown_timeout_sec=args.cooldown_timeout_sec,
                pmic_log=args.pmic_log,
                pmic_interval_sec=args.pmic_interval_sec,
                prompt_id_prefix="m3",
            )
        summaries.append(
            summarize_run(
                label=label,
                run_dir=run_dir,
                ceiling_c=ceiling_c,
                expected_duration_sec=args.duration_sec,
            )
        )
        _write_summary(output_root, protocol, summaries)
        current_throttled = _get_throttled()
        if _must_pause_after_run(
            summaries[-1],
            current_throttled=current_throttled,
            run_was_preexisting=run_was_preexisting,
        ):
            protocol["status"] = f"manual_pause_after_{label}"
            protocol["stop_reason"] = _terminal_reason(
                summaries[-1],
                current_throttled=current_throttled,
            )
            _write_terminal_marker(output_root, protocol["stop_reason"])
            _write_json(output_root / "m3_protocol.json", protocol)
            _write_summary(output_root, protocol, summaries)
            print(json.dumps(protocol, indent=2))
            print(f"summary_json={output_root / 'm3_summary.json'}")
            return

    protocol["status"] = "complete"
    _write_json(output_root / "m3_protocol.json", protocol)
    _write_summary(output_root, protocol, summaries)
    print(json.dumps(_build_summary(protocol, summaries), indent=2))
    print(f"summary_json={output_root / 'm3_summary.json'}")
    print(f"summary_csv={output_root / 'm3_summary.csv'}")


def _ensure_backends(m0_config: str) -> None:
    check = [sys.executable, "-m", "thermal_guardian.m0", "check", "--config", m0_config]
    if subprocess.run(check, check=False).returncode == 0:
        return
    subprocess.run(
        [sys.executable, "-m", "thermal_guardian.m0", "start", "--config", m0_config],
        check=True,
    )
    deadline = time.time() + 180
    while time.time() < deadline:
        if subprocess.run(check, check=False).returncode == 0:
            return
        time.sleep(5)
    raise SystemExit("q8/q4 backends did not become healthy")


def _build_protocol(
    *,
    args: argparse.Namespace,
    output_root: Path,
    status: str,
    ceiling_c: float | None,
    temp_down_c: float | None,
) -> dict[str, Any]:
    return {
        "schema": "thermal-guardian-m3-protocol-v1",
        "status": status,
        "output_root": str(output_root),
        "arrival_interval_sec": args.arrival_interval_sec,
        "smoke_duration_sec": args.smoke_duration_sec,
        "duration_sec": args.duration_sec,
        "safety_temp_c": args.safety_temp_c,
        "min_start_temp_c": args.min_start_temp_c,
        "max_start_temp_c": args.max_start_temp_c,
        "start_gate_poll_sec": args.start_gate_poll_sec,
        "ceiling_c": ceiling_c,
        "temp_down_c": temp_down_c,
        "fan_off_confirmed": args.fan_off_confirmed,
        "power_logging": "pmic_read_adc" if args.pmic_log else "not_used",
        "pmic_interval_sec": args.pmic_interval_sec if args.pmic_log else None,
        "claim_note": (
            "M3 tests thermal continuity under fan-off stress. It does not claim "
            "hardware wear reduction, lifespan improvement, or energy efficiency."
        ),
    }


def _is_complete_run(run_dir: Path) -> bool:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = _read_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if manifest.get("finished_ts") is None:
        return False
    if not (run_dir / "telemetry.csv").exists():
        return False
    if not manifest.get("safety_stop") and not (run_dir / "requests.csv").exists():
        return False
    if manifest.get("mode") == "controller" and not (
        run_dir / "router_logs" / "events.csv"
    ).exists():
        return False
    return True


def _reject_incomplete_run(run_dir: Path) -> None:
    if not run_dir.exists():
        return
    try:
        has_files = any(run_dir.iterdir())
    except OSError:
        has_files = True
    if has_files:
        raise SystemExit(
            f"incomplete existing run directory: {run_dir}. "
            "Use a fresh --output-root or inspect/rename the partial run before resuming."
        )


def _run_m2_condition(
    *,
    label: str,
    mode: str,
    output_root: Path,
    m2_config: str,
    duration_sec: float,
    arrival_interval_sec: float,
    safety_temp_c: float,
    min_start_temp_c: float | None,
    max_start_temp_c: float,
    start_gate_poll_sec: float,
    cooldown_timeout_sec: float,
    pmic_log: bool,
    pmic_interval_sec: float,
    prompt_id_prefix: str,
) -> Path:
    run_dir = output_root / label
    if _is_complete_run(run_dir):
        print(f"skip existing run: {run_dir}")
        return run_dir
    _reject_incomplete_run(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    _wait_for_start_gate(
        run_dir=run_dir,
        min_start_temp_c=min_start_temp_c,
        max_start_temp_c=max_start_temp_c,
        start_gate_poll_sec=start_gate_poll_sec,
        cooldown_timeout_sec=cooldown_timeout_sec,
    )
    with (run_dir / "m2_run.stdout.log").open("w", encoding="utf-8") as stdout:
        command = [
            sys.executable,
            "-m",
            "thermal_guardian.m2",
            "run",
            "--config",
            m2_config,
            "--mode",
            mode,
            "--output-dir",
            str(run_dir),
            "--duration-sec",
            str(duration_sec),
            "--arrival-interval-sec",
            str(arrival_interval_sec),
            "--cooling",
            "fan_off",
            "--safety-temp-c",
            str(safety_temp_c),
            "--stop-on-throttle",
            "--prompt-id-prefix",
            prompt_id_prefix,
        ]
        result = _run_with_optional_pmic_log(
            command=command,
            stdout=stdout,
            run_dir=run_dir,
            label=label,
            enabled=pmic_log,
            interval_sec=pmic_interval_sec,
        )
    if result.returncode != 0 and not _is_complete_run(run_dir):
        raise subprocess.CalledProcessError(result.returncode, command)
    return run_dir


def _run_controller_condition(
    *,
    label: str,
    output_root: Path,
    router_base_config: str,
    m2_config: str,
    duration_sec: float,
    arrival_interval_sec: float,
    safety_temp_c: float,
    min_start_temp_c: float | None,
    max_start_temp_c: float,
    start_gate_poll_sec: float,
    cooldown_timeout_sec: float,
    pmic_log: bool,
    pmic_interval_sec: float,
    ceiling_c: float,
    temp_down_c: float,
) -> Path:
    run_dir = output_root / label
    if _is_complete_run(run_dir):
        print(f"skip existing run: {run_dir}")
        return run_dir
    _reject_incomplete_run(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    _wait_for_start_gate(
        run_dir=run_dir,
        min_start_temp_c=min_start_temp_c,
        max_start_temp_c=max_start_temp_c,
        start_gate_poll_sec=start_gate_poll_sec,
        cooldown_timeout_sec=cooldown_timeout_sec,
    )
    router_config = _write_router_config(
        run_dir=run_dir,
        router_base_config=router_base_config,
        ceiling_c=ceiling_c,
        temp_down_c=temp_down_c,
    )
    _stop_router()
    router_stdout = (run_dir / "router.stdout.log").open("w", encoding="utf-8")
    router = subprocess.Popen(
        [sys.executable, "-m", "thermal_guardian.router", "--config", str(router_config)],
        stdout=router_stdout,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_port("127.0.0.1", 8080)
        with (run_dir / "m2_run.stdout.log").open("w", encoding="utf-8") as stdout:
            command = [
                sys.executable,
                "-m",
                "thermal_guardian.m2",
                "run",
                "--config",
                m2_config,
                "--mode",
                "controller",
                "--output-dir",
                str(run_dir),
                "--duration-sec",
                str(duration_sec),
                "--arrival-interval-sec",
                str(arrival_interval_sec),
                "--cooling",
                "fan_off",
                "--safety-temp-c",
                str(safety_temp_c),
                "--stop-on-throttle",
                "--prompt-id-prefix",
                "m3",
            ]
            result = _run_with_optional_pmic_log(
                command=command,
                stdout=stdout,
                run_dir=run_dir,
                label=label,
                enabled=pmic_log,
                interval_sec=pmic_interval_sec,
            )
        if result.returncode != 0 and not _is_complete_run(run_dir):
            raise subprocess.CalledProcessError(result.returncode, command)
    finally:
        router.terminate()
        try:
            router.wait(timeout=10)
        except subprocess.TimeoutExpired:
            router.kill()
            router.wait(timeout=5)
        router_stdout.close()
    return run_dir


def _q4_smoke_failed(summary: RunSummary) -> bool:
    return summary.safety_stop or summary.throttle_seen or not summary.survived_full_window


def _must_pause_after_run(
    summary: RunSummary,
    *,
    current_throttled: str,
    run_was_preexisting: bool,
) -> bool:
    if current_throttled != "0x0":
        return True
    if run_was_preexisting:
        return False
    return summary.safety_stop or summary.throttle_seen


def _terminal_reason(summary: RunSummary, *, current_throttled: str) -> str:
    parts = [
        f"label={summary.label}",
        f"mode={summary.mode}",
        f"safety_stop={str(summary.safety_stop).lower()}",
        f"safety_reason={summary.safety_reason or 'none'}",
        f"throttle_seen={str(summary.throttle_seen).lower()}",
        f"first_throttled_hex={summary.first_throttled_hex or 'none'}",
        f"final_throttled_hex={summary.final_throttled_hex or 'none'}",
        f"current_get_throttled={current_throttled}",
        f"survived_full_window={str(summary.survived_full_window).lower()}",
    ]
    if current_throttled != "0x0":
        parts.append(
            "next_step=reboot Pi before the next arm because get_throttled sticky bits "
            "can remain set until reboot"
        )
    else:
        parts.append("next_step=manual review before continuing")
    return "; ".join(parts)


def _write_terminal_marker(output_root: Path, reason: str) -> None:
    (output_root / "manual_pause_required.txt").write_text(reason + "\n", encoding="utf-8")


def _write_router_config(
    *,
    run_dir: Path,
    router_base_config: str,
    ceiling_c: float,
    temp_down_c: float,
) -> Path:
    base = json.loads(Path(router_base_config).read_text(encoding="utf-8"))
    if not isinstance(base, dict):
        raise SystemExit(f"router base config must be a JSON object: {router_base_config}")
    base.update(
        {
            "temp_up_c": ceiling_c,
            "temp_down_c": temp_down_c,
            "look_ahead_sec": 0.0,
            "min_residence_sec": 0.0,
            "log_dir": str(run_dir / "router_logs"),
            "dry_run": False,
        }
    )
    path = run_dir / "router.local.json"
    path.write_text(json.dumps(base, indent=2) + "\n", encoding="utf-8")
    return path


PMIC_FIELDS = ["ts", "elapsed_sec", "label", "rail", "value", "unit", "raw_line"]


def _run_with_optional_pmic_log(
    *,
    command: list[str],
    stdout: Any,
    run_dir: Path,
    label: str,
    enabled: bool,
    interval_sec: float,
) -> subprocess.CompletedProcess[str]:
    if not enabled:
        return subprocess.run(command, stdout=stdout, stderr=subprocess.STDOUT)

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_pmic_loop,
        kwargs={
            "path": run_dir / "pmic.csv",
            "error_path": run_dir / "pmic_error.txt",
            "label": label,
            "interval_sec": interval_sec,
            "stop_event": stop_event,
        },
        name=f"thermal-guardian-m3-pmic-{label}",
        daemon=True,
    )
    thread.start()
    try:
        return subprocess.run(command, stdout=stdout, stderr=subprocess.STDOUT)
    finally:
        stop_event.set()
        thread.join(timeout=interval_sec + 1.0)


def _pmic_loop(
    *,
    path: Path,
    error_path: Path,
    label: str,
    interval_sec: float,
    stop_event: threading.Event,
) -> None:
    start_ts = time.time()
    while not stop_event.is_set():
        try:
            rows = _read_pmic_rows(label=label, start_ts=start_ts)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            error_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            return
        _append_csv_rows(path, PMIC_FIELDS, rows)
        stop_event.wait(interval_sec)


def _append_csv_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def _read_pmic_rows(*, label: str, start_ts: float) -> list[dict[str, str]]:
    ts = time.time()
    completed = subprocess.run(
        ["vcgencmd", "pmic_read_adc"],
        check=False,
        capture_output=True,
        text=True,
        timeout=2.0,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"vcgencmd pmic_read_adc failed: {detail}")

    rows: list[dict[str, str]] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2 or "=" not in parts[-1]:
            continue
        rail = parts[0]
        value_with_unit = parts[-1].split("=", 1)[1]
        value_text = ""
        unit = ""
        for char in value_with_unit:
            if char.isdigit() or char in ".-+":
                value_text += char
            else:
                unit += char
        if not value_text or not unit:
            continue
        rows.append(
            {
                "ts": f"{ts:.6f}",
                "elapsed_sec": f"{max(0.0, ts - start_ts):.3f}",
                "label": label,
                "rail": rail,
                "value": f"{float(value_text):.6f}",
                "unit": unit,
                "raw_line": line,
            }
        )
    return rows


def _wait_for_start_gate(
    *,
    run_dir: Path,
    min_start_temp_c: float | None,
    max_start_temp_c: float,
    start_gate_poll_sec: float,
    cooldown_timeout_sec: float,
) -> None:
    deadline = time.time() + cooldown_timeout_sec
    log_path = run_dir / "preflight.jsonl"
    while True:
        temp_c = _measure_temp_c()
        throttled = _get_throttled()
        sample = {
            "ts": time.time(),
            "temp_c": temp_c,
            "throttled_hex": throttled,
            "min_start_temp_c": min_start_temp_c,
            "max_start_temp_c": max_start_temp_c,
            "ok": (
                (min_start_temp_c is None or temp_c >= min_start_temp_c)
                and temp_c <= max_start_temp_c
                and throttled == "0x0"
            ),
        }
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(sample) + "\n")
        if sample["ok"]:
            (run_dir / "pre_temp.txt").write_text(f"temp={temp_c:.1f}'C\n", encoding="utf-8")
            (run_dir / "pre_throttled.txt").write_text(
                f"throttled={throttled}\n",
                encoding="utf-8",
            )
            return
        if time.time() >= deadline:
            if min_start_temp_c is None:
                temp_requirement = f"temp<={max_start_temp_c:.1f}C"
            else:
                temp_requirement = (
                    f"{min_start_temp_c:.1f}C<=temp<={max_start_temp_c:.1f}C"
                )
            raise SystemExit(
                f"start gate timed out for {run_dir}: temp={temp_c:.1f}C "
                f"throttled={throttled}, required {temp_requirement} "
                "and throttled=0x0"
            )
        if min_start_temp_c is None:
            temp_target = f"target<={max_start_temp_c:.1f}C"
        else:
            temp_target = f"target={min_start_temp_c:.1f}-{max_start_temp_c:.1f}C"
        print(
            f"waiting for start gate: temp={temp_c:.1f}C "
            f"throttled={throttled} {temp_target}",
            flush=True,
        )
        time.sleep(start_gate_poll_sec)


def summarize_run(
    *,
    label: str,
    run_dir: Path,
    ceiling_c: float,
    expected_duration_sec: float,
) -> RunSummary:
    manifest = _read_json(run_dir / "manifest.json")
    telemetry = _read_csv(run_dir / "telemetry.csv")
    requests = _read_csv(run_dir / "requests.csv")
    events = _read_csv(run_dir / "router_logs" / "events.csv")

    temps = [_float(row["temp_c"]) for row in telemetry if row.get("temp_c")]
    elapsed = [_float(row["elapsed_sec"]) for row in telemetry if row.get("elapsed_sec")]
    throttled_flags = [
        int(row["throttled_hex"], 16) != 0
        for row in telemetry
        if row.get("throttled_hex")
    ]
    throttled_values = [row["throttled_hex"] for row in telemetry if row.get("throttled_hex")]
    first_throttled_hex = next(
        (value for value in throttled_values if int(value, 16) != 0),
        None,
    )
    final_throttled_hex = throttled_values[-1] if throttled_values else None

    time_to_ceiling = _first_elapsed_at_or_above(telemetry, ceiling_c)
    time_to_throttle = _first_elapsed_throttled(telemetry)
    seconds_above_ceiling = _seconds_matching(telemetry, lambda row: _float(row["temp_c"]) >= ceiling_c)
    seconds_throttled = _seconds_matching(
        telemetry,
        lambda row: int(row["throttled_hex"], 16) != 0,
    )
    ok_count = sum(1 for row in requests if row.get("ok") == "true")
    failed_count = sum(1 for row in requests if row.get("ok") != "true")
    tokens_out_total = sum(int(row.get("tokens_out") or 0) for row in requests)
    safety_stop = bool(manifest.get("safety_stop"))
    survived_full_window = (
        not safety_stop
        and bool(elapsed)
        and max(elapsed) >= max(0.0, expected_duration_sec - 2.5)
    )
    q4_fraction = None
    switch_to_q4_count = None
    switch_to_q8_count = None
    if events and manifest.get("mode") == "controller":
        started_ts = _float(manifest["started_ts"])
        finished_ts = _float(manifest["finished_ts"])
        q4_time, q8_time = _state_times(events, started_ts=started_ts, finished_ts=finished_ts)
        duration = max(0.0, finished_ts - started_ts)
        q4_fraction = q4_time / duration if duration else 0.0
        events_in_window = [
            row for row in events if started_ts <= _float(row["ts"]) <= finished_ts
        ]
        switch_to_q4_count = sum(1 for row in events_in_window if row.get("event") == "switch_to_q4")
        switch_to_q8_count = sum(1 for row in events_in_window if row.get("event") == "switch_to_q8")

    return RunSummary(
        label=label,
        mode=str(manifest.get("mode") or ""),
        run_dir=run_dir,
        request_count=len(requests),
        ok_count=ok_count,
        failed_count=failed_count,
        tokens_out_total=tokens_out_total,
        start_temp_c=temps[0] if temps else None,
        peak_temp_c=max(temps) if temps else None,
        final_temp_c=temps[-1] if temps else None,
        time_to_ceiling_sec=time_to_ceiling,
        time_to_throttle_sec=time_to_throttle,
        seconds_above_ceiling=seconds_above_ceiling,
        seconds_throttled=seconds_throttled,
        throttle_seen=any(throttled_flags),
        first_throttled_hex=first_throttled_hex,
        final_throttled_hex=final_throttled_hex,
        safety_stop=safety_stop,
        safety_reason=str(manifest.get("safety_reason") or ""),
        survived_full_window=survived_full_window,
        q4_fraction=q4_fraction,
        switch_to_q4_count=switch_to_q4_count,
        switch_to_q8_count=switch_to_q8_count,
    )


def _choose_ceiling(*, args: argparse.Namespace, q4_smoke_summary: RunSummary) -> float:
    if args.ceiling_c is not None:
        return args.ceiling_c
    if q4_smoke_summary.peak_temp_c is None:
        raise SystemExit("cannot choose ceiling: q4 smoke has no telemetry")
    ceiling = max(args.min_ceiling_c, q4_smoke_summary.peak_temp_c + args.ceiling_margin_c)
    ceiling = min(ceiling, args.max_ceiling_c)
    if ceiling <= q4_smoke_summary.peak_temp_c:
        raise SystemExit(
            "cannot choose a valid ceiling above the Q4 fan-off smoke peak "
            f"({q4_smoke_summary.peak_temp_c:.1f}C)"
        )
    return round(ceiling, 1)


def _build_summary(protocol: dict[str, Any], runs: list[RunSummary]) -> dict[str, Any]:
    return {
        "schema": "thermal-guardian-m3-summary-v1",
        "protocol": protocol,
        "runs": [run.as_dict() for run in runs],
        "claim_note": (
            "This summary supports a thermal-continuity claim only: whether the "
            "controller avoided throttle/safety-stop and kept serving under matched "
            "fan-off open-loop demand. It excludes power and hardware-wear claims."
        ),
    }


def _write_summary(output_root: Path, protocol: dict[str, Any], runs: list[RunSummary]) -> None:
    summary = _build_summary(protocol, runs)
    _write_json(output_root / "m3_summary.json", summary)
    fields = list(runs[0].as_dict()) if runs else []
    if fields:
        with (output_root / "m3_summary.csv").open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=fields)
            writer.writeheader()
            for run in runs:
                writer.writerow(run.as_dict())


def _state_times(
    events: list[dict[str, str]],
    *,
    started_ts: float,
    finished_ts: float,
) -> tuple[float, float]:
    in_window = sorted(
        (row for row in events if _float(row["ts"]) <= finished_ts),
        key=lambda row: _float(row["ts"]),
    )
    if not in_window:
        return 0.0, max(0.0, finished_ts - started_ts)
    current = "q8"
    prior_ts = started_ts
    q4_time = 0.0
    q8_time = 0.0
    for row in in_window:
        ts = min(max(_float(row["ts"]), started_ts), finished_ts)
        if ts > prior_ts:
            if current == "q4":
                q4_time += ts - prior_ts
            else:
                q8_time += ts - prior_ts
        state = row.get("state")
        if state in {"q4", "q8"}:
            current = state
        prior_ts = ts
    if finished_ts > prior_ts:
        if current == "q4":
            q4_time += finished_ts - prior_ts
        else:
            q8_time += finished_ts - prior_ts
    return q4_time, q8_time


def _seconds_matching(
    telemetry: list[dict[str, str]],
    predicate: Any,
) -> float:
    if len(telemetry) < 2:
        return 0.0
    total = 0.0
    rows = sorted(telemetry, key=lambda row: _float(row["elapsed_sec"]))
    for prev, current in zip(rows, rows[1:]):
        if predicate(prev):
            total += max(0.0, _float(current["elapsed_sec"]) - _float(prev["elapsed_sec"]))
    return total


def _first_elapsed_at_or_above(
    telemetry: list[dict[str, str]],
    threshold_c: float,
) -> float | None:
    for row in telemetry:
        if _float(row["temp_c"]) >= threshold_c:
            return _float(row["elapsed_sec"])
    return None


def _first_elapsed_throttled(telemetry: list[dict[str, str]]) -> float | None:
    for row in telemetry:
        if int(row["throttled_hex"], 16) != 0:
            return _float(row["elapsed_sec"])
    return None


def _measure_temp_c() -> float:
    result = subprocess.run(
        ["vcgencmd", "measure_temp"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    match = re.search(r"temp=([0-9.]+)'C", result.stdout)
    if not match:
        raise SystemExit(f"could not parse vcgencmd measure_temp: {result.stdout!r}")
    return float(match.group(1))


def _get_throttled() -> str:
    result = subprocess.run(
        ["vcgencmd", "get_throttled"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip().split("=", 1)[-1]


def _wait_for_port(host: str, port: int) -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.5)
    raise SystemExit(f"router did not listen on {host}:{port}")


def _stop_router() -> None:
    subprocess.run(["pkill", "-f", "thermal_guardian.router"], check=False)
    time.sleep(1)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fp:
        return list(csv.DictReader(fp))


def _float(value: str | float | int) -> float:
    return float(value)


if __name__ == "__main__":
    main()
