from thermal_guardian.monitor import (
    FakeMonitor,
    MonitorSnapshot,
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
    assert _parse_get_throttled("bad output") == 0
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
