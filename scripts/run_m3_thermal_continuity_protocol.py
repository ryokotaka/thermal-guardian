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
    parser.add_argument("--max-start-temp-c", type=float, default=50.0)
    parser.add_argument("--cooldown-timeout-sec", type=float, default=1800.0)
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
        max_start_temp_c=args.max_start_temp_c,
        cooldown_timeout_sec=args.cooldown_timeout_sec,
        prompt_id_prefix="m3-smoke",
    )
    q4_smoke_summary = summarize_run(
        label="step0_q4_smoke_001",
        run_dir=q4_smoke,
        ceiling_c=args.ceiling_c or args.min_ceiling_c,
        expected_duration_sec=args.smoke_duration_sec,
    )
    summaries.append(q4_smoke_summary)

    ceiling_c = _choose_ceiling(args=args, q4_smoke_summary=q4_smoke_summary)
    temp_down_c = ceiling_c - args.temp_down_delta_c
    if temp_down_c >= ceiling_c:
        raise SystemExit("computed temp_down_c must be below ceiling")

    protocol = {
        "schema": "thermal-guardian-m3-protocol-v1",
        "status": "smoke_only" if args.stop_after_smoke else "running",
        "output_root": str(output_root),
        "arrival_interval_sec": args.arrival_interval_sec,
        "smoke_duration_sec": args.smoke_duration_sec,
        "duration_sec": args.duration_sec,
        "safety_temp_c": args.safety_temp_c,
        "max_start_temp_c": args.max_start_temp_c,
        "ceiling_c": ceiling_c,
        "temp_down_c": temp_down_c,
        "fan_off_confirmed": args.fan_off_confirmed,
        "power_logging": "not_used",
        "claim_note": (
            "M3 tests thermal continuity under fan-off stress. It does not claim "
            "hardware wear reduction, lifespan improvement, or energy efficiency."
        ),
    }
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
        if mode == "controller":
            run_dir = _run_controller_condition(
                label=label,
                output_root=output_root,
                router_base_config=args.router_base_config,
                m2_config=args.m2_config,
                duration_sec=args.duration_sec,
                arrival_interval_sec=args.arrival_interval_sec,
                safety_temp_c=args.safety_temp_c,
                max_start_temp_c=args.max_start_temp_c,
                cooldown_timeout_sec=args.cooldown_timeout_sec,
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
                max_start_temp_c=args.max_start_temp_c,
                cooldown_timeout_sec=args.cooldown_timeout_sec,
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


def _run_m2_condition(
    *,
    label: str,
    mode: str,
    output_root: Path,
    m2_config: str,
    duration_sec: float,
    arrival_interval_sec: float,
    safety_temp_c: float,
    max_start_temp_c: float,
    cooldown_timeout_sec: float,
    prompt_id_prefix: str,
) -> Path:
    run_dir = output_root / label
    if (run_dir / "manifest.json").exists():
        print(f"skip existing run: {run_dir}")
        return run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    _wait_for_start_gate(
        run_dir=run_dir,
        max_start_temp_c=max_start_temp_c,
        cooldown_timeout_sec=cooldown_timeout_sec,
    )
    with (run_dir / "m2_run.stdout.log").open("w", encoding="utf-8") as stdout:
        subprocess.run(
            [
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
            ],
            stdout=stdout,
            stderr=subprocess.STDOUT,
            check=True,
        )
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
    max_start_temp_c: float,
    cooldown_timeout_sec: float,
    ceiling_c: float,
    temp_down_c: float,
) -> Path:
    run_dir = output_root / label
    if (run_dir / "manifest.json").exists():
        print(f"skip existing run: {run_dir}")
        return run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    _wait_for_start_gate(
        run_dir=run_dir,
        max_start_temp_c=max_start_temp_c,
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
            subprocess.run(
                [
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
                ],
                stdout=stdout,
                stderr=subprocess.STDOUT,
                check=True,
            )
    finally:
        router.terminate()
        try:
            router.wait(timeout=10)
        except subprocess.TimeoutExpired:
            router.kill()
            router.wait(timeout=5)
        router_stdout.close()
    return run_dir


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


def _wait_for_start_gate(
    *,
    run_dir: Path,
    max_start_temp_c: float,
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
            "max_start_temp_c": max_start_temp_c,
            "ok": temp_c <= max_start_temp_c and throttled == "0x0",
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
            raise SystemExit(
                f"start gate timed out for {run_dir}: temp={temp_c:.1f}C "
                f"throttled={throttled}, required temp<={max_start_temp_c:.1f}C "
                "and throttled=0x0"
            )
        print(
            f"waiting for start gate: temp={temp_c:.1f}C "
            f"throttled={throttled} target<={max_start_temp_c:.1f}C",
            flush=True,
        )
        time.sleep(10)


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
