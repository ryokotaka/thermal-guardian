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


def test_min_residence_blocks_leaving_current_target_too_soon() -> None:
    controller = ThermalController(
        ControllerConfig(
            temp_up_c=70.0,
            temp_down_c=60.0,
            min_switch_interval_sec=0.0,
            min_residence_sec=30.0,
        )
    )
    controller.evaluate(snapshot(100.0, 72.0))

    blocked = controller.evaluate(snapshot(120.0, 55.0))
    recovered = controller.evaluate(snapshot(130.0, 55.0))

    assert blocked.target is RouteTarget.Q4
    assert blocked.event is RouteEvent.RESIDENCE_BLOCKED
    assert recovered.target is RouteTarget.Q8
    assert recovered.event is RouteEvent.SWITCH_TO_Q8


def test_default_min_residence_preserves_reactive_switching() -> None:
    controller = ThermalController(
        ControllerConfig(
            temp_up_c=70.0,
            temp_down_c=60.0,
            min_switch_interval_sec=0.0,
        )
    )
    controller.evaluate(snapshot(100.0, 72.0))

    recovered = controller.evaluate(snapshot(101.0, 55.0))

    assert recovered.target is RouteTarget.Q8
    assert recovered.event is RouteEvent.SWITCH_TO_Q8


def test_look_ahead_switches_before_actual_threshold() -> None:
    controller = ThermalController(
        ControllerConfig(
            temp_up_c=70.0,
            temp_down_c=60.0,
            look_ahead_sec=5.0,
            slope_window=2,
            look_ahead_min_samples=2,
            look_ahead_max_delta_c=20.0,
        )
    )
    # Rising 1 C/s. Reactively it would only switch at 70 C; with a 5 s
    # look-ahead the prediction (67 + 1*5 = 72) crosses first, so it switches
    # while the actual reading is still well below 70.
    controller.evaluate(snapshot(0.0, 66.0))
    early = controller.evaluate(snapshot(1.0, 67.0))

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


def test_look_ahead_needs_configured_sample_count_before_predicting() -> None:
    controller = ThermalController(
        ControllerConfig(
            temp_up_c=70.0,
            temp_down_c=60.0,
            look_ahead_sec=10.0,
            slope_window=4,
            look_ahead_min_samples=4,
            look_ahead_max_delta_c=20.0,
        )
    )
    # Fewer than four samples so far: do not trust the slope yet.
    controller.evaluate(snapshot(0.0, 64.0))
    controller.evaluate(snapshot(1.0, 66.0))
    decision = controller.evaluate(snapshot(2.0, 68.0))

    assert decision.target is RouteTarget.Q8
    assert decision.event is RouteEvent.NONE


def test_look_ahead_clamps_large_temperature_spikes() -> None:
    controller = ThermalController(
        ControllerConfig(
            temp_up_c=70.0,
            temp_down_c=60.0,
            look_ahead_sec=30.0,
            slope_window=2,
            look_ahead_min_samples=2,
            look_ahead_max_delta_c=3.0,
        )
    )
    # A huge short-term slope would predict far above 70 C without bounding.
    # The clamp limits the effective temperature to 66 + 3 = 69, so no switch.
    controller.evaluate(snapshot(0.0, 60.0))
    decision = controller.evaluate(snapshot(1.0, 66.0))

    assert decision.target is RouteTarget.Q8
    assert decision.event is RouteEvent.NONE


def test_look_ahead_does_not_switch_from_cold_sensor_spike() -> None:
    controller = ThermalController(
        ControllerConfig(
            temp_up_c=63.0,
            temp_down_c=59.0,
            look_ahead_sec=30.0,
            slope_window=5,
            look_ahead_min_samples=5,
            look_ahead_max_delta_c=3.0,
        )
    )
    for ts, temp in [
        (0.0, 40.6),
        (2.0, 41.1),
        (4.0, 45.0),
        (6.0, 46.1),
        (8.0, 47.2),
    ]:
        decision = controller.evaluate(snapshot(ts, temp))

    assert decision.target is RouteTarget.Q8
    assert decision.event is RouteEvent.NONE


def test_look_ahead_uses_actual_temperature_for_q4_recovery() -> None:
    controller = ThermalController(
        ControllerConfig(
            temp_up_c=70.0,
            temp_down_c=60.0,
            min_switch_interval_sec=0.0,
            look_ahead_sec=30.0,
            slope_window=3,
            look_ahead_min_samples=3,
            look_ahead_max_delta_c=20.0,
        ),
        initial_target=RouteTarget.Q4,
    )
    controller.evaluate(snapshot(0.0, 68.0))
    controller.evaluate(snapshot(1.0, 65.0))
    still_q4 = controller.evaluate(snapshot(2.0, 62.0))
    recovered = controller.evaluate(snapshot(3.0, 60.0))

    assert still_q4.target is RouteTarget.Q4
    assert still_q4.event is RouteEvent.NONE
    assert recovered.target is RouteTarget.Q8
    assert recovered.event is RouteEvent.SWITCH_TO_Q8
