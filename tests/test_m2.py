import csv
import json

import pytest
import requests

from thermal_guardian.m2 import (
    M2Config,
    build_power_summary,
    build_chat_payload,
    load_m2_config,
    main,
    plot_run,
    run_m2,
    summarize_runs,
)
from thermal_guardian.monitor import FakeMonitor, MonitorSnapshot
from thermal_guardian.router import CHAT_COMPLETIONS_PATH, PROMPT_ID_HEADER


def test_m2_config_selects_expected_urls_and_payload_has_no_prompt_id() -> None:
    config = M2Config(
        q8_url="http://q8:8081",
        q4_url="http://q4:8082",
        router_url="http://router:8080",
        prompt="Say ok.",
        max_tokens=4,
    )

    assert config.url_for_mode("q8_fixed") == "http://q8:8081"
    assert config.url_for_mode("q4_fixed") == "http://q4:8082"
    assert config.url_for_mode("controller") == "http://router:8080"
    assert build_chat_payload(config) == {
        "model": "thermal-guardian",
        "messages": [{"role": "user", "content": "Say ok."}],
        "max_tokens": 4,
        "temperature": 0.0,
    }
    assert "prompt_id" not in build_chat_payload(config)


def test_load_m2_config_rejects_unknown_keys(tmp_path) -> None:
    config_path = tmp_path / "m2.json"
    config_path.write_text(json.dumps({"q8_url": "http://q8", "unknown": True}), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown M2 config keys"):
        load_m2_config(config_path)


def test_run_m2_posts_to_fixed_mode_url_and_writes_csvs(tmp_path) -> None:
    class FakeResponse:
        status_code = 200

        def __init__(self, tokens: int, model: str) -> None:
            self.text = json.dumps({"model": model, "usage": {"completion_tokens": tokens}})
            self._tokens = tokens
            self._model = model

        def json(self):
            return {"model": self._model, "usage": {"completion_tokens": self._tokens}}

    class FakeSession:
        def __init__(self) -> None:
            self.calls = []

        def post(self, url, json, headers, timeout):
            self.calls.append(
                {
                    "url": url,
                    "json": json,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            return FakeResponse(tokens=5 + len(self.calls), model="fake-q4")

    session = FakeSession()
    monitor = FakeMonitor(
        [
            MonitorSnapshot(10.0, 45.0, 1_500_000_000, "0x0"),
            MonitorSnapshot(11.0, 46.0, 1_400_000_000, "0x0"),
            MonitorSnapshot(12.0, 47.0, 1_300_000_000, "0x0"),
        ]
    )
    config = M2Config(
        q4_url="http://127.0.0.1:8082",
        request_count=2,
        cooling="fan_on",
        prompt_id_prefix="unit",
        timeout_sec=3.0,
    )

    result = run_m2(
        config=config,
        mode="q4_fixed",
        output_dir=tmp_path,
        session=session,
        monitor=monitor,
        background_telemetry=False,
        now_func=lambda: 10.0,
    )

    assert result.ok is True
    assert session.calls[0]["url"] == "http://127.0.0.1:8082" + CHAT_COMPLETIONS_PATH
    assert session.calls[0]["headers"][PROMPT_ID_HEADER] == "unit-q4_fixed-000001"
    assert "prompt_id" not in session.calls[0]["json"]

    with (tmp_path / "requests.csv").open(encoding="utf-8", newline="") as fp:
        request_rows = list(csv.DictReader(fp))
    assert [row["prompt_id"] for row in request_rows] == [
        "unit-q4_fixed-000001",
        "unit-q4_fixed-000002",
    ]
    assert request_rows[0]["mode"] == "q4_fixed"
    assert request_rows[0]["cooling"] == "fan_on"
    assert request_rows[0]["status_code"] == "200"
    assert request_rows[0]["tokens_out"] == "6"
    assert float(request_rows[0]["tokens_per_sec"]) >= 0.0

    with (tmp_path / "telemetry.csv").open(encoding="utf-8", newline="") as fp:
        telemetry_rows = list(csv.DictReader(fp))
    assert telemetry_rows[0]["temp_c"] == "45.000"
    assert telemetry_rows[0]["clock_hz"] == "1500000000"

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "q4_fixed"
    assert manifest["request_count"] == 2
    assert manifest["failed_count"] == 0
    assert manifest["safety_stop"] is False


def test_run_m2_open_loop_arrival_rate_paces_requests(tmp_path) -> None:
    class FakeResponse:
        status_code = 200

        def __init__(self) -> None:
            self.text = json.dumps({"model": "m", "usage": {"completion_tokens": 5}})

        def json(self):
            return {"model": "m", "usage": {"completion_tokens": 5}}

    class FakeSession:
        def post(self, url, json, headers, timeout):
            return FakeResponse()

    sleeps: list[float] = []
    config = M2Config(
        q4_url="http://127.0.0.1:8082",
        request_count=3,
        arrival_interval_sec=5.0,
        prompt_id_prefix="rate",
    )

    run_m2(
        config=config,
        mode="q4_fixed",
        output_dir=tmp_path,
        session=FakeSession(),
        monitor=FakeMonitor([MonitorSnapshot(0.0, 40.0, 1_500_000_000, "0x0")]),
        background_telemetry=False,
        now_func=lambda: 0.0,
        monotonic_func=lambda: 0.0,
        sleep_func=lambda s: sleeps.append(round(s, 3)),
    )

    # Request 1 dispatches at loop_start (no wait); 2 at +5 s; 3 at +10 s —
    # independent of (instant) backend latency.
    assert sleeps == [5.0, 10.0]


def test_run_m2_open_loop_does_not_dispatch_past_duration(tmp_path) -> None:
    class FakeResponse:
        status_code = 200

        def __init__(self) -> None:
            self.text = json.dumps({"model": "m", "usage": {"completion_tokens": 5}})

        def json(self):
            return {"model": "m", "usage": {"completion_tokens": 5}}

    class FakeSession:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, url, json, headers, timeout):
            self.calls += 1
            return FakeResponse()

    clock = {"t": 0.0}
    session = FakeSession()
    config = M2Config(
        q4_url="http://127.0.0.1:8082",
        duration_sec=6.0,
        arrival_interval_sec=10.0,
        prompt_id_prefix="rate",
    )

    result = run_m2(
        config=config,
        mode="q4_fixed",
        output_dir=tmp_path,
        session=session,
        monitor=FakeMonitor([MonitorSnapshot(0.0, 40.0, 1_500_000_000, "0x0")]),
        background_telemetry=False,
        now_func=lambda: 0.0,
        monotonic_func=lambda: clock["t"],
        sleep_func=lambda s: clock.__setitem__("t", clock["t"] + s),
    )

    assert session.calls == 1
    assert len(result.request_rows) == 1


def test_m2_config_rejects_both_pacing_modes() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        M2Config(interval_sec=1.0, arrival_interval_sec=5.0)


def test_run_m2_records_request_failure_and_cli_exits_nonzero(monkeypatch, tmp_path) -> None:
    class FailingSession:
        def post(self, url, json, headers, timeout):
            raise requests.Timeout("timed out")

    output_dir = tmp_path / "run"
    rows = run_m2(
        config=M2Config(request_count=1),
        mode="controller",
        output_dir=output_dir,
        session=FailingSession(),
        monitor=FakeMonitor([MonitorSnapshot(1.0, 40.0, 1_500_000_000, "0x0")]),
        background_telemetry=False,
    ).request_rows

    assert rows[0].ok is False
    assert rows[0].status_code is None
    assert "timed out" in rows[0].detail

    def fake_run_m2(**kwargs):
        return type(
            "FakeResult",
            (),
            {
                "ok": False,
                "request_rows": rows,
                "telemetry_rows": [],
                "manifest": {},
                "output_dir": output_dir,
            },
        )()

    config_path = tmp_path / "m2.json"
    config_path.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr("thermal_guardian.m2.run_m2", fake_run_m2)

    with pytest.raises(SystemExit) as exc:
        main(["run", "--config", str(config_path), "--mode", "controller"])

    assert exc.value.code == 1


def test_run_m2_aborts_after_consecutive_request_failures(tmp_path) -> None:
    class FailingSession:
        def post(self, url, json, headers, timeout):
            raise requests.ConnectionError("connection refused")

    result = run_m2(
        config=M2Config(duration_sec=60.0, max_consecutive_failures=3),
        mode="controller",
        output_dir=tmp_path,
        session=FailingSession(),
        monitor=FakeMonitor([MonitorSnapshot(1.0, 40.0, 1_500_000_000, "0x0")]),
        background_telemetry=False,
        now_func=lambda: 1.0,
        monotonic_func=lambda: 1.0,
    )

    assert result.ok is False
    assert len(result.request_rows) == 3
    assert all(not row.ok for row in result.request_rows)
    assert result.manifest["safety_stop"] is True
    assert "consecutive request failures 3" in result.manifest["safety_reason"]


def test_run_m2_records_telemetry_safety_stop_before_requests(tmp_path) -> None:
    result = run_m2(
        config=M2Config(request_count=1, safety_temp_c=70.0),
        mode="q8_fixed",
        output_dir=tmp_path,
        session=object(),
        monitor=FakeMonitor([MonitorSnapshot(1.0, 72.0, 1_000_000_000, "0x80000")]),
        background_telemetry=False,
    )

    assert result.ok is False
    assert result.request_rows == []
    assert result.manifest["safety_stop"] is True
    assert "72.0" in result.manifest["safety_reason"]

    with (tmp_path / "telemetry.csv").open(encoding="utf-8", newline="") as fp:
        telemetry_rows = list(csv.DictReader(fp))
    assert telemetry_rows[0]["temp_c"] == "72.000"
    assert telemetry_rows[0]["throttled_hex"] == "0x80000"


def test_summarize_runs_computes_medians_iqr_and_safety(tmp_path) -> None:
    run_dir = tmp_path / "controller_fan_on_001"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "mode": "controller",
                "cooling": "fan_on",
                "safety_stop": False,
                "safety_reason": "",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "requests.csv").write_text(
        "\n".join(
            [
                ",".join(
                    [
                        "ts",
                        "prompt_id",
                        "mode",
                        "cooling",
                        "ok",
                        "status_code",
                        "latency_ms",
                        "tokens_out",
                        "tokens_per_sec",
                        "model",
                        "url",
                        "detail",
                    ]
                ),
                "1.0,p1,controller,fan_on,true,200,10.0,5,500.0,m,http://r,{}",
                "2.0,p2,controller,fan_on,true,200,20.0,5,250.0,m,http://r,{}",
                "3.0,p3,controller,fan_on,true,200,30.0,5,166.666,m,http://r,{}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "telemetry.csv").write_text(
        "\n".join(
            [
                "ts,elapsed_sec,mode,cooling,temp_c,clock_hz,throttled_hex",
                "1.0,0.0,controller,fan_on,50.0,1500000000,0x0",
                "2.0,1.0,controller,fan_on,60.0,1400000000,0x0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = summarize_runs([run_dir], output=tmp_path / "summary.json")

    assert summary["ok"] is True
    assert summary["runs"][0]["median_latency_ms"] == 20.0
    assert summary["runs"][0]["iqr_latency_ms"] == 10.0
    assert summary["runs"][0]["max_temp_c"] == 60.0
    assert summary["runs"][0]["throttle_seen"] is False
    assert summary["by_mode"]["controller"]["run_count"] == 1


def test_summarize_cli_exits_nonzero_when_run_has_safety_stop(tmp_path) -> None:
    run_dir = tmp_path / "q8_fixed_fan_on_001"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "mode": "q8_fixed",
                "cooling": "fan_on",
                "safety_stop": True,
                "safety_reason": "temp_c 83.0 >= safety_temp_c 82.0",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "requests.csv").write_text(
        "ts,prompt_id,mode,cooling,ok,status_code,latency_ms,tokens_out,tokens_per_sec,model,url,detail\n"
        "1.0,p1,q8_fixed,fan_on,true,200,10.0,5,500.0,m,http://r,{}\n",
        encoding="utf-8",
    )
    (run_dir / "telemetry.csv").write_text(
        "ts,elapsed_sec,mode,cooling,temp_c,clock_hz,throttled_hex\n"
        "1.0,0.0,q8_fixed,fan_on,83.0,1500000000,0x80000\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        main(["summarize", "--input", str(run_dir), "--output", str(tmp_path / "summary.json")])

    assert exc.value.code == 1
    saved = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert saved["ok"] is False
    assert saved["runs"][0]["safety_stop"] is True


def test_plot_run_writes_svg_with_three_panels(tmp_path) -> None:
    run_dir = tmp_path / "controller_fan_on_001"
    run_dir.mkdir()
    (run_dir / "requests.csv").write_text(
        "ts,prompt_id,mode,cooling,ok,status_code,latency_ms,tokens_out,tokens_per_sec,model,url,detail\n"
        "1.0,p1,controller,fan_on,true,200,10.0,5,500.0,m,http://r,{}\n",
        encoding="utf-8",
    )
    (run_dir / "telemetry.csv").write_text(
        "ts,elapsed_sec,mode,cooling,temp_c,clock_hz,throttled_hex\n"
        "1.0,0.0,controller,fan_on,50.0,1500000000,0x0\n",
        encoding="utf-8",
    )

    output = plot_run(input_dir=run_dir, output=tmp_path / "main_graph.svg")

    text = output.read_text(encoding="utf-8")
    assert text.startswith("<svg")
    assert "Temperature C" in text
    assert "ARM clock GHz" in text
    assert "Tokens per sec" in text


def test_power_summary_joins_manual_power_and_computes_j_per_token(tmp_path) -> None:
    run_dir = tmp_path / "q4_fixed_001"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"mode": "q4_fixed", "cooling": "fan_on", "safety_stop": False}),
        encoding="utf-8",
    )
    (run_dir / "requests.csv").write_text(
        "ts,prompt_id,mode,cooling,ok,status_code,latency_ms,tokens_out,tokens_per_sec,model,url,detail\n"
        "1.0,p1,q4_fixed,fan_on,true,200,1000.0,10,10.0,m,http://q4,{}\n"
        "2.0,p2,q4_fixed,fan_on,true,200,2000.0,20,10.0,m,http://q4,{}\n",
        encoding="utf-8",
    )
    (run_dir / "telemetry.csv").write_text(
        "ts,elapsed_sec,mode,cooling,temp_c,clock_hz,throttled_hex\n"
        "1.0,0.0,q4_fixed,fan_on,55.0,1500000000,0x0\n",
        encoding="utf-8",
    )
    manual = tmp_path / "manual_power_readings.csv"
    manual.write_text(
        "run_dir,condition,run_id,mwh,elapsed_time,voltage_v,current_a,power_w,max_voltage_v,max_current_a,max_power_w,meter_cpu_c,photo_path,note\n"
        f"{run_dir},q4_fixed,q4_fixed_001,15,00:30:00,5.1,0.5,2.5,5.2,1.0,5.2,36,photos/q4.jpg,good run\n",
        encoding="utf-8",
    )

    rows = build_power_summary([run_dir], manual_power=manual, output=tmp_path / "power.csv")

    assert rows == [
        {
            "condition": "q4_fixed",
            "run_dir": str(run_dir),
            "requests": "2",
            "tokens_out_total": "30",
            "median_latency_ms": "1500.000",
            "iqr_latency_ms": "1000.000",
            "median_tokens_per_sec": "10.000000",
            "iqr_tokens_per_sec": "0.000000",
            "max_temp_c": "55.000",
            "throttle_seen": "false",
            "safety_stop": "false",
            "mwh": "15",
            "j_per_token": "1.800000",
            "note": "good run",
        }
    ]
    with (tmp_path / "power.csv").open(encoding="utf-8", newline="") as fp:
        saved = list(csv.DictReader(fp))
    assert saved[0]["j_per_token"] == "1.800000"


def test_power_summary_leaves_j_per_token_blank_when_tokens_are_zero(tmp_path) -> None:
    run_dir = tmp_path / "controller_001"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"mode": "controller", "cooling": "fan_on", "safety_stop": False}),
        encoding="utf-8",
    )
    (run_dir / "requests.csv").write_text(
        "ts,prompt_id,mode,cooling,ok,status_code,latency_ms,tokens_out,tokens_per_sec,model,url,detail\n"
        "1.0,p1,controller,fan_on,true,200,1000.0,0,0.0,m,http://r,{}\n",
        encoding="utf-8",
    )
    (run_dir / "telemetry.csv").write_text(
        "ts,elapsed_sec,mode,cooling,temp_c,clock_hz,throttled_hex\n"
        "1.0,0.0,controller,fan_on,55.0,1500000000,0x0\n",
        encoding="utf-8",
    )
    manual = tmp_path / "manual_power_readings.csv"
    manual.write_text(
        "run_dir,condition,run_id,mwh,elapsed_time,voltage_v,current_a,power_w,max_voltage_v,max_current_a,max_power_w,meter_cpu_c,photo_path,note\n"
        f"{run_dir},controller,controller_001,12,00:30:00,5.1,0.5,2.5,5.2,1.0,5.2,36,photos/controller.jpg,zero token test\n",
        encoding="utf-8",
    )

    rows = build_power_summary([run_dir], manual_power=manual, output=tmp_path / "power.csv")

    assert rows[0]["tokens_out_total"] == "0"
    assert rows[0]["j_per_token"] == ""


def test_power_summary_requires_manual_row_and_cli_exits_nonzero(tmp_path) -> None:
    run_dir = tmp_path / "q8_fixed_001"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"mode": "q8_fixed", "cooling": "fan_on", "safety_stop": False}),
        encoding="utf-8",
    )
    (run_dir / "requests.csv").write_text(
        "ts,prompt_id,mode,cooling,ok,status_code,latency_ms,tokens_out,tokens_per_sec,model,url,detail\n",
        encoding="utf-8",
    )
    (run_dir / "telemetry.csv").write_text(
        "ts,elapsed_sec,mode,cooling,temp_c,clock_hz,throttled_hex\n",
        encoding="utf-8",
    )
    manual = tmp_path / "manual_power_readings.csv"
    manual.write_text(
        "run_dir,condition,run_id,mwh,elapsed_time,voltage_v,current_a,power_w,max_voltage_v,max_current_a,max_power_w,meter_cpu_c,photo_path,note\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing manual power row"):
        build_power_summary([run_dir], manual_power=manual, output=tmp_path / "power.csv")

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "power-summary",
                "--input",
                str(run_dir),
                "--manual-power",
                str(manual),
                "--output",
                str(tmp_path / "power.csv"),
            ]
        )

    assert exc.value.code != 0
