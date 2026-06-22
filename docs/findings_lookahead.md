# Findings: look-ahead and dwell control (lab notebook)

> **In plain language.** The router's job is graceful degradation: under heat it
> steps down from the Q8 model to the lighter Q4 model to keep serving. This
> notebook tests one refinement — does *predicting* the temperature beat just
> *reacting* to it on a Raspberry Pi 5? Short answer, after fixing an unfair
> benchmark and controlling for confounds: **no.** The lever is how much time is
> spent on the lighter model, not prediction; and a dwell rule only trades fewer
> switches for more Q4 time. A look-ahead idea was tested and largely walked back,
> with data.

## What I found

The investigation ran in four steps (run data for each is in the evidence log below):

1. **A measurement trap.** Early switching *appeared* to lower heat — but only
   because the benchmark was closed-loop ("send the next request immediately"), so
   moving to the faster Q4 path completed more work in the same window. Fixing the
   load to a constant arrival rate removed the confound.
2. **A promising pilot.** Under that fair, open-loop load, bounded look-ahead
   stayed below 63 °C in 3/3 runs while the reactive controller did not (median
   peak 62.0 vs 63.7 °C; median time ≥63 °C 0.0 vs 207.1 s).
3. **The control.** Against a non-predictive controller given roughly the *same*
   Q4 time, the gap shrank to 0.6 °C (62.0 vs 62.6 °C, both 0.0 s ≥63 °C). The
   thermal benefit tracked Q4 time, not prediction.
4. **The dwell trade-off.** A minimum-residence rule cut total switches (36 → 7)
   only by spending more time on Q4 (fraction 0.378 up to 0.714), and the 30 s
   setting briefly crossed 63 °C. No setting reduced switching for free.

**Finding.** On this Raspberry Pi 5 workload, the controller's thermal leverage
comes from how much time it spends on the lighter Q4 model — not from predicting
temperature, and not from switching more cleverly. Anti-flap (dwell) control buys
quieter switching at the cost of more Q4 time, not for free.

**Implication.**

- Evaluate edge thermal control under an open-loop load; closed-loop throughput
  does not measure thermal exposure.
- Treat time on the lighter model as the primary thermal lever. Prediction and
  dwell are refinements that did not beat it here.
- The open question is a cost, not "more dwell": how much extra Q4 time — and the
  output-quality exposure that comes with it — is acceptable per switch removed. A
  second axis worth measuring is whether energy per token degrades non-linearly as
  temperature rises within a run.

## The question

Heat has a long time constant, unlike an injected-load fault, which is closer to a
step. The baseline controller is **reactive**: it switches Q8 → Q4 only after the
CPU actually crosses the upper threshold. If the thermal response is slow and
smooth, a **look-ahead** controller — switching when the temperature *predicted* a
few seconds ahead (from the recent slope) crosses the threshold — might act earlier
and cut overshoot or time-above-threshold. The test: does prediction measurably
beat reaction, and is any gain worth the earlier loss of Q8 quality? A clear
negative is as useful as a positive.

## How it was tested

- **Look-ahead controller.** `look_ahead_sec` turns on prediction (least-squares
  slope over the last `slope_window` samples × the horizon); `0.0` is the reactive
  baseline. After the first pilot flapped, the predictor was bounded:
  `look_ahead_min_samples` (enough samples before a slope is trusted),
  `look_ahead_min_temp_c` (ignore prediction far below the band),
  `look_ahead_max_delta_c` (cap the predicted rise), and reactive-only Q4 → Q8
  recovery. See `src/thermal_guardian/controller.py` and its tests.
- **Fair load.** Early runs were closed-loop, which confounds a thermal comparison
  (a faster model does more work). The harness gained `--arrival-interval-sec`
  (fixed arrival rate) and `--request-count` (equal total work); all later runs
  hold completed work equal.
- **Controlling for Q4 time.** To separate "switched earlier" from "spent more
  time on Q4," bounded look-ahead was compared against reactive controllers with
  *lower* thresholds, picking the one whose Q4 residence time matched
  (`reactive_up61_down59`).
