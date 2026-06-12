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


class VcgencmdMonitor:
    """Read Pi temperature, throttle flags, and ARM clock.

    On non-Pi hosts, unavailable `vcgencmd` calls return neutral values so unit
    tests and dry-runs can execute without Raspberry Pi hardware.
    """

    def snapshot(self) -> MonitorSnapshot:
        temp_output = _run_vcgencmd("measure_temp")
        throttle_output = _run_vcgencmd("get_throttled")
        clock_output = _run_vcgencmd("measure_clock", "arm")
        throttled_hex = _format_throttled_hex(_parse_get_throttled(throttle_output))
        return MonitorSnapshot(
            ts=time.time(),
            temp_c=_parse_measure_temp(temp_output) or 0.0,
            clock_hz=_parse_measure_clock(clock_output) or 0,
            throttled_hex=throttled_hex,
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
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _parse_measure_temp(output: str | None) -> float | None:
    if output is None:
        return None
    match = re.search(r"temp=([-+]?\d+(?:\.\d+)?)", output)
    if match is None:
        return None
    return float(match.group(1))


def _parse_get_throttled(output: str | None) -> int:
    if output is None:
        return 0
    match = re.search(r"throttled=(0x[0-9a-fA-F]+|\d+)", output)
    if match is None:
        return 0
    return int(match.group(1), 0)


def _parse_measure_clock(output: str | None) -> int | None:
    if output is None:
        return None
    match = re.search(r"frequency\(\d+\)=([0-9]+)", output)
    if match is None:
        return None
    return int(match.group(1))


def _format_throttled_hex(flags: int) -> str:
    return hex(max(0, int(flags)))
