# M2-lite Checklist

This checklist prepares the M2 comparison harness. It is not the full M2
experiment and does not prove latency improvement, J/token improvement, or
long-run stability.

## Before Running

- Reconnect the Raspberry Pi active cooler.
- Reboot the Pi before the first comparison run so old `get_throttled` history
  does not pollute the new run notes.
- Confirm local configs and data stay ignored:

```bash
git check-ignore m2.local.json
git check-ignore data/m2/YYYY-MM-DD/q8_fixed_fan_on_001/requests.csv
```

- Use `m2.local.json` for Pi-specific URLs, prompt settings, duration, sampling
  interval, safety temperature, room temperature, and power-meter model fields.
- Start Q8 and Q4 servers through M0 first. Start the router only for the
  `controller` condition.

## M2-lite Fan-on Pass

Run a short N=1 check for each condition. Use 5-10 minutes first; increase only
after CSVs and summaries look correct.

```bash
python -m edge_llm_guardian.m2 run \
  --config m2.local.json \
  --mode q8_fixed \
  --output-dir data/m2/YYYY-MM-DD/q8_fixed_fan_on_001 \
  --duration-sec 600 \
  --cooling fan_on

python -m edge_llm_guardian.m2 run \
  --config m2.local.json \
  --mode q4_fixed \
  --output-dir data/m2/YYYY-MM-DD/q4_fixed_fan_on_001 \
  --duration-sec 600 \
  --cooling fan_on

python -m edge_llm_guardian.m2 run \
  --config m2.local.json \
  --mode controller \
  --output-dir data/m2/YYYY-MM-DD/controller_fan_on_001 \
  --duration-sec 600 \
  --cooling fan_on
```

Each run directory should contain:

- `requests.csv`: prompt id, condition, HTTP status, latency, output tokens, and
  token/s for each chat request
- `telemetry.csv`: temperature, ARM clock, and throttle flags on the same run
  timeline
- `manifest.json`: commit hash, OS/kernel, governor, config, cooling label, and
  safety-stop status
- A run should stop after 3 consecutive request failures. If that happens,
  fix the router/backend problem and do not count that run toward N=5.

## Summary and Graph

Summarize the three short runs:

```bash
python -m edge_llm_guardian.m2 summarize \
  --input data/m2/YYYY-MM-DD/q8_fixed_fan_on_001 \
  --input data/m2/YYYY-MM-DD/q4_fixed_fan_on_001 \
  --input data/m2/YYYY-MM-DD/controller_fan_on_001 \
  --output data/m2/YYYY-MM-DD/m2_summary.json
```

Create a same-timeline graph for each run:

```bash
python -m edge_llm_guardian.m2 plot \
  --input data/m2/YYYY-MM-DD/controller_fan_on_001 \
  --output data/m2/YYYY-MM-DD/controller_main_graph.svg
```

## What Not To Claim Yet

- Do not claim Q8, Q4, or controller is faster from a single M2-lite pass.
- Do not claim J/token until USB power-meter CSV is collected and joined with
  token counts.
- Do not claim fan-off long-run stability. The earlier fan-off switch run was
  safety-stopped after high temperature and throttle history appeared.

## Completed M2-lite Evidence

The 2026-06-14 fan-on N=1 smoke run is summarized in
`docs/m2_lite_completion.md`.

That run shows the M2-lite harness can collect comparable CSV, manifest, graph,
and manual power-meter evidence. It does not prove performance improvement,
energy-efficiency improvement, J/token improvement, or long-run stability.

## Full M2 Comes Later

Full M2 should follow `docs/m2_full_protocol.md`: 1800-second fan-on runs, N=5
per condition, median+IQR reporting, manual USB power-meter readings, and a
tuned fan-on controller threshold for the controller condition.

Before starting full M2, confirm the Phase A helpers are ready:

```bash
python -m edge_llm_guardian.m2 power-summary --help
git check-ignore m2.local.json config.m2.fan_on.local.json
```

Use this manual power CSV header for the full run:

```text
run_dir,condition,run_id,mwh,elapsed_time,voltage_v,current_a,power_w,max_voltage_v,max_current_a,max_power_w,meter_cpu_c,photo_path,note
```

For controller runs, change the ignored router config `log_dir` before each run
so `events.csv` lands under the current run directory, for example:

```text
data/m2/YYYY-MM-DD/fan_on_full/controller_001/router_logs
```
