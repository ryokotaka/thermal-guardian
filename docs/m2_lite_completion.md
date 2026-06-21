# M2-lite Completion Package

> **What this is:** a record that the measurement harness works (a short smoke
> run), not a performance result. **Who it's for:** engineers checking the
> tooling. **Bottom line:** the harness collected comparable evidence files; it
> proves nothing about which model is better.

This package records the 2026-06-14 fan-on M2-lite smoke run. It is evidence
that the M2 harness can collect comparable request, telemetry, graph, and manual
power-meter files. It is not evidence of performance improvement,
energy-efficiency improvement, J/token improvement, or long-run stability.

## Evidence Directory

All files are under:

```text
data/m2/2026-06-14/fan_on/
```

Important files:

- `m2_summary.json`: machine-readable summary for the three selected runs
- `power_summary.csv`: request metrics joined with manual USB meter readings
- `manual_power_readings.csv`: raw manual readings copied from user photos
- `q4_fixed_001/main_graph.svg`: Q4 temperature, clock, and token/s graph
- `controller_001/main_graph.svg`: controller temperature, clock, and token/s graph
- `q8_fixed_002/main_graph.svg`: Q8 rerun temperature, clock, and token/s graph

Run directories:

- `q4_fixed_001/`
- `controller_001/`
- `q8_fixed_002/`

`q8_fixed_001/` was kept as the first smoke run, but it included model/server
setup time in the USB meter reading and is not used as the comparison Q8 row.

## Run Summary

Conditions:

- Cooling: active cooler connected (`fan_on`)
- Duration: 180 seconds per selected run
- Repetitions: N=1
- USB meter: YOJOCK KWS-2303C USB C Tester
- Manual power reading: reset before each selected run, photo immediately after
  each run, `mWh` copied into CSV

| condition | requests | tokens | median latency ms | median token/s | max temp C | throttle | mWh | J/token reference |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| q4_fixed | 71 | 2130 | 2489.457 | 12.050821 | 63.7 | false | 402 | 0.679 |
| controller | 43 | 1161 | 4278.910 | 6.310018 | 60.9 | false | 369 | 1.144 |
| q8_fixed rerun | 43 | 1161 | 4267.446 | 6.326969 | 62.0 | false | 354 | 1.098 |

## Interpretation

- All selected runs completed with `failed_count=0`.
- All selected runs had `safety_stop=false`.
- `throttle_seen=false` in the run telemetry.
- Final post-run check reported `get_throttled=0x0`.
- The controller run stayed on Q8. The fan-on 180-second run did not reach the
  default 70 C switch threshold.
- The J/token values are reference calculations from manual USB meter photos:

```text
J/token = mWh * 3.6 / tokens_out_total
```

## Supported wording

Supported statement:

`M2-lite fan-on smoke testing produced comparable CSV, manifest, graph, and
manual USB power-meter evidence for Q4 fixed, Q8 fixed, and controller runs.
The run path completed without request failures, safety stop, or throttle flags.`

Not supported by this run:

- that M2 is complete
- that Q4, Q8, or the controller is proven better
- that the controller improved J/token
- that the controller switched in this fan-on M2-lite run
- using these N=1 180-second results as long-run stability evidence
