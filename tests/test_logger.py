import csv

from thermal_guardian.controller import RouteEvent, RouteTarget, RouteDecision
from thermal_guardian.logger import CsvLogger, EVENT_FIELDS, REQUEST_FIELDS, RequestLogRow
from thermal_guardian.monitor import MonitorSnapshot


def test_csv_logger_writes_expected_headers_and_rows(tmp_path) -> None:
    logger = CsvLogger(tmp_path)
    decision = RouteDecision(
        target=RouteTarget.Q4,
        previous_target=RouteTarget.Q8,
        event=RouteEvent.SWITCH_TO_Q4,
        reason="hot",
        snapshot=MonitorSnapshot(1.25, 72.0, 1_500_000_000, "0x0"),
    )

    logger.log_event(decision)
    logger.log_request(
        RequestLogRow(
            ts=2.5,
            target=RouteTarget.Q4,
            latency_ms=123.4567,
            tokens_out=42,
            prompt_id="p001",
        )
    )

    with (tmp_path / "events.csv").open(newline="", encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    assert rows[0].keys() == set(EVENT_FIELDS)
    assert rows[0]["state"] == "q4"
    assert rows[0]["event"] == "switch_to_q4"

    with (tmp_path / "requests.csv").open(newline="", encoding="utf-8") as fp:
        request_rows = list(csv.DictReader(fp))
    assert request_rows[0].keys() == set(REQUEST_FIELDS)
    assert request_rows[0]["target"] == "q4"
    assert request_rows[0]["tokens_out"] == "42"
