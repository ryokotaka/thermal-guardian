import pytest

from thermal_guardian.config import RouterConfig


def test_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unknown config keys"):
        RouterConfig.from_dict({"unexpected": True})


def test_config_requires_hysteresis_gap() -> None:
    with pytest.raises(ValueError, match="temp_down_c"):
        RouterConfig(temp_up_c=60.0, temp_down_c=60.0)


def test_config_rejects_negative_look_ahead() -> None:
    with pytest.raises(ValueError, match="look_ahead_sec"):
        RouterConfig(look_ahead_sec=-1.0)


def test_config_requires_slope_window_of_at_least_two() -> None:
    with pytest.raises(ValueError, match="slope_window"):
        RouterConfig(slope_window=1)
