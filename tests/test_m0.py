import csv
import json

import pytest

from edge_llm_guardian.m0 import (
    LlamaServerInstance,
    M0Config,
    RssRow,
    append_rss_rows,
    build_llama_server_command,
    load_m0_config,
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
