import json
from http.server import ThreadingHTTPServer
import threading

import pytest
import requests

from thermal_guardian.config import RouterConfig
from thermal_guardian.controller import ControllerConfig, RouteTarget, ThermalController
from thermal_guardian.logger import CsvLogger
from thermal_guardian.monitor import FakeMonitor, MonitorSnapshot, MonitorUnavailableError
from thermal_guardian.router import (
    PROMPT_ID_HEADER,
    RouterRuntime,
    RoutingHandler,
    _backend_url,
    _config_with_cli_overrides,
    _extract_prompt_id,
    _extract_tokens_out,
)


def test_prompt_id_can_come_from_metadata() -> None:
    assert _extract_prompt_id({}, headers={PROMPT_ID_HEADER: "header-p001"}) == "header-p001"
    assert _extract_prompt_id({"metadata": {"prompt_id": "abc"}}) == "abc"
    assert _extract_prompt_id({}) == "unknown"


def test_extract_tokens_out() -> None:
    assert _extract_tokens_out({"usage": {"completion_tokens": 12}}) == 12
    assert _extract_tokens_out({"usage": {"completion_tokens": -1}}) == 0
    assert _extract_tokens_out({}) == 0


def test_backend_url_uses_target() -> None:
    config = RouterConfig(q8_url="http://q8:8081", q4_url="http://q4:8082")

    assert _backend_url(config, RouteTarget.Q8, "/v1/chat/completions") == (
        "http://q8:8081/v1/chat/completions"
    )
    assert _backend_url(config, RouteTarget.Q4, "/v1/chat/completions") == (
        "http://q4:8082/v1/chat/completions"
    )


def test_cli_overrides_min_residence_without_mutating_config() -> None:
    class Args:
        dry_run = True
        fake_monitor = True
        min_residence_sec = 60.0

    original = RouterConfig(min_residence_sec=0.0, dry_run=False)

    updated = _config_with_cli_overrides(original, Args())

    assert original.min_residence_sec == 0.0
    assert original.dry_run is False
    assert updated.min_residence_sec == 60.0
    assert updated.dry_run is True
    assert original.fake_monitor is False
    assert updated.fake_monitor is True


def test_dry_run_runtime_routes_and_logs(tmp_path) -> None:
    config = RouterConfig(log_dir=str(tmp_path), dry_run=True, temp_up_c=70.0, temp_down_c=60.0)
    runtime = RouterRuntime(
        config,
        monitor=FakeMonitor([MonitorSnapshot(1.0, 72.0, 1_500_000_000, "0x0")]),
        controller=ThermalController(
            ControllerConfig(temp_up_c=70.0, temp_down_c=60.0, min_switch_interval_sec=0.0)
        ),
        logger=CsvLogger(tmp_path),
    )
    body = json.dumps({"messages": [], "metadata": {"prompt_id": "p001"}}).encode("utf-8")

    response = runtime.handle_chat_completion(body)
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["model"] == "dry-run-q4"
    assert "p001" in payload["choices"][0]["message"]["content"]
    assert "switch_to_q4" in (tmp_path / "events.csv").read_text(encoding="utf-8")
    assert "p001" in (tmp_path / "requests.csv").read_text(encoding="utf-8")


