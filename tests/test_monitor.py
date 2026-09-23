import pytest

from thermal_guardian.monitor import (
    FakeMonitor,
    MonitorSnapshot,
    MonitorUnavailableError,
    VcgencmdMonitor,
    _format_throttled_hex,
    _parse_get_throttled,
    _parse_measure_clock,
    _parse_measure_temp,
)


def test_parse_measure_temp() -> None:
    assert _parse_measure_temp("temp=42.3'C") == 42.3
    assert _parse_measure_temp("bad output") is None
    assert _parse_measure_temp(None) is None


def test_parse_get_throttled_hex_and_decimal() -> None:
    assert _parse_get_throttled("throttled=0x50005") == 0x50005
    assert _parse_get_throttled("throttled=8") == 8
    assert _parse_get_throttled("throttled=08") == 8
    assert _parse_get_throttled("bad output") is None
    assert _parse_get_throttled("throttled=0xinvalid") is None
    assert _parse_get_throttled(None) is None
    assert _format_throttled_hex(0xE0008) == "0xe0008"


def test_parse_measure_clock() -> None:
    assert _parse_measure_clock("frequency(48)=1500000000") == 1_500_000_000
    assert _parse_measure_clock("bad output") is None


def test_fake_monitor_replays_then_holds_last_snapshot() -> None:
    monitor = FakeMonitor(
        [
            MonitorSnapshot(1.0, 40.0, 1, "0x0"),
            MonitorSnapshot(2.0, 80.0, 2, "0x8"),
        ]
    )

    assert monitor.snapshot().temp_c == 40.0
    assert monitor.snapshot().temp_c == 80.0
    assert monitor.snapshot().temp_c == 80.0


@pytest.mark.parametrize("failed_command", ["measure_temp", "get_throttled", "measure_clock"])
@pytest.mark.parametrize("failed_output", [None, "unavailable"])
def test_monitor_rejects_missing_or_malformed_telemetry(monkeypatch, failed_command, failed_output) -> None:
    outputs = {
        "measure_temp": "temp=42.3'C",
        "get_throttled": "throttled=0x0",
        "measure_clock": "frequency(0)=2400000000",
    }
    outputs[failed_command] = failed_output
    monkeypatch.setattr("thermal_guardian.monitor._run_vcgencmd", lambda command, *args: outputs[command])

    with pytest.raises(MonitorUnavailableError, match="telemetry unavailable"):
        VcgencmdMonitor().snapshot()


def test_monitor_preserves_real_zero_values(monkeypatch) -> None:
    outputs = {
        "measure_temp": "temp=0.0'C",
        "get_throttled": "throttled=0x0",
        "measure_clock": "frequency(0)=0",
    }
    monkeypatch.setattr("thermal_guardian.monitor._run_vcgencmd", lambda command, *args: outputs[command])

    snapshot = VcgencmdMonitor().snapshot()

    assert snapshot.temp_c == 0.0
    assert snapshot.throttled_hex == "0x0"
    assert snapshot.clock_hz == 0
