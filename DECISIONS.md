# Decisions

Model choices, test settings, and the results that informed them.

## 2026-06-13 — Initial repository name

The project started as `edge-llm-guardian` before the first implementation.

## 2026-06-14 — Models and power measurement

Selected `Qwen/Qwen2.5-1.5B-Instruct-GGUF` for bring-up, using
`qwen2.5-1.5b-instruct-q8_0.gguf` and `qwen2.5-1.5b-instruct-q4_k_m.gguf`.
The model card listed Apache-2.0 at the time of selection.

The power meter is a YOJOCK KWS-2303C USB C Tester. It displays voltage,
current, power, charge, energy, elapsed time, and maximum readings. Later
energy comparisons use its manually recorded mWh readings.

## 2026-06-14 — First switch without active cooling

The Pi switched to Q4 at 70.3 °C. The event check reported no oscillation.
The experiment then reached its safety stop at 81.8 °C after
`get_throttled` became `0x80000`. This confirmed the switch worked; the
run did not establish sustained operation without a fan.

Recorded data: `data/m1/2026-06-14/nofan/` in the local archive.

## 2026-06-14 — Short fan-on runs

The Q8, Q4, and controller smoke tests finished without failed requests,
safety stops, or observed throttle flags. The controller stayed on Q8:
the Pi never reached the default 70 °C switch threshold.

Recorded data: `data/m2/2026-06-14/fan_on/` in the local archive.

## 2026-06-14 — Full fan-on protocol

Set the full comparison to 30 minutes per run, five runs per condition,
with median and IQR reporting. Use manual USB-meter energy readings.
Set controller thresholds to 63 °C and 59 °C because the default 70/60 °C
policy did not switch during the short fan-on tests.

## 2026-06-16 — Full fan-on results

Completed five 30-minute runs for each mode. All selected runs finished
without a safety stop or an observed throttle flag. Each controller run
switched to Q4 once and returned to Q8 once.

Fixed Q4 had the best latency, token rate, and energy per token for this
workload. The controller improved on fixed Q8, but did not outperform
fixed Q4.

Recorded data: `data/m2/2026-06-15/fan_on_full/` in the local archive.
Archive SHA-256: `a0cf7239e1aa0c8c685510a6b716d15d111b0509d466f11321181a6ea11d1511`.

## 2026-06-16 — Thermal Guardian name

Renamed the project to Thermal Guardian, with package `thermal_guardian`
and distribution `thermal-guardian`. The name describes temperature-based
model routing and avoids confusing it with an LLM output-safety guardrail.
The related vision project became Pose Guardian. The GitHub repositories
now use these names.
