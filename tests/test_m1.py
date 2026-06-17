import csv
import json

import pytest
import requests

from thermal_guardian.config import RouterConfig
from thermal_guardian.m1 import (
    LoadRunRow,
    analyze_events,
    build_load_payload,
    main,
    parse_temperature_sequence,
    read_event_rows,
    run_analyze_events,
    run_fake_switch,
    run_load_run,
)
from thermal_guardian.router import CHAT_COMPLETIONS_PATH, PROMPT_ID_HEADER


def test_build_load_payload_is_openai_compatible_without_prompt_id() -> None:
    payload = build_load_payload(model="thermal-guardian", prompt="Say ok.", max_tokens=4)

    assert payload == {
        "model": "thermal-guardian",
        "messages": [{"role": "user", "content": "Say ok."}],
        "max_tokens": 4,
        "temperature": 0.0,
    }
    assert "prompt_id" not in payload


def test_load_run_posts_with_prompt_id_header_and_writes_tokens(tmp_path) -> None:
    class FakeResponse:
        status_code = 200

        def __init__(self, tokens: int, model: str) -> None:
            self.text = json.dumps(
                {
                    "model": model,
                    "usage": {"completion_tokens": tokens},
                }
            )
            self._tokens = tokens
            self._model = model

        def json(self):
            return {
                "model": self._model,
                "usage": {"completion_tokens": self._tokens},
            }

    class FakeSession:
        def __init__(self) -> None:
            self.calls = []

        def post(self, url, json, headers, timeout):
            self.calls.append(
                {
                    "url": url,
                    "json": json,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            return FakeResponse(tokens=len(self.calls) + 5, model=f"dry-run-q{len(self.calls)}")

    output = tmp_path / "load_requests.csv"
    session = FakeSession()

    rows = run_load_run(
        router_url="http://127.0.0.1:8080",
        output=output,
        duration_sec=0.0,
        request_count=2,
        timeout_sec=3.0,
        prompt="Say ok.",
        max_tokens=4,
        prompt_id_prefix="unit",
        session=session,
        now_func=lambda: 10.0,
    )

    assert [row.ok for row in rows] == [True, True]
    assert session.calls[0]["url"] == "http://127.0.0.1:8080" + CHAT_COMPLETIONS_PATH
    assert session.calls[0]["headers"][PROMPT_ID_HEADER] == "unit-000001"
    assert session.calls[0]["json"]["messages"][0]["content"] == "Say ok."
    assert "prompt_id" not in session.calls[0]["json"]

    with output.open(encoding="utf-8", newline="") as fp:
        parsed = list(csv.DictReader(fp))

    assert [row["prompt_id"] for row in parsed] == ["unit-000001", "unit-000002"]
    assert [row["tokens_out"] for row in parsed] == ["6", "7"]
    assert parsed[0]["model"] == "dry-run-q1"


def test_load_run_records_request_failures(tmp_path) -> None:
    class FailingSession:
        def post(self, url, json, headers, timeout):
            raise requests.Timeout("timed out")

    output = tmp_path / "load_requests.csv"

    rows = run_load_run(
        router_url="http://127.0.0.1:8080",
        output=output,
        duration_sec=0.0,
        request_count=1,
        session=FailingSession(),
    )

    assert rows[0].ok is False
    assert rows[0].status_code is None
    assert "timed out" in rows[0].detail

    with output.open(encoding="utf-8", newline="") as fp:
        parsed = list(csv.DictReader(fp))

    assert parsed[0]["ok"] == "false"
    assert parsed[0]["status_code"] == ""


def test_load_run_records_non_200_responses(tmp_path) -> None:
    class ErrorResponse:
        status_code = 503
        text = "backend unavailable"

        def json(self):
            return {"error": "backend unavailable"}

    class ErrorSession:
        def post(self, url, json, headers, timeout):
            return ErrorResponse()

    output = tmp_path / "load_requests.csv"

    rows = run_load_run(
        router_url="http://127.0.0.1:8080",
        output=output,
        duration_sec=0.0,
        request_count=1,
        session=ErrorSession(),
    )

    assert rows[0].ok is False
    assert rows[0].status_code == 503
    assert rows[0].detail == "backend unavailable"

    with output.open(encoding="utf-8", newline="") as fp:
        parsed = list(csv.DictReader(fp))

    assert parsed[0]["ok"] == "false"
    assert parsed[0]["status_code"] == "503"


def test_load_run_cli_exits_nonzero_on_failure(monkeypatch, tmp_path) -> None:
    def fake_run_load_run(**kwargs):
        return [
            LoadRunRow(1.0, "p1", True, 200, 1.0, 4, "q8", "ok"),
            LoadRunRow(2.0, "p2", False, 500, 1.0, 0, "", "failed"),
        ]

    monkeypatch.setattr("thermal_guardian.m1.run_load_run", fake_run_load_run)

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "load-run",
                "--router-url",
                "http://127.0.0.1:8080",
                "--output",
                str(tmp_path / "load.csv"),
            ]
        )

    assert exc.value.code == 1


