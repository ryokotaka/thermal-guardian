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

Send one minimal chat request to both servers and append smoke-test results:

```bash
python -m edge_llm_guardian.m0 chat-smoke \
  --config m0.example.json \
  --output data/m0/YYYY-MM-DD/chat_smoke.csv
```

On Raspberry Pi OS, append PMIC rail readings for the power-meter acceptance check:

```bash
python -m edge_llm_guardian.m0 pmic-sample --output data/m0/YYYY-MM-DD/pmic_idle.csv --label idle
python -m edge_llm_guardian.m0 pmic-sample --output data/m0/YYYY-MM-DD/pmic_load.csv --label load
```

## M1 Helpers

M1 helpers generate router load, check `events.csv` for switch evidence, and
offer a local FakeMonitor switch run. These commands are not performance claims.

Run repeated chat requests through the router:

```bash
python -m edge_llm_guardian.m1 load-run \
  --router-url http://127.0.0.1:8080 \
  --output data/m1/YYYY-MM-DD/load_requests.csv \
  --duration-sec 600 \
  --max-tokens 64
```

Analyze controller events for at least one switch and no rapid oscillation:

```bash
python -m edge_llm_guardian.m1 analyze-events \
  --events logs/events.csv \
  --config config.local.json \
  --output data/m1/YYYY-MM-DD/m1_summary.json
```

Generate switch events locally without a Pi or model:

```bash
python -m edge_llm_guardian.m1 fake-switch \
  --config config.example.json \
  --log-dir logs/m1_fake
```

## M2-lite Helpers

M2-lite helpers prepare the fixed-workload comparison path. They record inputs
for later evaluation; they are not latency, J/token, or stability claims.

Run one short fixed-workload condition:

```bash
python -m edge_llm_guardian.m2 run \
  --config m2.example.json \
  --mode q8_fixed \
  --output-dir data/m2/YYYY-MM-DD/q8_fixed_fan_on_001 \
  --duration-sec 600 \
  --cooling fan_on
```

Use `--mode q4_fixed` for direct Q4 requests and `--mode controller` for router
requests. Summarize one or more run directories:

```bash
python -m edge_llm_guardian.m2 summarize \
  --input data/m2/YYYY-MM-DD/q8_fixed_fan_on_001 \
  --input data/m2/YYYY-MM-DD/q4_fixed_fan_on_001 \
  --input data/m2/YYYY-MM-DD/controller_fan_on_001 \
  --output data/m2/YYYY-MM-DD/m2_summary.json
```

Write a simple SVG graph for one run:

```bash
python -m edge_llm_guardian.m2 plot \
  --input data/m2/YYYY-MM-DD/controller_fan_on_001 \
  --output data/m2/YYYY-MM-DD/controller_main_graph.svg
```

Join manual USB power-meter readings with run summaries:

```bash
python -m edge_llm_guardian.m2 power-summary \
  --manual-power data/m2/YYYY-MM-DD/manual_power_readings.csv \
  --input data/m2/YYYY-MM-DD/q8_fixed_fan_on_001 \
  --input data/m2/YYYY-MM-DD/q4_fixed_fan_on_001 \
  --input data/m2/YYYY-MM-DD/controller_fan_on_001 \
  --output data/m2/YYYY-MM-DD/power_summary.csv
```

The 2026-06-14 fan-on M2-lite smoke evidence is summarized in
`docs/m2_lite_completion.md`. The full M2 protocol is fixed in
`docs/m2_full_protocol.md`; `config.m2.fan_on.example.json` records the tuned
fan-on controller threshold for that protocol. The completed 2026-06-15/16
fan-on M2 N=5 result package is summarized in
`docs/m2_full_fan_on_n5_results.md`.

Keep the result wording conservative: in the measured fan-on workload, fixed Q4
was best on latency, token/s, and J/token; the controller improved over fixed Q8
but did not outperform fixed Q4.
