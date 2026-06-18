"""Configuration loading for the router and controller."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
import json
from typing import Any


@dataclass(frozen=True)
class RouterConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 8080
    q8_url: str = "http://127.0.0.1:8081"
    q4_url: str = "http://127.0.0.1:8082"
    monitor_interval_sec: float = 2.0
    temp_up_c: float = 70.0
    temp_down_c: float = 60.0
    min_switch_interval_sec: float = 10.0
    look_ahead_sec: float = 0.0
    slope_window: int = 5
    request_timeout_sec: float = 120.0
    log_dir: str = "logs"
    dry_run: bool = False

    def __post_init__(self) -> None:
        if self.listen_port <= 0:
            raise ValueError("listen_port must be positive")
        if self.monitor_interval_sec <= 0:
            raise ValueError("monitor_interval_sec must be positive")
        if self.temp_down_c >= self.temp_up_c:
            raise ValueError("temp_down_c must be lower than temp_up_c")
        if self.min_switch_interval_sec < 0:
            raise ValueError("min_switch_interval_sec must be non-negative")
        if self.look_ahead_sec < 0:
            raise ValueError("look_ahead_sec must be non-negative")
        if self.slope_window < 2:
            raise ValueError("slope_window must be at least 2")
        if self.request_timeout_sec <= 0:
            raise ValueError("request_timeout_sec must be positive")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RouterConfig":
        names = {field.name for field in fields(cls)}
        unknown = sorted(set(data) - names)
        if unknown:
            joined = ", ".join(unknown)
            raise ValueError(f"unknown config keys: {joined}")
        return cls(**data)


def load_config(path: str | Path | None = None) -> RouterConfig:
    """Load JSON config, returning defaults when no path is provided."""
    if path is None:
        return RouterConfig()
    with Path(path).open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")
    return RouterConfig.from_dict(data)
