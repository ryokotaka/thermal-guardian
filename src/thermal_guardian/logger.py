"""CSV logging helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv

from edge_llm_guardian.controller import RouteDecision, RouteTarget


EVENT_FIELDS = ["ts", "temp_c", "clock_hz", "throttled_hex", "state", "event"]
REQUEST_FIELDS = ["ts", "target", "latency_ms", "tokens_out", "prompt_id"]


@dataclass(frozen=True)
class RequestLogRow:
    ts: float
    target: RouteTarget
    latency_ms: float
    tokens_out: int
    prompt_id: str


class CsvLogger:
    def __init__(self, log_dir: str | Path) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.log_dir / "events.csv"
        self.requests_path = self.log_dir / "requests.csv"
        self._ensure_header(self.events_path, EVENT_FIELDS)
        self._ensure_header(self.requests_path, REQUEST_FIELDS)

    def log_event(self, decision: RouteDecision) -> None:
        snapshot = decision.snapshot
        self._append(
            self.events_path,
            EVENT_FIELDS,
            {
                "ts": f"{snapshot.ts:.6f}",
                "temp_c": f"{snapshot.temp_c:.3f}",
                "clock_hz": str(snapshot.clock_hz),
                "throttled_hex": snapshot.throttled_hex,
                "state": decision.target.value,
                "event": decision.event.value,
            },
        )

    def log_request(self, row: RequestLogRow) -> None:
        self._append(
            self.requests_path,
            REQUEST_FIELDS,
            {
                "ts": f"{row.ts:.6f}",
                "target": row.target.value,
                "latency_ms": f"{row.latency_ms:.3f}",
                "tokens_out": str(row.tokens_out),
                "prompt_id": row.prompt_id,
            },
        )

    @staticmethod
    def _ensure_header(path: Path, fields: list[str]) -> None:
        if path.exists() and path.stat().st_size > 0:
            return
        with path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=fields)
            writer.writeheader()

    @staticmethod
    def _append(path: Path, fields: list[str], row: dict[str, str]) -> None:
        with path.open("a", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=fields)
            writer.writerow(row)