- **Analysis.** `scripts/analyze_lookahead.py` (per-run τ, time-to-threshold,
  peak, overshoot, seconds-above, and the overlay plot) and
  `scripts/analyze_q4_budget_match.py` (Q4-residence-matched comparison). Configs:
  `config.m2.fan_on.example.json` (reactive),
  `config.m2.fan_on.predictive.example.json` (look-ahead). Run commands:
  [Run on a Raspberry Pi](../README.md#run-on-a-raspberry-pi).

## Evidence log

Chronological, fan on, same M2 workload. Tables show the numbers each step rests
on; raw bundles are hashed under [Evidence & reproducibility](#evidence--reproducibility).

### Calibration — the naive predictor flapped, so it was bounded (2026-06-18)

A 10-minute pilot of the first slope-only predictor switched to Q4 at ~45 °C and
oscillated (`switch_to_q4 = 9`, 277 s ≥63 °C, vs the reactive run's single switch
and 125 s ≥63 °C). It was clearly too sensitive to early slope and sensor noise, so
it was bounded (parameters above) before any scaled comparison.

### Counterexample — the closed-loop benchmark was unfair (2026-06-18)

A cleaner reboot-pair (still closed-loop) exposed the trap:

| Run | Start | Requests | Peak temp | Seconds ≥63 °C |
| --- | ---: | ---: | ---: | ---: |
| reactive | 42.2 C | 147 | 63.1 C | 0.0 s |
| bounded look-ahead | 43.9 C | 215 | 65.3 C | 350.1 s |

Bounded switched earlier yet ran *hotter* — because it completed far more requests
(215 vs 147). Under a closed-loop load the faster Q4 path does more work per minute,
coupling the controller to the benchmark. Conclusion: evaluate thermal control
under an open-loop (fixed-demand) load.

### Open-loop pilot — look-ahead looks promising, N=3 (2026-06-19)

Re-run with fixed demand (4 s arrival, 150 requests per run), N=3:

| Mode | N | Median peak | Median s ≥63 °C | Median Q4 switches |
| --- | ---: | ---: | ---: | ---: |
| reactive | 3 | 63.7 C | 207.1 s | 3 |
| bounded look-ahead | 3 | 62.0 C | 0.0 s | 18 |

![Open-loop 4-second N=3 summary](assets/lookahead_open_loop_4s_n3_summary.svg)

Bounded stayed below 63 °C in 3/3, reactive exceeded it in 3/3 — a stronger
candidate than the N=1 pilot. But bounded also spent more time on Q4, switched
often, and start temperatures were not matched, so this was not yet a clean claim.

### Control — at matched Q4 time, the edge is Q4 time, not prediction (2026-06-19)

Bounded look-ahead vs a lower-threshold reactive arm tuned to the same Q4 residence:

| Arm | N | Median Q4 time | Median peak | Median s ≥63 °C |
| --- | ---: | ---: | ---: | ---: |
| bounded look-ahead | 3 | 226.7 s | 62.0 C | 0.0 s |
| reactive 61/59 | 3 | 235.3 s | 62.6 C | 0.0 s |

At similar Q4 time the two were within 0.6 °C — the lower temperature is driven by
time on Q4, not by prediction.

### Dwell — cutting switches costs Q4 time (2026-06-20)

A minimum-residence (dwell) rule, swept 0–120 s (N=3 at 0 s and 60 s, N=1 otherwise),
each run gated to start ≤50 °C with `get_throttled = 0x0`:

| dwell | N | Q4 fraction | Total switches | Peak temp | Seconds ≥63 °C |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 s | 3 | 0.378 | 36 | 62.0 C | 0.0 s |
| 30 s | 1 | 0.446 | 17 | 63.1 C | 2.0 s |
| 60 s | 3 | 0.562 | 11 | 62.6 C | 0.0 s |
| 90 s | 1 | 0.714 | 9 | 60.9 C | 0.0 s |
| 120 s | 1 | 0.628 | 7 | 62.0 C | 0.0 s |

![Minimum-residence dwell sweep: switches fall from 36 to 7 only as Q4 residence rises](assets/lookahead_dwell_sweep.svg)

More dwell cuts switches but raises Q4 time, and the 30 s run briefly crossed
63 °C. No setting cut switching for free; the sweep is noisy (mostly N=1) with no
clean optimum. So **anti-flap control trades switch economy against Q4 residence
time** — the next design question is how much extra Q4 time is acceptable per switch
removed, not simply "more dwell."

## Evidence & reproducibility

Raw CSVs, logs, and archives stay out of git under `data/`. Archived bundles are
referenced by SHA-256 so a result can be tied to a specific package:

- **Q4-budget-matched** — `data/m2/2026-06-19/artifacts/q4_budget_match_2026-06-19.tar.gz`
  · `4bb7df9b20996a14fb28011bfebfb65506892d52d22293b643de491afcccb97c`
- **dwell 120 s pilot** — `data/m2/2026-06-20/artifacts/min_residence_bounded_dwell120_001_2026-06-20.tar.gz`
  · `9e15650bea2275c5388e0fb02466dcce9edbc7a3c519626176cc6cb1d87da440`
- **dwell sweep** — `data/m2/2026-06-20/artifacts/min_residence_gated_sweep_2026-06-20.tar.gz`
  · `a773e4addda4c691e29f8e4352437a824f1c7f847be090b61c241be5b0624239`

Analysis: `scripts/analyze_lookahead.py`, `scripts/analyze_q4_budget_match.py`.
Reproduction protocol and exact `m2 run` invocations:
[Run on a Raspberry Pi](../README.md#run-on-a-raspberry-pi).