def test_runtime_forwards_to_selected_backend_and_logs_tokens(tmp_path) -> None:
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = json.dumps({"usage": {"completion_tokens": 9}}).encode("utf-8")

        def json(self):
            return {"usage": {"completion_tokens": 9}}

    class FakeSession:
        def __init__(self) -> None:
            self.calls = []

        def post(self, url, data, headers, timeout):
            self.calls.append(
                {
                    "url": url,
                    "data": data,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            return FakeResponse()

    session = FakeSession()
    config = RouterConfig(
        q8_url="http://127.0.0.1:8081",
        q4_url="http://127.0.0.1:8082",
        log_dir=str(tmp_path),
        temp_up_c=70.0,
        temp_down_c=60.0,
        min_switch_interval_sec=0.0,
    )
    runtime = RouterRuntime(
        config,
        monitor=FakeMonitor([MonitorSnapshot(1.0, 72.0, 1_500_000_000, "0x0")]),
        logger=CsvLogger(tmp_path),
        session=session,
    )
    body = json.dumps({"messages": [], "prompt_id": "p-forward"}).encode("utf-8")

    response = runtime.handle_chat_completion(body)

    assert response.status_code == 200
    assert session.calls[0]["url"] == "http://127.0.0.1:8082/v1/chat/completions"
    assert session.calls[0]["data"] == body
    assert "p-forward" in (tmp_path / "requests.csv").read_text(encoding="utf-8")
    assert ",9," in (tmp_path / "requests.csv").read_text(encoding="utf-8")


def test_runtime_logs_prompt_id_header_without_mutating_body(tmp_path) -> None:
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = json.dumps({"usage": {"completion_tokens": 3}}).encode("utf-8")

        def json(self):
            return {"usage": {"completion_tokens": 3}}

    class FakeSession:
        def __init__(self) -> None:
            self.calls = []

        def post(self, url, data, headers, timeout):
            self.calls.append(
                {
                    "url": url,
                    "data": data,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            return FakeResponse()

    session = FakeSession()
    config = RouterConfig(
        q8_url="http://127.0.0.1:8081",
        q4_url="http://127.0.0.1:8082",
        log_dir=str(tmp_path),
        min_switch_interval_sec=0.0,
    )
    runtime = RouterRuntime(
        config,
        monitor=FakeMonitor([MonitorSnapshot(1.0, 40.0, 1_500_000_000, "0x0")]),
        logger=CsvLogger(tmp_path),
        session=session,
    )
    body = json.dumps({"model": "thermal-guardian", "messages": []}).encode("utf-8")

    response = runtime.handle_chat_completion(
        body,
        headers={PROMPT_ID_HEADER: "header-p-forward"},
    )

    assert response.status_code == 200
    assert session.calls[0]["data"] == body
    assert "prompt_id" not in json.loads(session.calls[0]["data"].decode("utf-8"))
    assert "header-p-forward" in (tmp_path / "requests.csv").read_text(encoding="utf-8")


def test_fake_monitor_is_an_explicit_local_option(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("thermal_guardian.monitor._run_vcgencmd", lambda *args: None)
    real_runtime = RouterRuntime(RouterConfig(log_dir=str(tmp_path / "real"), dry_run=True))
    with pytest.raises(MonitorUnavailableError):
        real_runtime.handle_chat_completion(b'{"messages": []}')

    demo_runtime = RouterRuntime(
        RouterConfig(log_dir=str(tmp_path / "demo"), dry_run=True, fake_monitor=True)
    )
    assert demo_runtime.handle_chat_completion(b'{"messages": []}').status_code == 200


def test_telemetry_failure_invalidates_cached_route_and_returns_503(tmp_path) -> None:
    class RecoverableMonitor:
        failed = False

        def snapshot(self):
            if self.failed:
                raise MonitorUnavailableError("temperature unavailable")
            return MonitorSnapshot(1.0, 40.0, 1_500_000_000, "0x0")

    monitor = RecoverableMonitor()
    runtime = RouterRuntime(
        RouterConfig(log_dir=str(tmp_path), dry_run=True, monitor_interval_sec=3600),
        monitor=monitor,
    )
    runtime.sample_controller()
    monitor.failed = True
    with pytest.raises(MonitorUnavailableError):
        runtime.sample_controller()

    class Handler(RoutingHandler):
        pass

    Handler.runtime = runtime
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        response = requests.post(url, json={"messages": []}, timeout=5)
        assert response.status_code == 503
        assert "temperature unavailable" in response.json()["error"]
        assert len((tmp_path / "requests.csv").read_text().splitlines()) == 1

        monitor.failed = False
        assert requests.post(url, json={"messages": []}, timeout=5).status_code == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_background_monitor_retries_after_telemetry_failure(tmp_path) -> None:
    recovered = threading.Event()

    class RecoveringMonitor:
        calls = 0

        def snapshot(self):
            self.calls += 1
            if self.calls == 1:
                raise MonitorUnavailableError("temperature unavailable")
            recovered.set()
            return MonitorSnapshot(1.0, 40.0, 1_500_000_000, "0x0")

    runtime = RouterRuntime(
        RouterConfig(log_dir=str(tmp_path), dry_run=True, monitor_interval_sec=0.01),
        monitor=RecoveringMonitor(),
    )
    runtime.start_background_monitor()
    try:
        assert recovered.wait(timeout=2), "background sampling stopped after the failed read"
        assert runtime.current_decision().target is RouteTarget.Q8
    finally:
        runtime.stop_background_monitor()
