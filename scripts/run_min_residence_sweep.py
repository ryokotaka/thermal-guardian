#!/usr/bin/env python3
"""Run a small min-residence sweep on the Raspberry Pi.

This intentionally runs one pass per residence value. Use the resulting
trade-off table to decide whether one point deserves N=3 confirmation.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import argparse
import json
import re
import socket
import subprocess
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--m0-config", default="m0.local.json")
    parser.add_argument("--m2-config", default="m2.local.json")
    parser.add_argument(
        "--router-config",
        default="config.m2.fan_on.predictive.dwell.example.json",
    )
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--duration-sec", type=float, default=600.0)
    parser.add_argument("--arrival-interval-sec", type=float, default=4.0)
    parser.add_argument("--max-start-temp-c", type=float, default=50.0)
    parser.add_argument("--cooldown-timeout-sec", type=float, default=1800.0)
    parser.add_argument(
        "--min-residence-sec",
        type=float,
        action="append",
        required=True,
        help="Residence value to run; pass repeatedly, e.g. 30 60 90.",
    )
    args = parser.parse_args()

    output_root = Path(args.output_root or f"data/m2/{args.date}/min_residence")
    output_root.mkdir(parents=True, exist_ok=True)

    _ensure_backends(args.m0_config)
    run_specs = []
    for value in args.min_residence_sec:
        label = f"bounded_dwell{_label_number(value)}_001"
        run_dir = _run_one(
            label=label,
            min_residence_sec=value,
            output_root=output_root,
            router_config=args.router_config,
            m2_config=args.m2_config,
            duration_sec=args.duration_sec,
            arrival_interval_sec=args.arrival_interval_sec,
            max_start_temp_c=args.max_start_temp_c,
            cooldown_timeout_sec=args.cooldown_timeout_sec,
        )
        run_specs.append(f"{label}:{value:g}={run_dir}")

    out_json = output_root / "min_residence_tradeoff.json"
    out_csv = output_root / "min_residence_tradeoff.csv"
    subprocess.run(
        [
            sys.executable,
            "scripts/analyze_min_residence_tradeoff.py",
            "--out-json",
            str(out_json),
            "--out-csv",
            str(out_csv),
            *sum((["--run", spec] for spec in run_specs), []),
        ],
        check=True,
    )
    print(f"tradeoff_json={out_json}")
    print(f"tradeoff_csv={out_csv}")


def _ensure_backends(m0_config: str) -> None:
    check = [sys.executable, "-m", "thermal_guardian.m0", "check", "--config", m0_config]
    result = subprocess.run(check, check=False)
    if result.returncode == 0:
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


def _run_one(
    *,
    label: str,
    min_residence_sec: float,
    output_root: Path,
    router_config: str,
    m2_config: str,
    duration_sec: float,
    arrival_interval_sec: float,
    max_start_temp_c: float,
    cooldown_timeout_sec: float,
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
    _write_router_run_config(run_dir=run_dir, router_config=router_config)
    _stop_router()
    router_stdout = (run_dir / "router.stdout.log").open("w", encoding="utf-8")
    router = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "thermal_guardian.router",
            "--config",
            str(run_dir / "router.local.json"),
            "--min-residence-sec",
            str(min_residence_sec),
        ],
        stdout=router_stdout,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_port("127.0.0.1", 8080)
        with (run_dir / "m2_run.stdout.log").open("w", encoding="utf-8") as m2_stdout:
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
                    "fan_on",
                    "--prompt-id-prefix",
                    f"dwell{_label_number(min_residence_sec)}",
                ],
                stdout=m2_stdout,
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


def _write_router_run_config(*, run_dir: Path, router_config: str) -> None:
    base = json.loads(Path(router_config).read_text(encoding="utf-8"))
    if not isinstance(base, dict):
        raise SystemExit(f"router config must be a JSON object: {router_config}")
    base["log_dir"] = str(run_dir / "router_logs")
    (run_dir / "router.local.json").write_text(
        json.dumps(base, indent=2) + "\n",
        encoding="utf-8",
    )


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


def _label_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value).replace(".", "p")


if __name__ == "__main__":
    main()
