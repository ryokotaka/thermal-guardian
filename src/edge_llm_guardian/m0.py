"""M0 helpers for launching and measuring two llama-server processes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import argparse
import csv
import json
import shlex
import subprocess
import sys
import time
from typing import Any

import psutil
import requests


RSS_FIELDS = ["ts", "name", "pid", "rss_bytes", "model_path", "host", "port"]
REQUIRED_INSTANCE_NAMES = ("q8", "q4")


@dataclass(frozen=True)
class LlamaServerInstance:
    name: str
    model_path: str
    host: str
    port: int
    ctx_size: int = 2048
    extra_args: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("instance name must not be empty")
        if not self.model_path:
            raise ValueError(f"{self.name}: model_path must not be empty")
        if self.port <= 0:
            raise ValueError(f"{self.name}: port must be positive")
        if self.ctx_size <= 0:
            raise ValueError(f"{self.name}: ctx_size must be positive")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LlamaServerInstance":
        return cls(
            name=str(data["name"]),
            model_path=str(data["model_path"]),
            host=str(data.get("host", "127.0.0.1")),
            port=int(data["port"]),
            ctx_size=int(data.get("ctx_size", 2048)),
            extra_args=[str(value) for value in data.get("extra_args", [])],
        )

    def pid_path(self, pid_dir: Path) -> Path:
        return pid_dir / f"{self.name}.pid"

    def log_path(self, log_dir: Path) -> Path:
        return log_dir / f"{self.name}.llama-server.log"

    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}/health"


@dataclass(frozen=True)
class M0Config:
    llama_server_bin: str
    pid_dir: str
    log_dir: str
    rss_output: str
    instances: list[LlamaServerInstance]

    def __post_init__(self) -> None:
        names = [instance.name for instance in self.instances]
        missing = [name for name in REQUIRED_INSTANCE_NAMES if name not in names]
        if missing:
            raise ValueError(f"missing required instances: {', '.join(missing)}")
        if len(set(names)) != len(names):
            raise ValueError("instance names must be unique")
        ports = [instance.port for instance in self.instances]
        if len(set(ports)) != len(ports):
            raise ValueError("instance ports must be unique")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "M0Config":
        return cls(
            llama_server_bin=str(data.get("llama_server_bin", "llama-server")),
            pid_dir=str(data.get("pid_dir", "run")),
            log_dir=str(data.get("log_dir", "logs")),
            rss_output=str(data.get("rss_output", "logs/server_rss.csv")),
            instances=[
                LlamaServerInstance.from_dict(instance)
                for instance in data.get("instances", [])
            ],
        )


@dataclass(frozen=True)
class HealthResult:
    name: str
    url: str
    ok: bool
    status_code: int | None
    detail: str


@dataclass(frozen=True)
class RssRow:
    ts: float
    name: str
    pid: int
    rss_bytes: int
    model_path: str
    host: str
    port: int

    def as_csv_row(self) -> dict[str, str]:
        return {
            "ts": f"{self.ts:.6f}",
            "name": self.name,
            "pid": str(self.pid),
            "rss_bytes": str(self.rss_bytes),
            "model_path": self.model_path,
            "host": self.host,
            "port": str(self.port),
        }


def load_m0_config(path: str | Path) -> M0Config:
    with Path(path).open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError("M0 config root must be a JSON object")
    return M0Config.from_dict(data)


def build_llama_server_command(
    config: M0Config,
    instance: LlamaServerInstance,
) -> list[str]:
    return [
        config.llama_server_bin,
        "-m",
        instance.model_path,
        "-c",
        str(instance.ctx_size),
        "--host",
        instance.host,
        "--port",
        str(instance.port),
        *instance.extra_args,
    ]


def validate_model_paths(config: M0Config) -> None:
    missing = [
        f"{instance.name}: {instance.model_path}"
        for instance in config.instances
        if not Path(instance.model_path).exists()
    ]
    if missing:
        joined = "\n".join(missing)
        raise FileNotFoundError(f"model files do not exist:\n{joined}")


def start_servers(config: M0Config, dry_run: bool = False) -> list[int]:
    if dry_run:
        for instance in config.instances:
            command = build_llama_server_command(config, instance)
            print(f"{instance.name}: {shlex.join(command)}")
        return []

    validate_model_paths(config)
    pid_dir = Path(config.pid_dir)
    log_dir = Path(config.log_dir)
    pid_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    pids: list[int] = []
    for instance in config.instances:
        command = build_llama_server_command(config, instance)
        log_file = instance.log_path(log_dir).open("a", encoding="utf-8")
        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        instance.pid_path(pid_dir).write_text(f"{process.pid}\n", encoding="utf-8")
        pids.append(process.pid)
        print(f"{instance.name}: started pid={process.pid} log={instance.log_path(log_dir)}")
    return pids


def check_health(
    config: M0Config,
    timeout_sec: float = 5.0,
) -> list[HealthResult]:
    results: list[HealthResult] = []
    for instance in config.instances:
        url = instance.health_url()
        try:
            response = requests.get(url, timeout=timeout_sec)
        except requests.RequestException as exc:
            results.append(
                HealthResult(
                    name=instance.name,
                    url=url,
                    ok=False,
                    status_code=None,
                    detail=str(exc),
                )
            )
            continue
        results.append(
            HealthResult(
                name=instance.name,
                url=url,
                ok=response.status_code == 200,
                status_code=response.status_code,
                detail=response.text.strip()[:200],
            )
        )
    return results


def read_pid(instance: LlamaServerInstance, pid_dir: str | Path) -> int:
    path = instance.pid_path(Path(pid_dir))
    return int(path.read_text(encoding="utf-8").strip())


def collect_rss_rows(config: M0Config, ts: float | None = None) -> list[RssRow]:
    timestamp = time.time() if ts is None else ts
    rows: list[RssRow] = []
    for instance in config.instances:
        pid = read_pid(instance, config.pid_dir)
        process = psutil.Process(pid)
        rows.append(
            RssRow(
                ts=timestamp,
                name=instance.name,
                pid=pid,
                rss_bytes=int(process.memory_info().rss),
                model_path=instance.model_path,
                host=instance.host,
                port=instance.port,
            )
        )
    return rows


def append_rss_rows(path: str | Path, rows: list[RssRow]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_header = not output.exists() or output.stat().st_size == 0
    with output.open("a", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=RSS_FIELDS)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv_row())


def run_rss(config: M0Config) -> list[RssRow]:
    rows = collect_rss_rows(config)
    append_rss_rows(config.rss_output, rows)
    for row in rows:
        print(f"{row.name}: pid={row.pid} rss_bytes={row.rss_bytes}")
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="M0 helpers for edge-llm-guardian.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start both llama-server processes.")
    start_parser.add_argument("--config", required=True)
    start_parser.add_argument("--dry-run", action="store_true")

    check_parser = subparsers.add_parser("check", help="Check both /health endpoints.")
    check_parser.add_argument("--config", required=True)
    check_parser.add_argument("--timeout-sec", type=float, default=5.0)

    rss_parser = subparsers.add_parser("rss", help="Append RSS readings for saved PIDs.")
    rss_parser.add_argument("--config", required=True)

    args = parser.parse_args(argv)
    config = load_m0_config(args.config)

    if args.command == "start":
        start_servers(config, dry_run=args.dry_run)
        return

    if args.command == "check":
        results = check_health(config, timeout_sec=args.timeout_sec)
        for result in results:
            status = result.status_code if result.status_code is not None else "error"
            print(f"{result.name}: ok={result.ok} status={status} url={result.url} detail={result.detail}")
        if not all(result.ok for result in results):
            raise SystemExit(1)
        return

    if args.command == "rss":
        run_rss(config)
        return

    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main(sys.argv[1:])
