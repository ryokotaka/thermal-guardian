"""Q4-time budget analysis for controller counterfactual runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import json
import re
import statistics
from typing import Any


@dataclass(frozen=True)
class RunBudgetSummary:
    label: str
    group: str
    run_dir: str
    completed_requests: int
    tokens_out_total: int
    start_temp_c: float | None
    peak_temp_c: float | None
    secs_at_or_above_temp_up: float
    throttle_seen: bool
    safety_stop: bool
    q4_time_sec: float
    q8_time_sec: float
    q4_fraction: float
    switch_to_q4_count: int
    switch_to_q8_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "group": self.group,
            "run_dir": self.run_dir,
            "completed_requests": self.completed_requests,
            "tokens_out_total": self.tokens_out_total,
            "start_temp_c": self.start_temp_c,
            "peak_temp_c": self.peak_temp_c,
            "secs_at_or_above_temp_up": round(self.secs_at_or_above_temp_up, 3),
            "throttle_seen": self.throttle_seen,
            "safety_stop": self.safety_stop,
            "q4_time_sec": round(self.q4_time_sec, 3),
            "q8_time_sec": round(self.q8_time_sec, 3),
            "q4_fraction": round(self.q4_fraction, 6),
            "switch_to_q4_count": self.switch_to_q4_count,
            "switch_to_q8_count": self.switch_to_q8_count,
        }


def summarize_run(label: str, run_dir: str | Path, *, temp_up_c: float) -> RunBudgetSummary:
    run_path = Path(run_dir)
    manifest = _read_json_required(run_path / "manifest.json")
    started_ts = _required_float(manifest.get("started_ts"), "started_ts", run_path)
    finished_ts = _required_float(manifest.get("finished_ts"), "finished_ts", run_path)
    if finished_ts < started_ts:
        raise ValueError(f"finished_ts is before started_ts for run_dir: {run_path}")

    requests = _read_requests(run_path / "requests.csv")
    telemetry = _read_telemetry(run_path / "telemetry.csv")
    events = _read_events(run_path / "router_logs" / "events.csv")

    events_in_window = _events_in_window(
        events=events,
        started_ts=started_ts,
        finished_ts=finished_ts,
    )
    q4_time_sec, q8_time_sec = _compute_state_times(
        events=events,
        started_ts=started_ts,
        finished_ts=finished_ts,
    )
    duration = max(0.0, finished_ts - started_ts)
    q4_fraction = q4_time_sec / duration if duration else 0.0

    temps = [row["temp_c"] for row in telemetry]
    throttles = [row["throttled_hex"] for row in telemetry]

    return RunBudgetSummary(
        label=label,
        group=group_label(label),
        run_dir=str(run_path),
        completed_requests=len(requests),
        tokens_out_total=sum(row["tokens_out"] for row in requests),
        start_temp_c=temps[0] if temps else None,
        peak_temp_c=max(temps) if temps else None,
        secs_at_or_above_temp_up=_time_at_or_above(telemetry, temp_up_c),
        throttle_seen=any(int(value, 16) != 0 for value in throttles),
        safety_stop=bool(manifest.get("safety_stop")),
        q4_time_sec=q4_time_sec,
        q8_time_sec=q8_time_sec,
        q4_fraction=q4_fraction,
        switch_to_q4_count=sum(1 for row in events_in_window if row["event"] == "switch_to_q4"),
        switch_to_q8_count=sum(1 for row in events_in_window if row["event"] == "switch_to_q8"),
    )


def build_budget_match_summary(
    runs: list[RunBudgetSummary],
    *,
    baseline_group: str = "bounded",
) -> dict[str, Any]:
    if not runs:
        raise ValueError("at least one run is required")

    groups: dict[str, list[RunBudgetSummary]] = {}
    for run in runs:
        groups.setdefault(run.group, []).append(run)

    group_summaries = {
        group: _summarize_group(rows)
        for group, rows in sorted(groups.items())
    }
    baseline = group_summaries.get(baseline_group)
    if baseline is None:
        raise ValueError(f"baseline group not found: {baseline_group}")

    baseline_q4 = baseline["q4_time_sec_median"]
    selected = None
    if baseline_q4 is not None:
        candidates = [
            (group, summary)
            for group, summary in group_summaries.items()
            if group != baseline_group and summary["q4_time_sec_median"] is not None
        ]
        if candidates:
            selected = min(
                candidates,
                key=lambda item: abs(item[1]["q4_time_sec_median"] - baseline_q4),
            )

    selected_summary = None
    if selected is not None:
        group, summary = selected
        diff = summary["q4_time_sec_median"] - baseline_q4
        selected_summary = {
            "group": group,
            "q4_time_diff_sec": round(diff, 3),
            "q4_time_diff_fraction_of_baseline": (
                round(diff / baseline_q4, 6) if baseline_q4 else None
            ),
        }

    return {
        "schema": "thermal-guardian-q4-budget-match-v1",
        "baseline_group": baseline_group,
        "selected_candidate": selected_summary,
        "runs": [run.as_dict() for run in runs],
        "groups": group_summaries,
        "claim_note": (
            "This compares thermal behavior at similar Q4 residence time; it is "
            "not an output-quality, safety, or optimal-control claim."
        ),
    }


def group_label(label: str) -> str:
    return re.sub(r"_(?:\d{3}|retry)$", "", label)


def write_budget_outputs(
    summary: dict[str, Any],
    *,
    out_json: str | Path | None = None,
    out_csv: str | Path | None = None,
) -> None:
    if out_json is not None:
        path = Path(out_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if out_csv is not None:
        rows = summary["runs"]
        path = Path(out_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def _summarize_group(rows: list[RunBudgetSummary]) -> dict[str, Any]:
    return {
        "run_count": len(rows),
        "completed_requests_median": _median([row.completed_requests for row in rows]),
        "tokens_out_total_median": _median([row.tokens_out_total for row in rows]),
        "peak_temp_c_median": _median_optional([row.peak_temp_c for row in rows]),
        "secs_at_or_above_temp_up_median": _median(
            [row.secs_at_or_above_temp_up for row in rows]
        ),
        "q4_time_sec_median": _median([row.q4_time_sec for row in rows]),
        "q4_fraction_median": _median([row.q4_fraction for row in rows]),
        "switch_to_q4_count_median": _median([row.switch_to_q4_count for row in rows]),
        "switch_to_q8_count_median": _median([row.switch_to_q8_count for row in rows]),
        "throttle_seen_any": any(row.throttle_seen for row in rows),
        "safety_stop_any": any(row.safety_stop for row in rows),
    }


def _compute_state_times(
    *,
    events: list[dict[str, Any]],
    started_ts: float,
    finished_ts: float,
) -> tuple[float, float]:
    if not events:
        raise ValueError("events.csv contains no rows")

    events = sorted(events, key=lambda row: row["ts"])
    initial_state = "q8"
    for row in events:
        if row["ts"] <= started_ts:
            initial_state = row["state"]
        else:
            break
    if all(row["ts"] > started_ts for row in events):
        initial_state = events[0]["state"]

    q4_time = 0.0
    q8_time = 0.0
    current_ts = started_ts
    current_state = initial_state

    for row in events:
        ts = row["ts"]
        if ts <= started_ts:
            continue
        if ts > finished_ts:
            break
        q4_time, q8_time = _add_interval(
            q4_time,
            q8_time,
            state=current_state,
            duration=max(0.0, ts - current_ts),
        )
        current_state = row["state"]
        current_ts = ts

    q4_time, q8_time = _add_interval(
        q4_time,
        q8_time,
        state=current_state,
        duration=max(0.0, finished_ts - current_ts),
    )
    return q4_time, q8_time


def _events_in_window(
    *,
    events: list[dict[str, Any]],
    started_ts: float,
    finished_ts: float,
) -> list[dict[str, Any]]:
    return [row for row in events if started_ts <= row["ts"] <= finished_ts]


def _add_interval(
    q4_time: float,
    q8_time: float,
    *,
    state: str,
    duration: float,
) -> tuple[float, float]:
    if state == "q4":
        return q4_time + duration, q8_time
    if state == "q8":
        return q4_time, q8_time + duration
    return q4_time, q8_time


def _time_at_or_above(rows: list[dict[str, Any]], temp_up_c: float) -> float:
    total = 0.0
    for first, second in zip(rows, rows[1:]):
        if first["temp_c"] >= temp_up_c:
            total += max(0.0, second["ts"] - first["ts"])
    return total


def _read_json_required(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"missing required file: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {path}")
    return data


def _read_requests(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"missing required file: {path}")
    rows = []
    with path.open(encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            rows.append(
                {
                    "ts": float(row["ts"]),
                    "tokens_out": int(row.get("tokens_out") or 0),
                }
            )
    return rows


def _read_telemetry(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"missing required file: {path}")
    rows = []
    with path.open(encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            rows.append(
                {
                    "ts": float(row["ts"]),
                    "temp_c": float(row["temp_c"]),
                    "throttled_hex": row["throttled_hex"],
                }
            )
    return rows


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"missing required file: {path}")
    rows = []
    with path.open(encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            rows.append(
                {
                    "ts": float(row["ts"]),
                    "state": row["state"],
                    "event": row["event"],
                }
            )
    return rows


def _required_float(value: Any, field: str, run_path: Path) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"manifest field {field} must be numeric for run_dir: {run_path}") from exc


def _median(values: list[float | int]) -> float | int | None:
    if not values:
        return None
    value = statistics.median(values)
    return round(value, 6) if isinstance(value, float) else value


def _median_optional(values: list[float | None]) -> float | None:
    return _median([value for value in values if value is not None])
