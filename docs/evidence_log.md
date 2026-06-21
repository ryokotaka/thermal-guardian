# Evidence Log

> **What this is:** a dated log of every fact actually checked on the device,
> each paired with the exact wording it supports. **Who it's for:** reviewers who
> want to trace any claim back to its evidence. **Bottom line:** nothing here is a
> claim until the evidence files and run conditions are checked.

This file records checked facts from local/Pi runs. It is not public-facing
performance prose. No entry here becomes a claim without checking the evidence
files and the run conditions.

## 2026-06-14 M0 Real Model Bring-Up

Environment:

- Device: Raspberry Pi 5 4GB
- OS: Raspberry Pi OS Bookworm 64-bit
- Runtime: `llama-server` built from `llama.cpp` commit `8ed274e`
- Model repo: `Qwen/Qwen2.5-1.5B-Instruct-GGUF`
- Model files:
  - `qwen2.5-1.5b-instruct-q8_0.gguf`
  - `qwen2.5-1.5b-instruct-q4_k_m.gguf`
- License noted on model card: Apache-2.0

Observed:

- Both Q8 and Q4 `llama-server` processes reached `/health` OK.
- `chat-smoke` succeeded for both servers.
- RSS was recorded for both saved PIDs.
- PMIC idle sample after model load showed `EXT5V_V=5.12282V`.
- `get_throttled` was `0x0` at the checked point.

Evidence:

- `data/m0/2026-06-14/chat_smoke.csv`
- `data/m0/2026-06-14/server_rss.csv`
- `data/m0/2026-06-14/pmic_idle_after_models.csv`

Supported wording:

`M0 real-model bring-up succeeded for Qwen2.5-1.5B Q8/Q4 on Raspberry Pi 5:
both llama-server processes answered health and chat-smoke, and RSS plus PMIC
check logs were recorded.`

This entry is not evidence of latency improvement, J/token improvement, or
long-run stability.

## 2026-06-14 M1 Active-Cooling Run

Observed:

- With active cooling connected, a 600-second run using `config.local.json`
  (`temp_up_c=70.0`, `temp_down_c=60.0`) completed load generation but did not
  reach the 70 C switch threshold.
- `analyze-events` returned `ok=false` because no switch event was present.
- This result shows the threshold was too high for that cooling condition; it
  is not a controller failure.

Evidence:

- `data/m1/2026-06-14/load_requests.csv`
- `data/m1/2026-06-14/m1_summary.json`
- `data/m1/2026-06-14/pmic_load.csv`

## 2026-06-14 M1 Tuned Threshold Run

Observed:

- With active cooling connected, a 300-second run using `config.m1.local.json`
  (`temp_up_c=63.0`, `temp_down_c=59.0`) recorded one `switch_to_q4` event.
- `analyze-events` returned `ok=true`.
- `oscillation_detected=false`.
- PMIC load sample showed `EXT5V_V=5.07458V`.
- `get_throttled` was `0x0` at the checked point.

Evidence:

- `data/m1/2026-06-14/load_requests_tuned.csv`
- `data/m1/2026-06-14/m1_summary_tuned.json`
- `data/m1/2026-06-14/events_tuned.csv`
- `data/m1/2026-06-14/router_requests_tuned.csv`
- `data/m1/2026-06-14/pmic_load_tuned.csv`

Supported wording:

`M1 switch evidence was confirmed on Pi with real Q8/Q4 servers under a tuned
threshold: the router switched from Q8 to Q4, recorded the event in events.csv,
and analyze-events found no oscillation.`

This does not prove better latency, better energy efficiency, or long-run thermal
stability.

## 2026-06-14 M1 No-Fan Run

Condition:

- Active cooler was disconnected.
- Heat sink was kept on.
- Original threshold config was used: `temp_up_c=70.0`, `temp_down_c=60.0`.
- Safety stop rule: stop load if temperature reaches about 82 C or
  `get_throttled` changes from `0x0`.

Observed:

- M0 no-fan `chat-smoke` succeeded before load.
- M1 no-fan run recorded one `switch_to_q4` at `70.3 C`.
- `analyze-events` returned `ok=true`.
- `oscillation_detected=false`.
- PMIC load sample showed `EXT5V_V=5.0652V`.
- Safety stop triggered at `temp=81.8` and `throttled=0x80000`.
- Load process exit status was `143`, because it was intentionally killed by
  the safety stop.

Evidence:

- `data/m0/2026-06-14/nofan/chat_smoke_nofan.csv`
- `data/m1/2026-06-14/nofan/m1_summary_nofan.json`
- `data/m1/2026-06-14/nofan/events_nofan.csv`
- `data/m1/2026-06-14/nofan/router_requests_nofan.csv`
- `data/m1/2026-06-14/nofan/thermal_watch_nofan.csv`
- `data/m1/2026-06-14/nofan/pmic_load_nofan.csv`
- `data/m1/2026-06-14/nofan/abort_reason.txt`

