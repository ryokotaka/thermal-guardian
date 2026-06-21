# M1 Checklist

> **What this is:** the check that the router can record real switch events
> without flapping. **Who it's for:** anyone reproducing the work. **Bottom
> line:** switch-event evidence only — no speed, energy, or stability claim.

This checklist is for the M1 v0 acceptance pass after M0 has already passed.
It records switch-event evidence only, not a latency, energy-efficiency, J/token,
or long-run-stability claim.

## Before Running

- Complete M0 on Raspberry Pi 5 first.
- Use `config.local.json` for local router settings and confirm it is ignored:

```bash
git check-ignore config.local.json
```

- Use a dated evidence directory such as `data/m1/YYYY-MM-DD/`.

## Router Load

Start the router in a separate terminal. For real Pi runs, use the local config:

```bash
python -m thermal_guardian.router --config config.local.json
```

For a local no-model run, use dry-run mode:

```bash
python -m thermal_guardian.router --config config.example.json --dry-run
```

Send repeated OpenAI-compatible chat requests:

```bash
python -m thermal_guardian.m1 load-run \
  --router-url http://127.0.0.1:8080 \
  --output data/m1/YYYY-MM-DD/load_requests.csv \
  --duration-sec 600 \
  --max-tokens 64
```

The load command adds `X-Edge-Prompt-Id` for router-side CSV tracking. It does
not add a custom `prompt_id` field to the OpenAI-compatible request body.

## Event Analysis

Analyze the router event log:

```bash
python -m thermal_guardian.m1 analyze-events \
  --events logs/events.csv \
  --config config.local.json \
  --output data/m1/YYYY-MM-DD/m1_summary.json
```

M1 event analysis passes only when:

- at least one `switch_to_q4` or `switch_to_q8` event exists
- adjacent real switch events are not closer than `min_switch_interval_sec`

`cooldown_blocked` is counted as evidence that the controller blocked a rapid
switch. It is not a failure by itself.

## Local Fake Switch

Use FakeMonitor to prove the logging and analysis path without Pi hardware:

```bash
python -m thermal_guardian.m1 fake-switch \
  --config config.example.json \
  --log-dir logs/m1_fake

python -m thermal_guardian.m1 analyze-events \
  --events logs/m1_fake/events.csv \
  --config config.example.json \
  --output data/m1/YYYY-MM-DD/fake_m1_summary.json
```

## Supported M1 statement

After the checks above pass, the supported statement is:

`M1 tooling can generate continuous router load, record switch events in
events.csv, and check that the recorded switch events do not oscillate faster
than the configured cooldown.`

M1 alone is not evidence of latency improvement, J/token improvement, energy
improvement, or long-run thermal stability.
