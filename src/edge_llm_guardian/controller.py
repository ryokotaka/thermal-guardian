"""Two-state thermal routing controller."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from edge_llm_guardian.monitor import MonitorSnapshot


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

    def __post_init__(self) -> None:
        if self.temp_down_c >= self.temp_up_c:
            raise ValueError("temp_down_c must be lower than temp_up_c")
        if self.min_switch_interval_sec < 0:
            raise ValueError("min_switch_interval_sec must be non-negative")


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

    @property
    def target(self) -> RouteTarget:
        return self._target

    def evaluate(self, snapshot: MonitorSnapshot) -> RouteDecision:
        previous = self._target

        if self._target is RouteTarget.Q8 and snapshot.temp_c >= self.config.temp_up_c:
            return self._maybe_switch(
                snapshot=snapshot,
                next_target=RouteTarget.Q4,
                event=RouteEvent.SWITCH_TO_Q4,
                reason=(
                    f"temp_c={snapshot.temp_c:.1f} >= "
                    f"temp_up_c={self.config.temp_up_c:.1f}"
                ),
            )

        if self._target is RouteTarget.Q4 and snapshot.temp_c <= self.config.temp_down_c:
            return self._maybe_switch(
                snapshot=snapshot,
                next_target=RouteTarget.Q8,
                event=RouteEvent.SWITCH_TO_Q8,
                reason=(
                    f"temp_c={snapshot.temp_c:.1f} <= "
                    f"temp_down_c={self.config.temp_down_c:.1f}"
                ),
            )

        return RouteDecision(
            target=self._target,
            previous_target=previous,
            event=RouteEvent.NONE,
            reason="within hysteresis band",
            snapshot=snapshot,
        )

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
