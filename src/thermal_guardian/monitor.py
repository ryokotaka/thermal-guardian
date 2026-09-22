"""Raspberry Pi thermal monitor helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re
import subprocess
import time


@dataclass(frozen=True)
class MonitorSnapshot:
    ts: float
    temp_c: float
    clock_hz: int
    throttled_hex: str

    @property
    def throttled_flags(self) -> int:
        return int(self.throttled_hex, 16)


class MonitorUnavailableError(RuntimeError):
    """Required Raspberry Pi telemetry could not be read."""


class VcgencmdMonitor:
    """Read Pi telemetry; never substitute healthy values for a failed read."""

    def snapshot(self) -> MonitorSnapshot:
        temp_output = _run_vcgencmd("measure_temp")
        throttle_output = _run_vcgencmd("get_throttled")
        clock_output = _run_vcgencmd("measure_clock", "arm")
        temp_c = _parse_measure_temp(temp_output)
        flags = _parse_get_throttled(throttle_output)
        clock_hz = _parse_measure_clock(clock_output)
        missing = [
            name
            for name, value in (("temperature", temp_c), ("throttle flags", flags), ("ARM clock", clock_hz))
            if value is None
        ]
        if missing:
            raise MonitorUnavailableError(
                f"vcgencmd telemetry unavailable: {', '.join(missing)}"
            )
        assert temp_c is not None and flags is not None and clock_hz is not None
        return MonitorSnapshot(
            ts=time.time(),
            temp_c=temp_c,
            clock_hz=clock_hz,
            throttled_hex=_format_throttled_hex(flags),
        )


class FakeMonitor:
    """Deterministic monitor for local dry-runs and unit tests."""

    def __init__(self, snapshots: Sequence[MonitorSnapshot] | None = None) -> None:
        self._snapshots = list(snapshots or [MonitorSnapshot(time.time(), 40.0, 0, "0x0")])
        if not self._snapshots:
            raise ValueError("snapshots must not be empty")
        self._index = 0

    def snapshot(self) -> MonitorSnapshot:
        if self._index < len(self._snapshots):
            snapshot = self._snapshots[self._index]
            self._index += 1
            return snapshot
        return self._snapshots[-1]


def _run_vcgencmd(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["vcgencmd", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _parse_measure_temp(output: str | None) -> float | None:
    if output is None:
        return None
    match = re.fullmatch(r"temp=([-+]?\d+(?:\.\d+)?)'C", output.strip())
    if match is None:
        return None
    return float(match.group(1))


def _parse_get_throttled(output: str | None) -> int | None:
    if output is None:
        return None
    match = re.fullmatch(r"throttled=(0x[0-9a-fA-F]+|\d+)", output.strip())
    if match is None:
        return None
    value = match.group(1)
    return int(value, 16 if value.startswith("0x") else 10)


def _parse_measure_clock(output: str | None) -> int | None:
    if output is None:
        return None
    match = re.fullmatch(r"frequency\(\d+\)=([0-9]+)", output.strip())
    if match is None:
        return None
    return int(match.group(1))


def _format_throttled_hex(flags: int) -> str:
    return hex(max(0, int(flags)))
