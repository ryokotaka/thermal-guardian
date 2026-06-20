#!/usr/bin/env python3
"""Build a min-residence trade-off table from completed controller runs."""

from __future__ import annotations

from pathlib import Path
import argparse
import csv
import json
import statistics
from typing import Any

from thermal_guardian.q4_budget import summarize_run


FIELDS = [
    "label",
    "min_residence_sec",
    "n",
    "run_dirs",
    "completed_requests_median",
    "start_temp_c_median",
    "peak_temp_c_median",
    "secs_at_or_above_63_median",
    "q4_time_sec_median",
    "q4_fraction_median",
    "q4_fraction_delta_from_baseline",
    "total_switches_median",
    "switch_to_q4_count_median",
    "switch_to_q8_count_median",
    "residence_blocked_count_median",
    "throttle_seen_any",
    "safety_stop_any",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        help=(
            "Run spec in the form label:min_residence_sec=run_dir. "
            "Repeat the same label to aggregate N>1 as one median point."
        ),
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
    grouped: dict[tuple[str, float], list[Any]] = {}
    for spec in specs:
        label, min_residence_sec, run_dir = _parse_run_spec(spec)
        grouped.setdefault((label, min_residence_sec), []).append(
            summarize_run(label, run_dir, temp_up_c=temp_up_c)
        )
    points = [
        _summarize_point(label, min_residence_sec, rows)
        for (label, min_residence_sec), rows in grouped.items()
    ]
    baseline = _select_baseline(points, baseline_label)
    baseline_fraction = baseline["q4_fraction_median"]

    rows = []
    for point in points:
        rows.append(
            {
                "label": point["label"],
                "min_residence_sec": _format_number(point["min_residence_sec"]),
                "n": point["n"],
                "run_dirs": ";".join(point["run_dirs"]),
                "completed_requests_median": _format_optional(
                    point["completed_requests_median"]
                ),
                "start_temp_c_median": _format_optional(point["start_temp_c_median"]),
                "peak_temp_c_median": _format_optional(point["peak_temp_c_median"]),
                "secs_at_or_above_63_median": _format_optional(
                    point["secs_at_or_above_63_median"]
                ),
                "q4_time_sec_median": _format_optional(point["q4_time_sec_median"]),
                "q4_fraction_median": f"{point['q4_fraction_median']:.6f}",
                "q4_fraction_delta_from_baseline": (
                    f"{point['q4_fraction_median'] - baseline_fraction:.6f}"
                ),
                "total_switches_median": _format_optional(point["total_switches_median"]),
                "switch_to_q4_count_median": _format_optional(
                    point["switch_to_q4_count_median"]
                ),
                "switch_to_q8_count_median": _format_optional(
                    point["switch_to_q8_count_median"]
                ),
                "residence_blocked_count_median": _format_optional(
                    point["residence_blocked_count_median"]
                ),
                "throttle_seen_any": str(point["throttle_seen_any"]).lower(),
                "safety_stop_any": str(point["safety_stop_any"]).lower(),
            }
        )
    return sorted(rows, key=lambda row: float(row["min_residence_sec"]))


def _summarize_point(label: str, min_residence_sec: float, rows: list[Any]) -> dict[str, Any]:
    return {
        "label": label,
        "min_residence_sec": min_residence_sec,
        "n": len(rows),
        "run_dirs": [row.run_dir for row in rows],
        "completed_requests_median": _median([row.completed_requests for row in rows]),
        "start_temp_c_median": _median_optional([row.start_temp_c for row in rows]),
        "peak_temp_c_median": _median_optional([row.peak_temp_c for row in rows]),
        "secs_at_or_above_63_median": _median(
            [row.secs_at_or_above_temp_up for row in rows]
        ),
        "q4_time_sec_median": _median([row.q4_time_sec for row in rows]),
        "q4_fraction_median": _median([row.q4_fraction for row in rows]),
        "total_switches_median": _median(
            [row.switch_to_q4_count + row.switch_to_q8_count for row in rows]
        ),
        "switch_to_q4_count_median": _median([row.switch_to_q4_count for row in rows]),
        "switch_to_q8_count_median": _median([row.switch_to_q8_count for row in rows]),
        "residence_blocked_count_median": _median(
            [row.residence_blocked_count for row in rows]
        ),
        "throttle_seen_any": any(row.throttle_seen for row in rows),
        "safety_stop_any": any(row.safety_stop for row in rows),
    }


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


def _select_baseline(points: list[dict[str, Any]], baseline_label: str | None) -> dict[str, Any]:
    if not points:
        raise SystemExit("at least one --run is required")
    if baseline_label is None:
        return points[0]
    for point in points:
        if point["label"] == baseline_label:
            return point
    raise SystemExit(f"baseline label not found: {baseline_label}")


def _median(values: list[float | int]) -> float | int | None:
    if not values:
        return None
    value = statistics.median(values)
    return round(value, 6) if isinstance(value, float) else value


def _median_optional(values: list[float | None]) -> float | None:
    return _median([value for value in values if value is not None])


def _format_optional(value: float | int | None) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    return f"{value:.3f}"


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.3f}"


if __name__ == "__main__":
    main()
