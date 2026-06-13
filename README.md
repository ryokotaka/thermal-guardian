# edge-llm-guardian

AI-generated Phase 1 scaffold for a thermal-aware LLM routing runtime.

The public README prose is intentionally not drafted here. The project spec says
the author writes public-facing explanations. This file only records local
developer commands for the current scaffold.

## Phase 1 Scope

- Python package under `src/edge_llm_guardian`
- Raspberry Pi monitor wrappers for `vcgencmd`
- Two-state Q8/Q4 thermal controller with hysteresis and switch cooldown
- OpenAI-compatible `/v1/chat/completions` router
- CSV logs for controller events and requests
- Fake monitor/backend paths for local tests without a Pi or model

## Local Commands

```bash
python -m pip install -e ".[dev]"
python -m pytest
python scripts/fake_llama_server.py --port 8081 --name q8
python scripts/fake_llama_server.py --port 8082 --name q4
python -m edge_llm_guardian.router --config config.example.json
```

Dry-run mode does not contact llama-server:

```bash
python -m edge_llm_guardian.router --config config.example.json --dry-run
```

## M0 Helpers

`m0.example.json` intentionally contains placeholder model paths. Replace them
only after the model choice is approved.

Print the two `llama-server` commands without starting anything:

```bash
python -m edge_llm_guardian.m0 start --config m0.example.json --dry-run
```

After approved model paths are configured, start both servers:

```bash
python -m edge_llm_guardian.m0 start --config m0.example.json
```

Check both `/health` endpoints:

```bash
python -m edge_llm_guardian.m0 check --config m0.example.json
```

Record resident memory for the two saved PIDs:

```bash
python -m edge_llm_guardian.m0 rss --config m0.example.json
```
