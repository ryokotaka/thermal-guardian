# M2 Full Fan-on N=5 Results

> **What this is:** the raw evidence behind the README's headline numbers.
> **Who it's for:** anyone who wants to verify the results.
> **Bottom line:** all 15 runs finished with no throttling or safety stop; fixed
> Q4 was the best baseline, and the controller beat fixed Q8 but not fixed Q4.

This file records the completed fan-on M2 evaluation package from 2026-06-15 to
2026-06-16. It is an evidence note, not marketing prose.

## Scope

- Device: Raspberry Pi 5 4GB
- Cooling: active cooler connected (`fan_on`)
- Models: Qwen2.5-1.5B Q8_0 and Q4_K_M GGUF
- Workload: same chat prompt, `temperature=0.0`, `max_tokens=64`
- Duration: 1800 seconds per run
- Repetitions: N=5 per condition
- Conditions:
  - `q8_fixed`: direct requests to the Q8 server
  - `q4_fixed`: direct requests to the Q4 server
  - `controller`: requests through the router
- Controller threshold: `temp_up_c=63.0`, `temp_down_c=59.0`
- Controller cooldown: 10 seconds
- Safety temperature: 82 C
- USB meter: YOJOCK KWS-2303C USB C Tester

## Evidence

Primary evidence directory on the Pi:

```text
data/m2/2026-06-15/fan_on_full/
```

Important generated files:

- `m2_summary_n5.json`
- `power_summary_n5.csv`
- `condition_summary_n5.csv`
- `manual_power_readings.csv`
- per-run `requests.csv`
- per-run `telemetry.csv`
- per-run `manifest.json`
- per-controller-run `router_logs/events.csv`

Archived evidence package:

```text
data/m2/2026-06-15/fan_on_full_n5_artifacts_2026-06-16.tar.gz
```

SHA-256:

```text
a0cf7239e1aa0c8c685510a6b716d15d111b0509d466f11321181a6ea11d1511
```

The archive and raw `data/` directory are intentionally ignored by git.

## Selected Runs

These successful runs are included in the N=5 summaries:

```text
q8_fixed_001
q8_fixed_002
q8_fixed_003
q8_fixed_004
q8_fixed_005

q4_fixed_001
q4_fixed_002_retry
q4_fixed_003
q4_fixed_004
q4_fixed_005

controller_001_retry
controller_002
controller_003
controller_004
controller_005
```

Known invalid runs are preserved but excluded:

- `controller_001`: router was not running at the start.
- `q4_fixed_002`: Q8/Q4 llama-server processes were not running at the start.

## Condition Summary

Median values from `condition_summary_n5.csv`:

| condition | n | requests | tokens | latency ms | token/s | max temp C | mWh | J/token | throttle | safety stop |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `q8_fixed` | 5 | 436 | 11772 | 4133.114 | 6.532604 | 65.3 | 3536 | 1.081346 | false | false |
| `q4_fixed` | 5 | 685 | 20550 | 2661.228 | 11.272979 | 68.1 | 3864 | 0.676905 | false | false |
| `controller` | 5 | 627 | 18504 | 2671.448 | 11.229868 | 68.1 | 3767 | 0.730637 | false | false |

IQR across the five runs:

| condition | requests IQR | tokens IQR | latency ms IQR | token/s IQR | max temp C IQR | mWh IQR | J/token IQR |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `q8_fixed` | 4.0 | 108.0 | 26.501 | 0.041904 | 0.850 | 49.5 | 0.010224 |
| `q4_fixed` | 3.5 | 105.0 | 21.808 | 0.092705 | 0.250 | 35.0 | 0.004750 |
| `controller` | 14.0 | 493.5 | 31.430 | 0.133407 | 0.600 | 31.0 | 0.025023 |

## Controller Switch Evidence

Each of the five controller runs recorded:

```text
switch_to_q4 = 1
switch_to_q8 = 1
```

The controller evidence supports this statement:

```text
Under the fan-on M2 N=5 protocol, the controller produced switch events in
every selected controller run and completed without throttle or safety stop.
```

It does not prove that the controller is the best policy.

## Interpretation

Measured facts:

- All 15 selected runs completed.
- `throttle_seen=false` for all selected runs.
- `safety_stop=false` for all selected runs.
- `q4_fixed` had the best median latency, token/s, and J/token in this workload.
- `controller` was much closer to `q4_fixed` than to `q8_fixed` for latency and
  token/s because it switched to Q4 during each controller run.
- `controller` had better median J/token than `q8_fixed`.
- `controller` did not beat `q4_fixed` on median latency, token/s, or J/token.

Supported conclusion:

```text
The implementation can run Q8/Q4 llama-server processes on Raspberry Pi 5,
route OpenAI-compatible chat requests, switch between Q8 and Q4 based on
temperature, and record repeatable M2 fan-on N=5 evidence. In this workload,
fixed Q4 was the best performer; the controller improved over fixed Q8 but did
not outperform fixed Q4.
```

## What this does not show

These runs do not show:

- the controller is the fastest option
- the controller is the most energy-efficient option
- the controller improves over fixed Q4
- the 63/59 C thresholds are optimal
- fan-off stability
- the result proves general long-run stability beyond this fan-on 30-minute
  N=5 protocol
- any output-quality conclusion
- any LLM output-safety conclusion

## Next Technical Questions

- Does the controller help when Q4 quality is not acceptable for all prompts?
- Does a quality-aware policy beat fixed Q4?
- Would longer prompts, higher concurrency, or different models change the
  Q8/Q4/controller tradeoff?
- Can the controller thresholds be tuned for lower temperature without giving
  up too much Q8 time?