Supported wording:

`With active cooling disconnected, the original 70 C threshold produced a real
Q8 -> Q4 switch event on Pi and analyze-events found no oscillation. The same
run also reached a thermal safety stop at 81.8 C with get_throttled=0x80000, so
it is switch evidence, not no-fan stability evidence.`

This entry is not evidence that no-fan operation is safe, stable, or suitable for
long evaluation runs.

## 2026-06-14 M2-lite Fan-on N=1 Smoke Run

Scope:

- Short fan-on M2-lite runs after Q8/Q4 servers and router were already running.
- Conditions: `q4_fixed`, `controller`, and rerun `q8_fixed`.
- USB meter: YOJOCK KWS-2303C USB C Tester, manually read from photos.
- Evidence directory: `data/m2/2026-06-14/fan_on/`.

Run summary:

| condition | requests | tokens | median latency ms | median token/s | max temp C | throttle | mWh | J/token reference |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| q4_fixed | 71 | 2130 | 2489.457 | 12.050821 | 63.7 | false | 402 | 0.679 |
| controller | 43 | 1161 | 4278.910 | 6.310018 | 60.9 | false | 369 | 1.144 |
| q8_fixed rerun | 43 | 1161 | 4267.446 | 6.326969 | 62.0 | false | 354 | 1.098 |

Interpretation:

- The harness produced `requests.csv`, `telemetry.csv`, `manifest.json`,
  `m2_summary.json`, manual power readings, and SVG graphs.
- `get_throttled` stayed `0x0` after the runs.
- The controller run stayed on Q8 because fan-on temperature did not reach the
  configured 70 C switch threshold.
- These are N=1 180-second smoke results, not evidence of performance
  improvement, J/token improvement, energy-efficiency improvement, or long-run
  stability.

## 2026-06-15/16 M2 Full Fan-on N=5

Scope:

- Device: Raspberry Pi 5 4GB
- Cooling: active cooler connected (`fan_on`)
- Models: Qwen2.5-1.5B Q8_0 and Q4_K_M GGUF
- Workload: same chat prompt, `temperature=0.0`, `max_tokens=64`
- Duration: 1800 seconds per run
- Repetitions: N=5 per condition
- Controller config: `temp_up_c=63.0`, `temp_down_c=59.0`,
  `min_switch_interval_sec=10.0`
- USB meter: YOJOCK KWS-2303C USB C Tester
- Evidence directory: `data/m2/2026-06-15/fan_on_full/`
- Archive: `data/m2/2026-06-15/fan_on_full_n5_artifacts_2026-06-16.tar.gz`
- Archive SHA-256:
  `a0cf7239e1aa0c8c685510a6b716d15d111b0509d466f11321181a6ea11d1511`

Selected successful runs:

- `q8_fixed_001` through `q8_fixed_005`
- `q4_fixed_001`, `q4_fixed_002_retry`, `q4_fixed_003`, `q4_fixed_004`,
  `q4_fixed_005`
- `controller_001_retry`, `controller_002`, `controller_003`,
  `controller_004`, `controller_005`

Excluded invalid runs:

- `controller_001`: router was not running at the start.
- `q4_fixed_002`: llama-server processes were not running at the start.

Condition medians:

| condition | n | requests | tokens | median latency ms | median token/s | max temp C | mWh | J/token | throttle | safety stop |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `q8_fixed` | 5 | 436 | 11772 | 4133.114 | 6.532604 | 65.3 | 3536 | 1.081346 | false | false |
| `q4_fixed` | 5 | 685 | 20550 | 2661.228 | 11.272979 | 68.1 | 3864 | 0.676905 | false | false |
| `controller` | 5 | 627 | 18504 | 2671.448 | 11.229868 | 68.1 | 3767 | 0.730637 | false | false |

Observed:

- All 15 selected runs completed.
- `throttle_seen=false` for all selected runs.
- `safety_stop=false` for all selected runs.
- Every controller run recorded one `switch_to_q4` and one `switch_to_q8`.
- In this workload, `q4_fixed` was best on median latency, token/s, and
  J/token.
- `controller` was better than `q8_fixed` on median latency, token/s, and
  J/token.
- `controller` did not outperform `q4_fixed`.

Supported wording:

`M2 fan-on N=5 completed on Raspberry Pi 5. The controller produced switch
events in all selected controller runs and completed without throttle or safety
stop. In this workload, fixed Q4 was best on latency, token/s, and J/token; the
controller improved over fixed Q8 but did not outperform fixed Q4.`

This does not show the controller is generally best, that the 63/59 C thresholds
are optimal, or that fan-off or general long-run stability holds.
