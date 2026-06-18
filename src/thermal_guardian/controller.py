"""Two-state thermal routing controller."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from thermal_guardian.monitor import MonitorSnapshot


class RouteTarget(str, Enum):
    Q8 = "q8"
    Q4 = "q4"


class RouteEvent(str, Enum):
    NONE = "none"
    SWITCH_TO_Q4 = "switch_to_q4"
    SWITCH_TO_Q8 = "switch_to_q8"
    COOLDOWN_BLOCKED = "cooldown_blocked"


@dataclass(frozen=True)
class ControllerConfig:
    temp_up_c: float = 70.0
    temp_down_c: float = 60.0
    min_switch_interval_sec: float = 10.0
    # Look-ahead control: when > 0, switch on the temperature predicted
    # look_ahead_sec into the future (from the recent slope) instead of the
    # current reading. 0.0 keeps the original reactive behavior exactly.
    look_ahead_sec: float = 0.0
    slope_window: int = 5

    def __post_init__(self) -> None:
        if self.temp_down_c >= self.temp_up_c:
            raise ValueError("temp_down_c must be lower than temp_up_c")
        if self.min_switch_interval_sec < 0:
            raise ValueError("min_switch_interval_sec must be non-negative")
        if self.look_ahead_sec < 0:
            raise ValueError("look_ahead_sec must be non-negative")
        if self.slope_window < 2:
            raise ValueError("slope_window must be at least 2")


@dataclass(frozen=True)
class RouteDecision:
    target: RouteTarget
    previous_target: RouteTarget
    event: RouteEvent
    reason: str
    snapshot: MonitorSnapshot


class ThermalController:
    """Choose Q8 or Q4 from temperature using hysteresis and cooldown."""

    def __init__(
        self,
        config: ControllerConfig | None = None,
        initial_target: RouteTarget = RouteTarget.Q8,
    ) -> None:
        self.config = config or ControllerConfig()
        self._target = initial_target
        self._last_switch_ts: float | None = None
        self._history: list[tuple[float, float]] = []

    @property
    def target(self) -> RouteTarget:
        return self._target

    def evaluate(self, snapshot: MonitorSnapshot) -> RouteDecision:
        previous = self._target
        self._record(snapshot)
        effective_temp = self._decision_temp(snapshot)
        temp_desc = self._describe_temp(snapshot.temp_c, effective_temp)

        if self._target is RouteTarget.Q8 and effective_temp >= self.config.temp_up_c:
            return self._maybe_switch(
                snapshot=snapshot,
                next_target=RouteTarget.Q4,
                event=RouteEvent.SWITCH_TO_Q4,
                reason=f"{temp_desc} >= temp_up_c={self.config.temp_up_c:.1f}",
            )

        if self._target is RouteTarget.Q4 and effective_temp <= self.config.temp_down_c:
            return self._maybe_switch(
                snapshot=snapshot,
                next_target=RouteTarget.Q8,
                event=RouteEvent.SWITCH_TO_Q8,
                reason=f"{temp_desc} <= temp_down_c={self.config.temp_down_c:.1f}",
            )

        return RouteDecision(
            target=self._target,
            previous_target=previous,
            event=RouteEvent.NONE,
            reason="within hysteresis band",
            snapshot=snapshot,
        )

    def _record(self, snapshot: MonitorSnapshot) -> None:
        self._history.append((snapshot.ts, snapshot.temp_c))
        if len(self._history) > self.config.slope_window:
            self._history = self._history[-self.config.slope_window :]

    def _decision_temp(self, snapshot: MonitorSnapshot) -> float:
        """Temperature used for the switch test: actual, or look-ahead prediction."""
        if self.config.look_ahead_sec <= 0.0 or len(self._history) < 2:
            return snapshot.temp_c
        slope = _temp_slope_per_sec(self._history)
        return snapshot.temp_c + slope * self.config.look_ahead_sec

    def _describe_temp(self, actual: float, effective: float) -> str:
        if self.config.look_ahead_sec > 0.0 and effective != actual:
            return (
                f"temp_c={actual:.1f} "
                f"pred={effective:.1f}@{self.config.look_ahead_sec:.0f}s"
            )
        return f"temp_c={actual:.1f}"

    def _maybe_switch(
        self,
        *,
        snapshot: MonitorSnapshot,
        next_target: RouteTarget,
        event: RouteEvent,
        reason: str,
    ) -> RouteDecision:
        previous = self._target
        if not self._cooldown_elapsed(snapshot.ts):
            return RouteDecision(
                target=self._target,
                previous_target=previous,
                event=RouteEvent.COOLDOWN_BLOCKED,
                reason=(
                    f"{reason}; cooldown remaining after last switch at "
                    f"{self._last_switch_ts:.3f}"
                ),
                snapshot=snapshot,
            )

        self._target = next_target
        self._last_switch_ts = snapshot.ts
        return RouteDecision(
            target=self._target,
            previous_target=previous,
            event=event,
            reason=reason,
            snapshot=snapshot,
        )

    def _cooldown_elapsed(self, ts: float) -> bool:
        if self._last_switch_ts is None:
            return True
        elapsed = max(0.0, ts - self._last_switch_ts)
        return elapsed >= self.config.min_switch_interval_sec


def _temp_slope_per_sec(history: list[tuple[float, float]]) -> float:
    """Least-squares slope of temperature over time, in degrees C per second."""
    n = len(history)
    if n < 2:
        return 0.0
    mean_t = sum(t for t, _ in history) / n
    mean_v = sum(v for _, v in history) / n
    denominator = sum((t - mean_t) ** 2 for t, _ in history)
    if denominator == 0.0:
        return 0.0
    numerator = sum((t - mean_t) * (v - mean_v) for t, v in history)
    return numerator / denominator
