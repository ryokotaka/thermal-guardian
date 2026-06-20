#!/usr/bin/env python3
"""Build a min-residence trade-off table from completed controller runs."""

from __future__ import annotations

from pathlib import Path
import argparse
import csv
import json
from typing import Any

from thermal_guardian.q4_budget import summarize_run


FIELDS = [
    "label",
    "min_residence_sec",
    "run_dir",
    "completed_requests",
    "start_temp_c",
    "peak_temp_c",
    "secs_at_or_above_63",
    "q4_time_sec",
    "q4_fraction",
    "q4_fraction_delta_from_baseline",
    "total_switches",
    "switch_to_q4_count",
    "switch_to_q8_count",
    "residence_blocked_count",
    "throttle_seen",
    "safety_stop",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        help="Run spec in the form label:min_residence_sec=run_dir",
    )
    parser.add_argument("--baseline-label", default=None)
    parser.add_argument("--temp-up", type=float, default=63.0)
    parser.add_argument("--out-json")
    parser.add_argument("--out-csv")
    args = parser.parse_args()

    rows = build_tradeoff_rows(
        specs=args.run,
        baseline_label=args.baseline_label,
        temp_up_c=args.temp_up,
    )
    summary = {
        "schema": "thermal-guardian-min-residence-tradeoff-v1",
        "baseline_label": args.baseline_label or rows[0]["label"],
        "rows": rows,
        "claim_note": (
            "This is a switch-economy sweep. It checks whether short dwell reduces "
            "switching without materially increasing Q4 time; it is not an output "
            "quality, safety, or optimal-control claim."
        ),
    }

    if args.out_json:
        path = Path(args.out_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if args.out_csv:
        path = Path(args.out_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(summary, indent=2))


def build_tradeoff_rows(
    *,
    specs: list[str],
    baseline_label: str | None,
    temp_up_c: float,
) -> list[dict[str, Any]]:
    parsed = [_parse_run_spec(spec) for spec in specs]
    summaries = [
        (
            label,
            min_residence_sec,
            summarize_run(label, run_dir, temp_up_c=temp_up_c),
        )
        for label, min_residence_sec, run_dir in parsed
    ]
    baseline = _select_baseline(summaries, baseline_label)
    baseline_fraction = baseline.q4_fraction

    rows = []
    for label, min_residence_sec, summary in summaries:
        rows.append(
            {
                "label": label,
                "min_residence_sec": _format_number(min_residence_sec),
                "run_dir": summary.run_dir,
                "completed_requests": summary.completed_requests,
                "start_temp_c": _format_optional(summary.start_temp_c),
                "peak_temp_c": _format_optional(summary.peak_temp_c),
                "secs_at_or_above_63": f"{summary.secs_at_or_above_temp_up:.3f}",
                "q4_time_sec": f"{summary.q4_time_sec:.3f}",
                "q4_fraction": f"{summary.q4_fraction:.6f}",
                "q4_fraction_delta_from_baseline": (
                    f"{summary.q4_fraction - baseline_fraction:.6f}"
                ),
                "total_switches": summary.switch_to_q4_count + summary.switch_to_q8_count,
                "switch_to_q4_count": summary.switch_to_q4_count,
                "switch_to_q8_count": summary.switch_to_q8_count,
                "residence_blocked_count": summary.residence_blocked_count,
                "throttle_seen": str(summary.throttle_seen).lower(),
                "safety_stop": str(summary.safety_stop).lower(),
            }
        )
    return rows


def _parse_run_spec(spec: str) -> tuple[str, float, Path]:
    try:
        label_and_value, run_dir = spec.split("=", 1)
        label, value = label_and_value.split(":", 1)
    except ValueError as exc:
        raise SystemExit(
            f"invalid --run spec {spec!r}; expected label:min_residence_sec=run_dir"
        ) from exc
    try:
        min_residence_sec = float(value)
    except ValueError as exc:
        raise SystemExit(f"invalid min_residence_sec in --run spec: {spec!r}") from exc
    if not label:
        raise SystemExit(f"missing label in --run spec: {spec!r}")
    return label, min_residence_sec, Path(run_dir)


def _select_baseline(
    summaries: list[tuple[str, float, Any]],
    baseline_label: str | None,
) -> Any:
    if not summaries:
        raise SystemExit("at least one --run is required")
    if baseline_label is None:
        return summaries[0][2]
    for label, _, summary in summaries:
        if label == baseline_label:
            return summary
    raise SystemExit(f"baseline label not found: {baseline_label}")


def _format_optional(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.3f}"


if __name__ == "__main__":
    main()