def test_fake_switch_writes_switch_to_q4_and_q8(tmp_path) -> None:
    config = RouterConfig(
        log_dir=str(tmp_path / "unused"),
        temp_up_c=70.0,
        temp_down_c=60.0,
        min_switch_interval_sec=10.0,
    )

    decisions = run_fake_switch(
        config=config,
        log_dir=tmp_path,
        temps=[50.0, 72.0, 65.0, 58.0],
    )

    assert [decision.event.value for decision in decisions] == [
        "none",
        "switch_to_q4",
        "none",
        "switch_to_q8",
    ]
    text = (tmp_path / "events.csv").read_text(encoding="utf-8")
    assert "switch_to_q4" in text
    assert "switch_to_q8" in text


def test_analyze_events_passes_with_switch_and_no_oscillation(tmp_path) -> None:
    events = tmp_path / "events.csv"
    events.write_text(
        "\n".join(
            [
                "ts,temp_c,clock_hz,throttled_hex,state,event",
                "1.0,72.0,1500000000,0x0,q4,switch_to_q4",
                "20.0,58.0,1500000000,0x0,q8,switch_to_q8",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = run_analyze_events(
        events=events,
        config=RouterConfig(min_switch_interval_sec=10.0),
        output=tmp_path / "summary.json",
    )

    assert summary["ok"] is True
    assert summary["switch_to_q4_count"] == 1
    assert summary["switch_to_q8_count"] == 1
    assert summary["observed_min_switch_interval_sec"] == 19.0

    saved = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert saved["ok"] is True


def test_analyze_events_fails_without_switch() -> None:
    summary = analyze_events(
        [
            read_event_rows_from_dict(
                {
                    "ts": "1.0",
                    "temp_c": "40.0",
                    "clock_hz": "0",
                    "throttled_hex": "0x0",
                    "state": "q8",
                    "event": "none",
                }
            )
        ],
        min_switch_interval_sec=10.0,
    )

    assert summary["ok"] is False
    assert "no switch" in summary["failure_reasons"][0]


def test_analyze_events_cli_exits_nonzero_on_oscillation(tmp_path) -> None:
    events = tmp_path / "events.csv"
    events.write_text(
        "\n".join(
            [
                "ts,temp_c,clock_hz,throttled_hex,state,event",
                "1.0,72.0,1500000000,0x0,q4,switch_to_q4",
                "5.0,58.0,1500000000,0x0,q8,switch_to_q8",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"min_switch_interval_sec": 10.0}), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "analyze-events",
                "--events",
                str(events),
                "--config",
                str(config),
                "--output",
                str(tmp_path / "summary.json"),
            ]
        )

    assert exc.value.code == 1
    saved = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert saved["oscillation_detected"] is True


def test_read_event_rows_and_parse_temperature_sequence(tmp_path) -> None:
    events = tmp_path / "events.csv"
    events.write_text(
        "\n".join(
            [
                "ts,temp_c,clock_hz,throttled_hex,state,event",
                "1.0,72.5,1500000000,0x0,q4,switch_to_q4",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = read_event_rows(events)

    assert rows[0].ts == 1.0
    assert rows[0].temp_c == 72.5
    assert parse_temperature_sequence("40, 72.5, 58") == [40.0, 72.5, 58.0]


def read_event_rows_from_dict(row: dict[str, str]):
    from thermal_guardian.m1 import EventRow

    return EventRow(
        ts=float(row["ts"]),
        temp_c=float(row["temp_c"]),
        clock_hz=int(row["clock_hz"]),
        throttled_hex=row["throttled_hex"],
        state=row["state"],
        event=row["event"],
    )
