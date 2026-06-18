from thermal_guardian.controller import (
    ControllerConfig,
    RouteEvent,
    RouteTarget,
    ThermalController,
)
from thermal_guardian.monitor import MonitorSnapshot


def snapshot(ts: float, temp_c: float) -> MonitorSnapshot:
    return MonitorSnapshot(ts=ts, temp_c=temp_c, clock_hz=1_500_000_000, throttled_hex="0x0")


def test_starts_on_q8_under_cool_temperature() -> None:
    controller = ThermalController()

    decision = controller.evaluate(snapshot(1.0, 45.0))

    assert decision.target is RouteTarget.Q8
    assert decision.event is RouteEvent.NONE


def test_switches_to_q4_at_upper_threshold() -> None:
    controller = ThermalController()

    decision = controller.evaluate(snapshot(1.0, 70.0))

    assert decision.previous_target is RouteTarget.Q8
    assert decision.target is RouteTarget.Q4
    assert decision.event is RouteEvent.SWITCH_TO_Q4


def test_hysteresis_keeps_q4_until_lower_threshold() -> None:
    controller = ThermalController()
    controller.evaluate(snapshot(1.0, 72.0))

    middle = controller.evaluate(snapshot(20.0, 65.0))
    recovered = controller.evaluate(snapshot(30.0, 60.0))

    assert middle.target is RouteTarget.Q4
    assert middle.event is RouteEvent.NONE
    assert recovered.target is RouteTarget.Q8
    assert recovered.event is RouteEvent.SWITCH_TO_Q8


def test_cooldown_blocks_rapid_switch_back() -> None:
    controller = ThermalController(
        ControllerConfig(
            temp_up_c=70.0,
            temp_down_c=60.0,
            min_switch_interval_sec=10.0,
        )
    )
    controller.evaluate(snapshot(100.0, 72.0))

    blocked = controller.evaluate(snapshot(105.0, 55.0))
    recovered = controller.evaluate(snapshot(110.0, 55.0))

    assert blocked.target is RouteTarget.Q4
    assert blocked.event is RouteEvent.COOLDOWN_BLOCKED
    assert recovered.target is RouteTarget.Q8
    assert recovered.event is RouteEvent.SWITCH_TO_Q8


def test_look_ahead_switches_before_actual_threshold() -> None:
    controller = ThermalController(
        ControllerConfig(temp_up_c=70.0, temp_down_c=60.0, look_ahead_sec=5.0)
    )
    # Rising 2 C/s. Reactively it would only switch at 70 C; with a 5 s
    # look-ahead the prediction (62 + 2*5 = 72) crosses first, so it switches
    # while the actual reading is still well below 70.
    controller.evaluate(snapshot(0.0, 60.0))
    early = controller.evaluate(snapshot(1.0, 62.0))

    assert early.target is RouteTarget.Q4
    assert early.event is RouteEvent.SWITCH_TO_Q4
    assert early.snapshot.temp_c < 70.0


def test_reactive_default_waits_for_actual_threshold() -> None:
    controller = ThermalController(ControllerConfig(temp_up_c=70.0, temp_down_c=60.0))
    # Same rising sequence, but look_ahead_sec=0 (default): no early switch.
    controller.evaluate(snapshot(0.0, 60.0))
    controller.evaluate(snapshot(1.0, 62.0))
    decision = controller.evaluate(snapshot(2.0, 64.0))

    assert decision.target is RouteTarget.Q8
    assert decision.event is RouteEvent.NONE


def test_look_ahead_needs_two_samples_before_predicting() -> None:
    controller = ThermalController(
        ControllerConfig(temp_up_c=70.0, temp_down_c=60.0, look_ahead_sec=10.0)
    )
    # Only one sample so far: no slope yet, so no premature switch.
    decision = controller.evaluate(snapshot(0.0, 64.0))

    assert decision.target is RouteTarget.Q8
    assert decision.event is RouteEvent.NONE
