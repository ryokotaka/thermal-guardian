#!/usr/bin/env python3
"""Run the Q4-time matched counterfactual protocol on the Raspberry Pi.

This is a convenience driver for the experiment documented in
docs/findings_lookahead.md. It assumes q8/q4 llama-server can be started from
m0.local.json and that existing bounded look-ahead N=3 runs are already present.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import argparse
import json
import socket
import subprocess
import sys
import time
from typing import Any

from thermal_guardian.q4_budget import (
    build_budget_match_summary,
    summarize_run,
    write_budget_outputs,
)


CANDIDATES = {
    "reactive_up61_down59": (61.0, 59.0),
    "reactive_up60_down58": (60.0, 58.0),
    "reactive_up59_down57": (59.0, 57.0),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--m0-config", default="m0.local.json")
    parser.add_argument("--m2-config", default="m2.local.json")
    parser.add_argument("--router-base-config", default="config.m2.fan_on.local.json")
    parser.add_argument("--bounded-root", default=None)
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--duration-sec", type=float, default=600.0)
    parser.add_argument("--arrival-interval-sec", type=float, default=4.0)
    parser.add_argument("--temp-up", type=float, default=63.0)
    args = parser.parse_args()

    output_root = Path(args.output_root or f"data/m2/{args.date}/q4_budget_match")
    output_root.mkdir(parents=True, exist_ok=True)
    bounded_runs = _bounded_runs(args.date, args.bounded_root)

    _ensure_backends(args.m0_config)

    run_dirs: dict[str, Path] = {}
    for group, (temp_up, temp_down) in CANDIDATES.items():
        run_dirs[f"{group}_001"] = _run_one(
            group=group,
            index=1,
            temp_up=temp_up,
            temp_down=temp_down,
            output_root=output_root,
            router_base_config=args.router_base_config,
            m2_config=args.m2_config,
            duration_sec=args.duration_sec,
            arrival_interval_sec=args.arrival_interval_sec,
        )

    calibration = _summarize(
        bounded_runs=bounded_runs,
        candidate_runs=run_dirs,
        output_root=output_root,
        temp_up_c=args.temp_up,
        stem="q4_budget_match_calibration",
    )
    selected = calibration.get("selected_candidate") or {}
    selected_group = selected.get("group")
    if selected_group not in CANDIDATES:
        raise SystemExit(f"no selected candidate found in {output_root}")

    temp_up, temp_down = CANDIDATES[selected_group]
    for index in (2, 3):
        label = f"{selected_group}_{index:03d}"
        run_dirs[label] = _run_one(
            group=selected_group,
            index=index,
            temp_up=temp_up,
            temp_down=temp_down,
            output_root=output_root,
            router_base_config=args.router_base_config,
            m2_config=args.m2_config,
            duration_sec=args.duration_sec,
            arrival_interval_sec=args.arrival_interval_sec,
        )

    final = _summarize(
        bounded_runs=bounded_runs,
        candidate_runs={label: path for label, path in run_dirs.items() if label.startswith(selected_group)},
        output_root=output_root,
        temp_up_c=args.temp_up,
        stem="q4_budget_match_summary",
    )
    print(json.dumps(final, indent=2))
    print(f"selected_candidate={selected_group}")
    print(f"summary_json={output_root / 'q4_budget_match_summary.json'}")
    print(f"summary_csv={output_root / 'q4_budget_match_summary.csv'}")


def _bounded_runs(run_date: str, bounded_root: str | None) -> dict[str, Path]:
    if bounded_root is not None:
        root = Path(bounded_root)
        runs = {
            f"bounded_{idx:03d}": root / f"lookahead_open_loop_10min_4s_{idx:03d}" / "bounded"
            for idx in (1, 2, 3)
        }
    else:
        runs = {
            f"bounded_{idx:03d}": Path(
                f"data/m2/{run_date}/lookahead_open_loop_10min_4s_{idx:03d}/bounded"
            )
            for idx in (1, 2, 3)
        }
    missing = [str(path) for path in runs.values() if not (path / "manifest.json").exists()]
    if missing:
        raise SystemExit("missing bounded baseline runs:\n" + "\n".join(missing))
    return runs


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
    group: str,
    index: int,
    temp_up: float,
    temp_down: float,
    output_root: Path,
    router_base_config: str,
    m2_config: str,
    duration_sec: float,
    arrival_interval_sec: float,
) -> Path:
    run_dir = output_root / f"{group}_{index:03d}"
    manifest = run_dir / "manifest.json"
    if manifest.exists():
        print(f"skip existing run: {run_dir}")
        return run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    router_config = _write_router_config(
        run_dir=run_dir,
        router_base_config=router_base_config,
        temp_up=temp_up,
        temp_down=temp_down,
    )
    _stop_router()
    router = subprocess.Popen(
        [sys.executable, "-m", "thermal_guardian.router", "--config", str(router_config)]
    )
    try:
        _wait_for_port("127.0.0.1", 8080)
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
                "q4-budget",
            ],
            check=True,
        )
    finally:
        router.terminate()
        try:
            router.wait(timeout=10)
        except subprocess.TimeoutExpired:
            router.kill()
            router.wait(timeout=5)
    return run_dir


def _write_router_config(
    *,
    run_dir: Path,
    router_base_config: str,
    temp_up: float,
    temp_down: float,
) -> Path:
    base = json.loads(Path(router_base_config).read_text(encoding="utf-8"))
    if not isinstance(base, dict):
        raise SystemExit(f"router base config must be a JSON object: {router_base_config}")
    base.update(
        {
            "temp_up_c": temp_up,
            "temp_down_c": temp_down,
            "look_ahead_sec": 0.0,
            "log_dir": str(run_dir / "router_logs"),
        }
    )
    for key in (
        "slope_window",
        "look_ahead_min_samples",
        "look_ahead_min_temp_c",
        "look_ahead_max_delta_c",
    ):
        base.pop(key, None)
    path = run_dir / "router.local.json"
    path.write_text(json.dumps(base, indent=2) + "\n", encoding="utf-8")
    return path


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


def _summarize(
    *,
    bounded_runs: dict[str, Path],
    candidate_runs: dict[str, Path],
    output_root: Path,
    temp_up_c: float,
    stem: str,
) -> dict[str, Any]:
    runs = [
        summarize_run(label, path, temp_up_c=temp_up_c)
        for label, path in {**bounded_runs, **candidate_runs}.items()
    ]
    summary = build_budget_match_summary(runs)
    write_budget_outputs(
        summary,
        out_json=output_root / f"{stem}.json",
        out_csv=output_root / f"{stem}.csv",
    )
    return summary


if __name__ == "__main__":
    main()
