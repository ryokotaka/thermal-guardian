import pytest

from thermal_guardian.config import RouterConfig


def test_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unknown config keys"):
        RouterConfig.from_dict({"unexpected": True})


def test_config_requires_hysteresis_gap() -> None:
    with pytest.raises(ValueError, match="temp_down_c"):
        RouterConfig(temp_up_c=60.0, temp_down_c=60.0)


def test_config_rejects_negative_min_residence() -> None:
    with pytest.raises(ValueError, match="min_residence_sec"):
        RouterConfig(min_residence_sec=-1.0)


def test_config_rejects_negative_look_ahead() -> None:
    with pytest.raises(ValueError, match="look_ahead_sec"):
        RouterConfig(look_ahead_sec=-1.0)


def test_config_requires_slope_window_of_at_least_two() -> None:
    with pytest.raises(ValueError, match="slope_window"):
        RouterConfig(slope_window=1)


def test_config_requires_look_ahead_min_samples_of_at_least_two() -> None:
    with pytest.raises(ValueError, match="look_ahead_min_samples"):
        RouterConfig(look_ahead_min_samples=1)


def test_config_requires_look_ahead_min_samples_within_slope_window() -> None:
    with pytest.raises(ValueError, match="look_ahead_min_samples"):
        RouterConfig(slope_window=4, look_ahead_min_samples=5)


def test_config_rejects_negative_look_ahead_max_delta() -> None:
    with pytest.raises(ValueError, match="look_ahead_max_delta_c"):
        RouterConfig(look_ahead_max_delta_c=-1.0)
