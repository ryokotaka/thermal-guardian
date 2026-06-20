import csv
import json

import pytest

from thermal_guardian.q4_budget import (
    build_budget_match_summary,
    group_label,
    summarize_run,
)


def write_run(
    run_dir,
    *,
    started_ts=100.0,
    finished_ts=200.0,
    events=None,
    temps=None,
    tokens=(10, 20),
):
    run_dir.mkdir(parents=True)
    (run_dir / "router_logs").mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "started_ts": started_ts,
                "finished_ts": finished_ts,
                "mode": "controller",
                "cooling": "fan_on",
                "safety_stop": False,
            }
        ),
        encoding="utf-8",
    )
    request_lines = [
        "ts,prompt_id,mode,cooling,ok,status_code,latency_ms,tokens_out,tokens_per_sec,model,url,detail"
    ]
    for idx, token_count in enumerate(tokens, start=1):
        request_lines.append(
            f"{started_ts + idx},p{idx},controller,fan_on,true,200,10.0,{token_count},1.0,m,u,ok"
        )
    (run_dir / "requests.csv").write_text("\n".join(request_lines) + "\n", encoding="utf-8")

    telemetry_lines = ["ts,elapsed_sec,mode,cooling,temp_c,clock_hz,throttled_hex"]
    for idx, temp in enumerate(temps or [50.0, 64.0, 62.0]):
        ts = started_ts + idx * 10
        telemetry_lines.append(f"{ts},{ts-started_ts},controller,fan_on,{temp},1500000000,0x0")
    (run_dir / "telemetry.csv").write_text("\n".join(telemetry_lines) + "\n", encoding="utf-8")

    event_lines = ["ts,temp_c,clock_hz,throttled_hex,state,event"]
    for row in events or [
        (100.0, 50.0, "q8", "none"),
        (120.0, 61.0, "q4", "switch_to_q4"),
        (150.0, 58.0, "q8", "switch_to_q8"),
    ]:
        ts, temp, state, event = row
        event_lines.append(f"{ts},{temp},1500000000,0x0,{state},{event}")
    (run_dir / "router_logs" / "events.csv").write_text(
        "\n".join(event_lines) + "\n",
        encoding="utf-8",
    )


def test_group_label_strips_run_suffix() -> None:
    assert group_label("reactive_up61_down59_001") == "reactive_up61_down59"
    assert group_label("bounded_retry") == "bounded"
    assert group_label("bounded") == "bounded"


def test_summarize_run_computes_q4_time_between_switches(tmp_path) -> None:
    run_dir = tmp_path / "run"
    write_run(run_dir)

    summary = summarize_run("bounded_001", run_dir, temp_up_c=63.0)

    assert summary.q4_time_sec == 30.0
    assert summary.q8_time_sec == 70.0
    assert summary.q4_fraction == 0.3
    assert summary.switch_to_q4_count == 1
    assert summary.switch_to_q8_count == 1
    assert summary.residence_blocked_count == 0
    assert summary.completed_requests == 2
    assert summary.tokens_out_total == 30
    assert summary.peak_temp_c == 64.0
    assert summary.secs_at_or_above_temp_up == 10.0


def test_summarize_run_counts_final_q4_until_run_end(tmp_path) -> None:
    run_dir = tmp_path / "run"
    write_run(
        run_dir,
        events=[
            (100.0, 50.0, "q8", "none"),
            (120.0, 61.0, "q4", "switch_to_q4"),
        ],
    )

    summary = summarize_run("bounded_001", run_dir, temp_up_c=63.0)

    assert summary.q4_time_sec == 80.0
    assert summary.q8_time_sec == 20.0


def test_summarize_run_ignores_switch_events_after_run_end(tmp_path) -> None:
    run_dir = tmp_path / "run"
    write_run(
        run_dir,
        events=[
            (100.0, 50.0, "q8", "none"),
            (120.0, 61.0, "q4", "switch_to_q4"),
            (150.0, 58.0, "q8", "switch_to_q8"),
            (260.0, 61.0, "q4", "switch_to_q4"),
        ],
    )

    summary = summarize_run("bounded_001", run_dir, temp_up_c=63.0)

    assert summary.q4_time_sec == 30.0
    assert summary.q8_time_sec == 70.0
    assert summary.switch_to_q4_count == 1
    assert summary.switch_to_q8_count == 1


def test_summarize_run_counts_residence_blocked_events(tmp_path) -> None:
    run_dir = tmp_path / "run"
    write_run(
        run_dir,
        events=[
            (100.0, 50.0, "q8", "none"),
            (120.0, 61.0, "q4", "switch_to_q4"),
            (130.0, 58.0, "q4", "residence_blocked"),
            (140.0, 58.0, "q4", "residence_blocked"),
            (150.0, 58.0, "q8", "switch_to_q8"),
        ],
    )

    summary = summarize_run("bounded_dwell30_001", run_dir, temp_up_c=63.0)

    assert summary.residence_blocked_count == 2
    assert summary.as_dict()["residence_blocked_count"] == 2


def test_summarize_run_requires_events_csv(tmp_path) -> None:
    run_dir = tmp_path / "run"
    write_run(run_dir)
    (run_dir / "router_logs" / "events.csv").unlink()

    with pytest.raises(FileNotFoundError, match="events.csv"):
        summarize_run("bounded_001", run_dir, temp_up_c=63.0)


def test_budget_match_selects_nearest_candidate_group(tmp_path) -> None:
    bounded = tmp_path / "bounded"
    close = tmp_path / "close"
    far = tmp_path / "far"
    write_run(
        bounded,
        events=[(100.0, 50.0, "q8", "none"), (140.0, 61.0, "q4", "switch_to_q4")],
    )
    write_run(
        close,
        events=[(100.0, 50.0, "q8", "none"), (145.0, 61.0, "q4", "switch_to_q4")],
    )
    write_run(
        far,
        events=[(100.0, 50.0, "q8", "none"), (180.0, 61.0, "q4", "switch_to_q4")],
    )

    runs = [
        summarize_run("bounded_001", bounded, temp_up_c=63.0),
        summarize_run("reactive_up61_down59_001", close, temp_up_c=63.0),
        summarize_run("reactive_up60_down58_001", far, temp_up_c=63.0),
    ]
    summary = build_budget_match_summary(runs)

    assert summary["groups"]["bounded"]["q4_time_sec_median"] == 60.0
    assert summary["groups"]["bounded"]["residence_blocked_count_median"] == 0
    assert summary["selected_candidate"]["group"] == "reactive_up61_down59"
    assert summary["selected_candidate"]["q4_time_diff_sec"] == -5.0


def test_csv_shape_from_summary_dict(tmp_path) -> None:
    run_dir = tmp_path / "run"
    write_run(run_dir)
    summary = summarize_run("bounded_001", run_dir, temp_up_c=63.0).as_dict()

    output = tmp_path / "rows.csv"
    with output.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(summary))
        writer.writeheader()
        writer.writerow(summary)

    saved = list(csv.DictReader(output.open(encoding="utf-8", newline="")))
    assert saved[0]["q4_time_sec"] == "30.0"
