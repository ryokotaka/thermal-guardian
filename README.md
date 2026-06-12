# edge-llm-guardian

Phase 1 scaffold for a thermal-aware LLM routing runtime.

Local setup and development commands for the initial scaffold.

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
