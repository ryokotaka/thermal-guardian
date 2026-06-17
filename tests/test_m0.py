import csv
import json

import pytest
import requests

from thermal_guardian.m0 import (
    ChatSmokeRow,
    LlamaServerInstance,
    M0Config,
    PmicReadError,
    RssRow,
    append_rss_rows,
    build_llama_server_command,
    load_m0_config,
    main,
    parse_pmic_read_adc,
    read_pmic_read_adc,
    run_chat_smoke,
)


def make_config() -> M0Config:
    return M0Config(
        llama_server_bin="llama-server",
        pid_dir="run",
        log_dir="logs",
        rss_output="logs/server_rss.csv",
        instances=[
            LlamaServerInstance(
                name="q8",
                model_path="/models/model-Q8_0.gguf",
                host="127.0.0.1",
                port=8081,
                ctx_size=2048,
            ),
            LlamaServerInstance(
                name="q4",
                model_path="/models/model-Q4_K_M.gguf",
                host="127.0.0.1",
                port=8082,
                ctx_size=2048,
                extra_args=["--no-webui"],
            ),
        ],
    )


def test_load_m0_config_from_json(tmp_path) -> None:
    path = tmp_path / "m0.json"
    path.write_text(
        json.dumps(
            {
                "llama_server_bin": "/opt/llama.cpp/build/bin/llama-server",
                "pid_dir": "run",
                "log_dir": "logs",
                "rss_output": "logs/server_rss.csv",
                "instances": [
                    {
                        "name": "q8",
                        "model_path": "/models/q8.gguf",
                        "host": "127.0.0.1",
                        "port": 8081,
                    },
                    {
                        "name": "q4",
                        "model_path": "/models/q4.gguf",
                        "host": "127.0.0.1",
                        "port": 8082,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    config = load_m0_config(path)

    assert config.llama_server_bin == "/opt/llama.cpp/build/bin/llama-server"
    assert [instance.name for instance in config.instances] == ["q8", "q4"]
    assert config.instances[0].ctx_size == 2048


def test_m0_config_requires_q8_and_q4() -> None:
    with pytest.raises(ValueError, match="missing required instances"):
        M0Config(
            llama_server_bin="llama-server",
            pid_dir="run",
            log_dir="logs",
            rss_output="logs/server_rss.csv",
            instances=[
                LlamaServerInstance(
                    name="q8",
                    model_path="/models/q8.gguf",
                    host="127.0.0.1",
                    port=8081,
                )
            ],
        )


def test_build_llama_server_command_uses_approved_flags() -> None:
    config = make_config()

    q8 = build_llama_server_command(config, config.instances[0])
    q4 = build_llama_server_command(config, config.instances[1])

    assert q8 == [
        "llama-server",
        "-m",
        "/models/model-Q8_0.gguf",
        "-c",
        "2048",
        "--host",
        "127.0.0.1",
        "--port",
        "8081",
    ]
    assert q4[-1] == "--no-webui"


def test_append_rss_rows_writes_header_once(tmp_path) -> None:
    path = tmp_path / "rss.csv"
    rows = [
        RssRow(
            ts=1.25,
            name="q8",
            pid=123,
            rss_bytes=456,
            model_path="/models/q8.gguf",
            host="127.0.0.1",
            port=8081,
        )
    ]

    append_rss_rows(path, rows)
    append_rss_rows(path, rows)

    with path.open(encoding="utf-8", newline="") as fp:
        parsed = list(csv.DictReader(fp))

    assert len(parsed) == 2
    assert parsed[0]["name"] == "q8"
    assert parsed[0]["rss_bytes"] == "456"


def test_chat_smoke_posts_to_each_instance_and_writes_tokens(tmp_path) -> None:
    class FakeResponse:
        def __init__(self, tokens: int) -> None:
            self.status_code = 200
            self.text = json.dumps({"usage": {"completion_tokens": tokens}})
            self._tokens = tokens

        def json(self):
            return {"usage": {"completion_tokens": self._tokens}}

    class FakeSession:
        def __init__(self) -> None:
            self.calls = []

        def post(self, url, json, timeout):
            self.calls.append({"url": url, "json": json, "timeout": timeout})
            tokens = 11 if url.endswith(":8081/v1/chat/completions") else 7
            return FakeResponse(tokens)

    output = tmp_path / "chat_smoke.csv"
    session = FakeSession()

    rows = run_chat_smoke(
        make_config(),
        output=output,
        timeout_sec=3.0,
        prompt="Say ok.",
        max_tokens=4,
        session=session,
        ts=2.5,
        prompt_id_prefix="unit",
    )

    assert [row.name for row in rows] == ["q8", "q4"]
    assert all(row.ok for row in rows)
    assert session.calls[0]["url"] == "http://127.0.0.1:8081/v1/chat/completions"
    assert session.calls[1]["url"] == "http://127.0.0.1:8082/v1/chat/completions"
    assert session.calls[0]["json"]["messages"][0]["content"] == "Say ok."
    assert session.calls[0]["json"]["max_tokens"] == 4

    with output.open(encoding="utf-8", newline="") as fp:
        parsed = list(csv.DictReader(fp))

    assert [row["name"] for row in parsed] == ["q8", "q4"]
    assert [row["tokens_out"] for row in parsed] == ["11", "7"]
    assert parsed[0]["prompt_id"] == "unit-q8-2500"


def test_chat_smoke_records_request_failures(tmp_path) -> None:
    class FailingSession:
        def post(self, url, json, timeout):
            if url.endswith(":8082/v1/chat/completions"):
                raise requests.Timeout("timed out")

            class Response:
                status_code = 200
                text = json.dumps({"usage": {"completion_tokens": 5}})

                def json(self):
                    return {"usage": {"completion_tokens": 5}}

            return Response()

    output = tmp_path / "chat_smoke.csv"

    rows = run_chat_smoke(make_config(), output=output, session=FailingSession(), ts=1.0)

    assert rows[0].ok is True
    assert rows[1].ok is False
    assert rows[1].status_code is None
    assert "timed out" in rows[1].detail


def test_chat_smoke_cli_exits_nonzero_on_failure(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "m0.json"
    config_path.write_text(
        json.dumps(
            {
                "instances": [
                    {"name": "q8", "model_path": "/models/q8.gguf", "port": 8081},
                    {"name": "q4", "model_path": "/models/q4.gguf", "port": 8082},
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_run_chat_smoke(*args, **kwargs):
        return [
            ChatSmokeRow(1.0, "q8", "http://127.0.0.1:8081/v1/chat/completions", True, 200, 1.0, 4, "p1", "ok"),
            ChatSmokeRow(1.0, "q4", "http://127.0.0.1:8082/v1/chat/completions", False, None, 1.0, 0, "p2", "failed"),
        ]

    monkeypatch.setattr("thermal_guardian.m0.run_chat_smoke", fake_run_chat_smoke)

    with pytest.raises(SystemExit) as exc:
        main(["chat-smoke", "--config", str(config_path)])

    assert exc.value.code == 1


def test_parse_pmic_read_adc_extracts_ext5v() -> None:
    rows = parse_pmic_read_adc(
        "\n".join(
            [
                "EXT5V_V volt(24)=5.01234567V",
                "3V3_SYS_A current(17)=0.25000000A",
            ]
        ),
        label="load",
        ts=3.0,
    )

    ext5v = next(row for row in rows if row.rail == "EXT5V_V")

    assert ext5v.ts == 3.0
    assert ext5v.label == "load"
    assert ext5v.value == 5.01234567
    assert ext5v.unit == "V"
    assert rows[1].rail == "3V3_SYS_A"
    assert rows[1].unit == "A"


def test_read_pmic_read_adc_missing_vcgencmd_is_pi_specific(monkeypatch) -> None:
    def raise_missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("thermal_guardian.m0.subprocess.run", raise_missing)

    with pytest.raises(PmicReadError, match="Raspberry Pi specific"):
        read_pmic_read_adc()


def test_pmic_sample_cli_exits_nonzero_with_clear_error(monkeypatch, tmp_path, capsys) -> None:
    def raise_pmic_error():
        raise PmicReadError("vcgencmd pmic_read_adc is Raspberry Pi specific")

    monkeypatch.setattr("thermal_guardian.m0.read_pmic_read_adc", raise_pmic_error)

    with pytest.raises(SystemExit) as exc:
        main(["pmic-sample", "--output", str(tmp_path / "pmic.csv"), "--label", "idle"])

    assert exc.value.code == 1
    assert "pmic-sample: vcgencmd pmic_read_adc is Raspberry Pi specific" in capsys.readouterr().err
