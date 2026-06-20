"""OpenAI-compatible routing server."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import json
import threading
import time
from typing import Any, Mapping
from urllib.parse import urljoin

import requests

from thermal_guardian.config import RouterConfig, load_config
from thermal_guardian.controller import ControllerConfig, RouteTarget, ThermalController
from thermal_guardian.logger import CsvLogger, RequestLogRow
from thermal_guardian.monitor import VcgencmdMonitor


CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
PROMPT_ID_HEADER = "X-Edge-Prompt-Id"


@dataclass(frozen=True)
class RouterResponse:
    status_code: int
    body: bytes
    headers: dict[str, str]


class RouterRuntime:
    def __init__(
        self,
        config: RouterConfig,
        *,
        monitor: Any | None = None,
        controller: ThermalController | None = None,
        logger: CsvLogger | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.monitor = monitor or VcgencmdMonitor()
        self.controller = controller or ThermalController(
            ControllerConfig(
                temp_up_c=config.temp_up_c,
                temp_down_c=config.temp_down_c,
                min_switch_interval_sec=config.min_switch_interval_sec,
                min_residence_sec=config.min_residence_sec,
                look_ahead_sec=config.look_ahead_sec,
                slope_window=config.slope_window,
                look_ahead_min_samples=config.look_ahead_min_samples,
                look_ahead_min_temp_c=config.look_ahead_min_temp_c,
                look_ahead_max_delta_c=config.look_ahead_max_delta_c,
            )
        )
        self.logger = logger or CsvLogger(config.log_dir)
        self.session = session or requests.Session()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._last_decision: Any | None = None
        self._last_sample_monotonic: float | None = None

    def handle_chat_completion(
        self,
        body: bytes,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RouterResponse:
        request_json = _parse_json_body(body)
        prompt_id = _extract_prompt_id(request_json, headers=headers)
        decision = self.current_decision()

        start = time.perf_counter()
        if self.config.dry_run:
            response = _dry_run_response(decision.target, prompt_id)
            latency_ms = (time.perf_counter() - start) * 1000.0
            self.logger.log_request(
                RequestLogRow(
                    ts=time.time(),
                    target=decision.target,
                    latency_ms=latency_ms,
                    tokens_out=_extract_tokens_out(response),
                    prompt_id=prompt_id,
                )
            )
            return RouterResponse(
                status_code=HTTPStatus.OK,
                body=json.dumps(response).encode("utf-8"),
                headers={"content-type": "application/json"},
            )

        target_url = _backend_url(self.config, decision.target, CHAT_COMPLETIONS_PATH)
        upstream = self.session.post(
            target_url,
            data=body,
            headers={"content-type": "application/json"},
            timeout=self.config.request_timeout_sec,
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        tokens_out = _extract_tokens_out(_response_json_or_empty(upstream))
        self.logger.log_request(
            RequestLogRow(
                ts=time.time(),
                target=decision.target,
                latency_ms=latency_ms,
                tokens_out=tokens_out,
                prompt_id=prompt_id,
            )
        )
        return RouterResponse(
            status_code=upstream.status_code,
            body=upstream.content,
            headers={"content-type": upstream.headers.get("content-type", "application/json")},
        )

    def start_background_monitor(self) -> None:
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="thermal-guardian-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def stop_background_monitor(self) -> None:
        self._stop_event.set()
        thread = self._monitor_thread
        if thread is not None:
            thread.join(timeout=self.config.monitor_interval_sec + 1.0)
        self._monitor_thread = None

    def current_decision(self) -> Any:
        with self._lock:
            now = time.monotonic()
            if (
                self._last_decision is None
                or self._last_sample_monotonic is None
                or now - self._last_sample_monotonic >= self.config.monitor_interval_sec
            ):
                return self._sample_controller_locked(now)
            return self._last_decision

    def sample_controller(self) -> Any:
        with self._lock:
            return self._sample_controller_locked(time.monotonic())

    def _sample_controller_locked(self, sampled_at_monotonic: float) -> Any:
        snapshot = self.monitor.snapshot()
        decision = self.controller.evaluate(snapshot)
        self.logger.log_event(decision)
        self._last_decision = decision
        self._last_sample_monotonic = sampled_at_monotonic
        return decision

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            self.sample_controller()
            if self._stop_event.wait(self.config.monitor_interval_sec):
                break


class RoutingHandler(BaseHTTPRequestHandler):
    runtime: RouterRuntime

    def do_POST(self) -> None:
        if self.path != CHAT_COMPLETIONS_PATH:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        try:
            response = self.runtime.handle_chat_completion(body, headers=self.headers)
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except requests.RequestException as exc:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
            return

        self.send_response(response.status_code)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(response.body)))
        self.end_headers()
        self.wfile.write(response.body)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(config: RouterConfig) -> None:
    runtime = RouterRuntime(config)
    runtime.start_background_monitor()

    class Handler(RoutingHandler):
        pass

    Handler.runtime = runtime
    server = ThreadingHTTPServer((config.listen_host, config.listen_port), Handler)
    print(f"thermal-guardian listening on http://{config.listen_host}:{config.listen_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("thermal-guardian stopping")
    finally:
        runtime.stop_background_monitor()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Thermal Guardian routing server.")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-residence-sec", type=float, default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    config = _config_with_cli_overrides(config, args)
    run_server(config)


def _config_with_cli_overrides(config: RouterConfig, args: argparse.Namespace) -> RouterConfig:
    data = dict(config.__dict__)
    if args.dry_run:
        data["dry_run"] = True
    if args.min_residence_sec is not None:
        data["min_residence_sec"] = args.min_residence_sec
    return RouterConfig.from_dict(data)


def _parse_json_body(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("request body must be valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("request body must be a JSON object")
    return data


def _extract_prompt_id(
    data: dict[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
) -> str:
    if headers is not None:
        header_value = headers.get(PROMPT_ID_HEADER)
        if isinstance(header_value, str) and header_value.strip():
            return header_value.strip()
    direct = data.get("prompt_id")
    if isinstance(direct, str) and direct:
        return direct
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        prompt_id = metadata.get("prompt_id")
        if isinstance(prompt_id, str) and prompt_id:
            return prompt_id
    return "unknown"


def _extract_tokens_out(data: dict[str, Any]) -> int:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return 0
    value = usage.get("completion_tokens")
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _response_json_or_empty(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _backend_url(config: RouterConfig, target: RouteTarget, path: str) -> str:
    base = config.q8_url if target is RouteTarget.Q8 else config.q4_url
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def _dry_run_response(target: RouteTarget, prompt_id: str) -> dict[str, Any]:
    content = f"dry-run routed to {target.value} for prompt_id={prompt_id}"
    return {
        "id": f"dry-run-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": f"dry-run-{target.value}",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(content.split()),
            "total_tokens": len(content.split()),
        },
    }


if __name__ == "__main__":
    main()
