# M2 Full Protocol

This protocol is the next full evaluation plan after the M2-lite smoke run. It
is intentionally conservative: the goal is repeatable evidence, not a strong
claim from one run.

## Fixed Conditions

- Hardware: Raspberry Pi 5 with active cooler connected for the first full
  series.
- Models: the same approved Qwen2.5 1.5B Q8_0 and Q4_K_M GGUF files used in M0.
- Workload: the same M2 chat prompt, `max_tokens=64`, `temperature=0.0`.
- Run duration: 1800 seconds per run.
- Repetitions: N=5 per condition.
- Report statistics: median and IQR only.
- Conditions:
  - `q8_fixed`: direct requests to the Q8 server
  - `q4_fixed`: direct requests to the Q4 server
  - `controller`: requests through the router
- Output root: `data/m2/YYYY-MM-DD/fan_on_full/`.

## Controller Threshold

Use a tuned fan-on controller config for the full controller condition:

```json
{
  "temp_up_c": 63.0,
  "temp_down_c": 59.0,
  "min_switch_interval_sec": 10.0
}
```

Tracked example: `config.m2.fan_on.example.json`. Copy it to an ignored local
file such as `config.m2.fan_on.local.json` before running on the Pi.

Reason:

- The default 70/60 C config did not switch during the fan-on M2-lite controller
  run.
- A previous tuned fan-on run at 63/59 C produced a switch event without
  oscillation.
- This is an evaluation setting decision, not a performance claim.

Keep `q8_fixed` and `q4_fixed` direct-to-server, so their results are not
affected by router controller logic.

For every controller run, set the router `log_dir` to that run's directory:

```text
data/m2/YYYY-MM-DD/fan_on_full/controller_001/router_logs
data/m2/YYYY-MM-DD/fan_on_full/controller_002/router_logs
...
```

This keeps router `events.csv` and router-side `requests.csv` from different
controller runs separated.

## Run Naming

Use deterministic run directories:

```text
data/m2/YYYY-MM-DD/fan_on_full/q8_fixed_001
data/m2/YYYY-MM-DD/fan_on_full/q8_fixed_002
data/m2/YYYY-MM-DD/fan_on_full/q8_fixed_003
data/m2/YYYY-MM-DD/fan_on_full/q8_fixed_004
data/m2/YYYY-MM-DD/fan_on_full/q8_fixed_005

data/m2/YYYY-MM-DD/fan_on_full/q4_fixed_001
...
data/m2/YYYY-MM-DD/fan_on_full/controller_005
```

Run order should rotate to reduce temperature-order bias:

```text
round 1: q8_fixed -> q4_fixed -> controller
round 2: q4_fixed -> controller -> q8_fixed
round 3: controller -> q8_fixed -> q4_fixed
round 4: q8_fixed -> controller -> q4_fixed
round 5: q4_fixed -> q8_fixed -> controller
```

Before every run, wait until Pi CPU temperature is below 45 C or record the
starting temperature explicitly if waiting is impractical.

## Run Commands

For fixed Q8 and Q4 runs, call the M2 helper directly:

```bash
python -m edge_llm_guardian.m2 run \
  --config m2.local.json \
  --mode q8_fixed \
  --output-dir data/m2/YYYY-MM-DD/fan_on_full/q8_fixed_001 \
  --duration-sec 1800 \
  --cooling fan_on \
  --prompt-id-prefix m2-full

python -m edge_llm_guardian.m2 run \
  --config m2.local.json \
  --mode q4_fixed \
  --output-dir data/m2/YYYY-MM-DD/fan_on_full/q4_fixed_001 \
  --duration-sec 1800 \
  --cooling fan_on \
  --prompt-id-prefix m2-full
```

For controller runs:

1. Edit ignored `config.m2.fan_on.local.json` so `log_dir` points at the current
   run's `router_logs` directory.
2. Start the router with that config.
3. Run M2 against the router.

```bash
python -m edge_llm_guardian.router --config config.m2.fan_on.local.json

python -m edge_llm_guardian.m2 run \
  --config m2.local.json \
  --mode controller \
  --output-dir data/m2/YYYY-MM-DD/fan_on_full/controller_001 \
  --duration-sec 1800 \
  --cooling fan_on \
  --prompt-id-prefix m2-full
```

After each controller run, keep these files inside that run directory:

```text
controller_001/
  requests.csv
  telemetry.csv
  manifest.json
  router_logs/events.csv
  router_logs/requests.csv
```

## USB Power Meter Procedure

For each run:

1. Confirm `vcgencmd get_throttled` is `0x0`.
2. Confirm the USB meter shows about 5 V.
3. Reset the USB meter immediately before starting the run.
4. Start the M2 run.
5. Take a photo immediately after the run finishes.
6. Copy `mWh`, elapsed time, current displayed V/A/W, max V/A/W, and meter CPU
   temperature into `manual_power_readings.csv`.
7. Generate or update `power_summary.csv` after all runs.

Use these exact manual CSV columns:

```text
run_dir,condition,run_id,mwh,elapsed_time,voltage_v,current_a,power_w,max_voltage_v,max_current_a,max_power_w,meter_cpu_c,photo_path,note
```

Use this formula only after joining the USB meter value with token counts:

```text
J/token = mWh * 3.6 / tokens_out_total
```

Generate the joined power summary after all selected runs:

```bash
python -m edge_llm_guardian.m2 power-summary \
  --manual-power data/m2/YYYY-MM-DD/fan_on_full/manual_power_readings.csv \
  --input data/m2/YYYY-MM-DD/fan_on_full/q8_fixed_001 \
  --input data/m2/YYYY-MM-DD/fan_on_full/q4_fixed_001 \
  --input data/m2/YYYY-MM-DD/fan_on_full/controller_001 \
  --output data/m2/YYYY-MM-DD/fan_on_full/power_summary.csv
```

If a selected run has no manual power row, `power-summary` must fail. Add the
missing manual reading rather than guessing.

## Safety And Failure Rules

- Safety temperature: 82 C.
- Abort a run if `get_throttled` changes from `0x0`, if the USB meter voltage
  drops below about 4.9 V under load, or if the Pi becomes unreachable.
- Abort a run after 3 consecutive request failures. This prevents a missing
  router or dead backend from producing tens of thousands of failed requests.
- Keep aborted runs in the evidence directory with `safety_stop=true` or a
  separate abort note. Do not silently delete them.
- Fan-off full M2 is not part of the first full series. The previous no-fan run
  reached 81.8 C and `get_throttled=0x80000`, so fan-off remains a separate
  safety-capped series.

## Completion Criteria

The fan-on full M2 package is complete when:

- Each of the three conditions has N=5 successful or explicitly documented
  aborted runs.
- Every run has `requests.csv`, `telemetry.csv`, `manifest.json`, and a manual
  power entry.
- `m2_summary.json` exists for all selected run directories.
- `power_summary.csv` joins tokens, latency, temperature, throttle, mWh, and
  J/token reference values.
- Controller run `events.csv` files are separated under each run's
  `router_logs/` directory.
- The report states median and IQR, and clearly separates measured facts from
  interpretation.

Do not claim performance improvement, J/token improvement, or long-run stability
until this full protocol is complete and reviewed.
