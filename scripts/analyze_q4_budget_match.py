#!/usr/bin/env python3
"""Summarize Q4 residence time for matched-budget controller experiments."""

from __future__ import annotations

import argparse
import json

from thermal_guardian.q4_budget import (
    build_budget_match_summary,
    summarize_run,
    write_budget_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-up", type=float, default=63.0)
    parser.add_argument(
        "--baseline-group",
        default="bounded",
        help="group used as Q4-time baseline after stripping trailing _NNN",
    )
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="LABEL=run_dir",
        help="repeatable; e.g. bounded_001=data/m2/.../bounded",
    )
    parser.add_argument("--out-json")
    parser.add_argument("--out-csv")
    args = parser.parse_args()

    runs = []
    for spec in args.run:
        if "=" not in spec:
            raise SystemExit(f"--run expects LABEL=run_dir, got: {spec}")
        label, run_dir = spec.split("=", 1)
        runs.append(summarize_run(label, run_dir, temp_up_c=args.temp_up))

    summary = build_budget_match_summary(runs, baseline_group=args.baseline_group)
    print(json.dumps(summary, indent=2))
    write_budget_outputs(summary, out_json=args.out_json, out_csv=args.out_csv)


if __name__ == "__main__":
    main()
