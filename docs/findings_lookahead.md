# Findings: thermal dynamics & look-ahead control

> **In plain language.** This is the lab notebook behind one question: on a
> Raspberry Pi 5, does *predicting* temperature help a controller manage heat
> more than just *reacting* to it? An early benchmark was unfair (closed-loop, so
> the faster model did more work in the same window), so the load was fixed to a
> constant request rate. Under that fair load look-ahead first looked promising,
> but a non-predictive controller given the *same* Q4 time matched it within
> 0.6 °C: the lever was time on the light model, not prediction. A follow-up
> dwell rule then cut switching only by spending more Q4 time, a trade-off rather
> than a free win. Net: a look-ahead idea was tested and largely walked back,
> with data.
>
> *Who this is for: skim the plain-language summary above; engineers and
> reviewers can follow the chronological run log, tables, and evidence hashes
> below, ending in the author's **Finding** and **Implication**.*

> **Working doc.** This is the "question → measurement → finding → implication"
> scaffold for the look-ahead investigation. The assistant fills *question* and
> *measurement* (apparatus, protocol, plots, derived numbers). The **finding and
> implication are written by the author** from the run data — the assistant does
> not write them. Once the finding is backed by reproducible data, lift it into a
> `## Findings` section of the README.

**Status (2026-06-20):** complete through three results — (1) a closed-loop
measurement counterexample; (2) at matched Q4 time, the look-ahead thermal edge
largely disappears; (3) a minimum-residence (dwell) sweep showing a
switch-economy vs Q4-time trade-off. The synthesized **Finding** / **Implication**
are at the end of this doc; the sections in between are the chronological run log.

## Question

Heat has a long time constant — unlike an injected-load fault, which is close to a
step. The current controller is **reactive**: it switches Q8 → Q4 only after the
CPU *actually* reaches the upper threshold. If the thermal response is slow and
smooth, a **look-ahead** controller — switching when the temperature *predicted*
`look_ahead_sec` into the future (from the recent slope) crosses the threshold —
could act earlier and change the threshold-crossing behavior.

Does look-ahead control measurably change, versus reactive control:

- the **overshoot** above the threshold (`peak − temp_up`),
- the **time spent above** the threshold,
- the **latency transient** around the switch (from `requests.csv`),

and is any change worth the earlier loss of Q8 time? Either answer is a finding —
"look-ahead does **not** help here, because …" is as valid as "it does."

## Measurement

**Apparatus** (implemented; no new hardware):

- `look_ahead_sec` in the controller config turns on prediction (least-squares
  slope over the last `slope_window` samples × the horizon). `0.0` = today's
  reactive routing behavior. See `src/thermal_guardian/controller.py` and its
  unit tests.
- The predictive path is intentionally bounded after the first pilot:
  - `look_ahead_min_samples` requires enough recent samples before prediction is
    trusted.
  - `look_ahead_min_temp_c` ignores prediction while the CPU is still far below
    the switching band.
  - `look_ahead_max_delta_c` caps how much prediction can add to the current
    temperature.
  - Q4 → Q8 recovery remains reactive on the actual temperature, so a positive
    slope cannot force premature recovery.
- Reactive config: `config.m2.fan_on.example.json` (no `look_ahead_sec` → 0).
- Look-ahead config: `config.m2.fan_on.predictive.example.json`
  (`look_ahead_sec: 30.0` with bounded prediction — a **starting** horizon to
  sweep/tune, not a tuned value).

**Protocol** (fan on, same workload and duration as the M2 evaluation; repeat
**N ≥ 3** per arm). Run the M2 `controller` workload twice with everything
identical except the router's thermal config — once on the reactive config, once
on the predictive config (exact `m2 run` invocation: see
[Run on a Raspberry Pi](../README.md#run-on-a-raspberry-pi)). Suggested layout:

```text
data/m2/DATE/lookahead/reactive_00{1,2,3}/     # router on config.m2.fan_on.local.json
data/m2/DATE/lookahead/predictive_00{1,2,3}/   # router on config.m2.fan_on.predictive.local.json
```

Optional, to quantify τ cleanly: one open-loop heating run from cold under fixed
Q8 load to the plateau (no switching), so the slope/τ estimate is not truncated
by the switch.

**Analysis** (assistant's lane — derives numbers and draws the plot; no claims):

```bash
python scripts/analyze_lookahead.py --temp-up 63 \
  --run reactive=data/m2/DATE/lookahead/reactive_001/telemetry.csv \
  --run predictive=data/m2/DATE/lookahead/predictive_001/telemetry.csv \
  --out-json data/m2/DATE/lookahead/summary.json \
  --out-svg docs/assets/m2_lookahead_compare.svg
```

Per run it reports `tau_63_sec`, `time_to_up_sec`, `peak_temp_c`, `overshoot_c`,
`secs_above_up`, and draws the temperature overlay with per-run threshold-crossing
markers.

Context to record after each analysis run (**data only, not a finding**):
`tau_63_sec`, `time_to_up_sec`, `overshoot_c`, and `secs_above_up`. Do not turn
these into a README claim until the reactive and look-ahead runs for this
investigation have both been collected.

## Pilot calibration note (2026-06-18)

This is a calibration note, not a headline finding. A 10-minute pilot was
run to test the first, naive look-ahead controller before scaling to N >= 3.

Data location on the Pi:

```text
data/m2/2026-06-18/lookahead/reactive_001/
data/m2/2026-06-18/lookahead/predictive_001/
data/m2/2026-06-18/lookahead/lookahead_pilot_summary.json
data/m2/2026-06-18/lookahead/lookahead_pilot.svg
```

Observed data:

| Run | Requests | Switch events | First Q4 switch | Peak temp | Overshoot above 63 C | Seconds above 63 C | Throttle |
| --- | ---: | --- | --- | ---: | ---: | ---: | --- |
| `reactive_001` | 162 | `switch_to_q4=1` | 63.1 C | 65.3 C | 2.3 C | 125.5 s | `0x0` |
| `predictive_001` naive | 190 | `switch_to_q4=9`, `switch_to_q8=8`, `cooldown_blocked=5` | 45.0 C | 64.8 C | 1.8 C | 277.2 s | `0x0` |

What this honestly says:

- The naive slope-only look-ahead controller was too sensitive to early thermal
  slope and sensor noise.
- It switched to Q4 far below the intended thermal band and produced repeated
  Q8/Q4 oscillation.
- Because that behavior was visible in one pilot, the N >= 3 comparison should
  not be run with the naive controller.
- The controller was revised to bounded, upward-only look-ahead before the next
  experiment.

Interview-safe phrasing:

> A pilot falsified the naive predictive controller before I scaled the
> experiment. Instead of hiding the negative result, I changed the controller to
> require enough samples, ignore cold-region slope spikes, cap predicted
> temperature rise, and keep Q4-to-Q8 recovery reactive.

## Bounded look-ahead smoke note (2026-06-18)

After the naive pilot, the controller was changed to bounded upward look-ahead:

```text
look_ahead_sec = 30
slope_window = 10
look_ahead_min_samples = 10
look_ahead_min_temp_c = 59
look_ahead_max_delta_c = 3
```

One 10-minute smoke run was collected to check whether the obvious failure mode
was removed before any longer comparison:

```text
data/m2/2026-06-18/lookahead/predictive_bounded_001/
data/m2/2026-06-18/lookahead/lookahead_bounded_summary.json
data/m2/2026-06-18/lookahead/lookahead_bounded.svg
```

Observed data:

| Run | Requests | Switch events | First Q4 switch | Peak temp | Overshoot above 63 C | Seconds above 63 C | Throttle |
| --- | ---: | --- | --- | ---: | ---: | ---: | --- |
| `predictive_bounded_001` | 214 | `switch_to_q4=1` | 60.4 C | 65.9 C | 2.9 C | 476.8 s | `0x0` |

What this honestly says:

- The bounded controller removed the worst naive failure mode: it no longer
  switched at 45 C and did not flap repeatedly in this 10-minute smoke run.
- This smoke run does **not** show that bounded look-ahead is better than the
  reactive controller on overshoot or time-above-threshold.
- The start temperatures differed between runs, so these 10-minute pilots are
  calibration evidence, not a final comparison.
- If this line of work continues, the next experiment should compare reactive
  versus bounded look-ahead with matched start temperature and N >= 3, or use a
  controlled heating/cooling protocol focused only on switch timing.

## Reboot-pair counterexample (2026-06-18)

After a Raspberry Pi reboot, a cleaner cold-ish pair was run with the same
10-minute duration and no manual power-meter step:

```text
data/m2/2026-06-18/lookahead/reboot_pair_002/reactive/
data/m2/2026-06-18/lookahead/reboot_pair_002/bounded/
data/m2/2026-06-18/lookahead/reboot_pair_002/reboot_pair_summary.json
data/m2/2026-06-18/lookahead/reboot_pair_002/reboot_pair.svg
```

Observed data:

| Run | Start temp | Requests | Switch events | First Q4 switch | Peak temp | Overshoot above 63 C | Seconds above 63 C | Throttle |
| --- | ---: | ---: | --- | --- | ---: | ---: | ---: | --- |
| `reactive` | 42.2 C | 147 | none | none | 63.1 C | 0.1 C | 0.0 s | `0x0` |
| `bounded` | 43.9 C | 215 | `switch_to_q4=1` | 60.4 C | 65.3 C | 2.3 C | 350.1 s | `0x0` |

What this honestly says:

- In this pair, bounded look-ahead switched earlier, but that did **not** reduce
  peak temperature or time above the threshold.
- The bounded run completed many more requests because Q4 is faster in this
  workload. With a closed-loop "send the next request immediately" benchmark,
  switching to the faster backend can increase completed work during the same
  wall-clock window, which can obscure or reverse the expected thermal benefit.
- This is a useful counterexample, not a failure to hide: the control objective
  and the benchmark objective are coupled.

Interview-safe phrasing:

> I found a counterexample in my own follow-up experiment: early switching did
> not automatically lower thermal exposure. Because my load generator was
> closed-loop, moving to the faster Q4 path increased completed work, so the
> thermal controller and benchmark design were coupled. That changed the next
> question from "can I switch earlier?" to "what workload model is fair for
> evaluating thermal control?"

Next step if continuing this line:

- For thermal-control evaluation, use an open-loop fixed arrival rate or fixed
  request count, so Q4 does not automatically create more work per minute.
- Keep the existing closed-loop results as throughput/energy evidence, but do
  not use them alone to claim reduced thermal exposure from look-ahead control.

---

_Apparatus update (assistant): the two open-loop modes this implication calls for
are now in the harness — `--arrival-interval-sec` (fixed arrival rate, schedule-based
dispatch) and the existing `--request-count` (equal total work). `analyze_lookahead.py`
now also reports `completed_requests` per run, so the closed-loop coupling shows up in
the numbers. Reactive vs look-ahead can be re-run fairly; the finding/claim stay yours._

## Open-loop harness smoke note (2026-06-19)

After the Pi reboot, a short smoke test checked that `--arrival-interval-sec`
works on the real device. This is apparatus validation, not a thermal-control
finding.

```text
data/m2/2026-06-19/lookahead_open_loop_smoke_001/reactive/
data/m2/2026-06-19/lookahead_open_loop_smoke_001/bounded/
data/m2/2026-06-19/lookahead_open_loop_smoke_001/open_loop_smoke_summary.json
data/m2/2026-06-19/lookahead_open_loop_smoke_001/open_loop_smoke.svg
```

Run settings:

```text
duration_sec = 300
arrival_interval_sec = 5.0
mode = controller
cooling = fan_on
```

Observed data:

| Run | Start temp | Completed requests | Tokens out | Switch events | Peak temp | Seconds above 63 C | Throttle |
| --- | ---: | ---: | ---: | --- | ---: | ---: | --- |
| `reactive` | 47.2 C | 60 | 1620 | none | 60.9 C | 0.0 s | `0x0` |
| `bounded` | 45.0 C | 60 | 1623 | `switch_to_q4=1`, `switch_to_q8=1`, `cooldown_blocked=3` | 60.9 C | 0.0 s | `0x0` |

What this honestly says:

- The open-loop harness did what it was supposed to do: both runs completed the
  same number of scheduled requests in the same wall-clock window.
- This removes the closed-loop confound where the faster Q4 backend silently
  creates more completed work per minute.
- The 5-minute smoke did not reach 63 C, so it does **not** compare thermal
  control effectiveness. A real comparison needs a longer duration, a shorter
  arrival interval, or a fixed request-count protocol that reaches the switching
  band.


## Open-loop 4-second N=1 pilot note (2026-06-19)

A stronger open-loop pilot used a 4-second scheduled arrival interval for 10
minutes. This is the first run in this branch that both holds completed work
equal and reaches the reactive switching band. It is still **N=1**, so it is a
candidate finding, not a final claim.

```text
data/m2/2026-06-19/lookahead_open_loop_10min_4s_001/reactive/
data/m2/2026-06-19/lookahead_open_loop_10min_4s_001/bounded/
data/m2/2026-06-19/lookahead_open_loop_10min_4s_001/open_loop_4s_summary.json
docs/assets/lookahead_open_loop_4s_pilot.svg
```

Run settings:

```text
duration_sec = 600
arrival_interval_sec = 4.0
mode = controller
cooling = fan_on
bounded look-ahead = 30 sec horizon, min_temp_c=59, max_delta_c=3
```

Observed data:

| Run | Start temp | Completed requests | Tokens out | First switch | Peak temp | Time to 63 C | Seconds above 63 C | Throttle |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| `reactive` | 48.8 C | 150 | 4119 | `switch_to_q4` at 63.1 C | 64.8 C | 460.9 s | 93.2 s | `0x0` |
| `bounded` | 48.3 C | 150 | 4230 | `switch_to_q4` at 60.4 C | 62.0 C | not reached | 0.0 s | `0x0` |

![Open-loop 4-second pilot](assets/lookahead_open_loop_4s_pilot.svg)

What this honestly says:

- Under this open-loop demand, both runs completed the same number of requests.
- The bounded look-ahead controller switched before the reactive threshold and,
  in this N=1 pilot, kept the CPU below 63 C while the reactive controller spent
  93.2 seconds at or above 63 C.
- This is promising enough to repeat, but not enough to claim a general effect.
  The next validation step is N>=3 with the same 4-second open-loop protocol and
  similar starting temperatures.

## Open-loop 4-second N=3 pilot follow-up (2026-06-19)

The N=1 pilot was repeated twice more under the same scheduled-demand protocol.
This gives an N=3 pilot, not a final long-run claim.

```text
data/m2/2026-06-19/lookahead_open_loop_10min_4s_001/
data/m2/2026-06-19/lookahead_open_loop_10min_4s_002/
data/m2/2026-06-19/lookahead_open_loop_10min_4s_003/
data/m2/2026-06-19/lookahead_open_loop_10min_4s_n3/n3_summary.json
docs/assets/lookahead_open_loop_4s_n3_summary.svg
```

Run settings:

```text
duration_sec = 600
arrival_interval_sec = 4.0
completed_requests = 150 per run
mode = controller
cooling = fan_on
bounded look-ahead = 30 sec horizon, min_temp_c=59, max_delta_c=3
```

Observed data:

| Pair | Mode | Start temp | Completed requests | Tokens out | First switch | Peak temp | Seconds above 63 C | Switches to Q4 | Throttle |
| ---: | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| 1 | `reactive` | 48.8 C | 150 | 4119 | 63.1 C | 64.8 C | 93.2 s | 1 | `0x0` |
| 1 | `bounded` | 48.3 C | 150 | 4230 | 60.4 C | 62.0 C | 0.0 s | 19 | `0x0` |
| 2 | `reactive` | 48.3 C | 150 | 4107 | 63.1 C | 63.7 C | 265.7 s | 3 | `0x0` |
| 2 | `bounded` | 45.0 C | 150 | 4218 | 60.4 C | 62.0 C | 0.0 s | 18 | `0x0` |
| 3 | `reactive` | 47.2 C | 150 | 4137 | 63.1 C | 63.7 C | 207.1 s | 4 | `0x0` |
| 3 | `bounded` | 47.2 C | 150 | 4152 | 61.5 C | 62.6 C | 0.0 s | 11 | `0x0` |

Median summary:

| Mode | N | Completed requests | Median peak temp | Median seconds above 63 C | Median Q4 switches | Throttle |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `reactive` | 3 | 150 | 63.7 C | 207.1 s | 3 | `0x0` |
| `bounded` | 3 | 150 | 62.0 C | 0.0 s | 18 | `0x0` |

![Open-loop 4-second N=3 summary](assets/lookahead_open_loop_4s_n3_summary.svg)

What this honestly says:

- With the same completed request count, the bounded look-ahead controller stayed
  below 63 C in 3/3 runs; the reactive controller exceeded 63 C in 3/3 runs.
- This is now a stronger candidate finding than the N=1 result, because the
  direction repeated across three pilot pairs.
- It is not a final claim yet. One bounded run started cooler than its paired
  reactive run, and the bounded controller switched often. The next engineering
  question is whether the same effect survives cleaner start-temperature matching
  and a less chatty controller policy.

## Q4-budget-matched comparison (2026-06-19)

The N=3 pilot above left one confound open: bounded look-ahead spends more time on
the lighter Q4 model, and Q4 simply runs cooler — so "look-ahead helps" was not yet
separated from "more Q4 time helps." To control for this, I added a **non-predictive**
arm: a reactive controller with a lower threshold (`temp_up_c=61`, `temp_down_c=59`)
tuned to spend a similar amount of time on Q4, compared against bounded look-ahead at
matched Q4 residence time on the same 10-minute open-loop protocol, N=3 each.

| Arm | N | Median Q4 time | Median peak temp | Median seconds >= 63 C | Median Q4 switches |
| --- | ---: | ---: | ---: | ---: | ---: |
| `bounded look-ahead` | 3 | 226.7 s | 62.0 C | 0.0 s | 36 |
| `reactive 61/59` | 3 | 235.3 s | 62.6 C | 0.0 s | 32 |

Evidence: `data/m2/2026-06-19/q4_budget_match/q4_budget_match_summary.{json,csv}`,
archived as `data/m2/2026-06-19/artifacts/q4_budget_match_2026-06-19.tar.gz`
(SHA-256 `4bb7df9b20996a14fb28011bfebfb65506892d52d22293b643de491afcccb97c`).

What this honestly says:

- At a similar Q4 residence time, bounded look-ahead's advantage was limited: both
  arms stayed below the threshold (0.0 s >= 63 C) and peak temperatures were close
  (62.0 vs 62.6 C).
- In this condition, the lower temperature looks driven mainly by how much time is
  spent on the lighter Q4 model, not by look-ahead itself.
- The next test asks a different question, switch economy: whether a minimum-residence
  (dwell) rule reaches the same thermal result with fewer, less disruptive switches
  (the bounded arm switched a median of 36 times here).

## Finding

This investigation produced three results, in order.

**1 — A methodology counterexample.** Early switching did not automatically lower
thermal exposure. Because the load generator was closed-loop ("send the next
request immediately"), moving to the faster Q4 path increased completed work in the
same window — so the thermal controller and the benchmark design were coupled. The
question changed from "can I switch earlier?" to "what workload model is fair for
evaluating thermal control?"

**2 — Under a fair (open-loop) load, look-ahead looks promising.** With completed
work held equal (`arrival_interval_sec=4.0`, 150 requests per run), an N=3 pilot
showed bounded look-ahead stayed below 63 °C in 3/3 runs while the reactive
controller exceeded it in 3/3 (median peak 62.0 vs 63.7 °C; median time ≥63 °C
0.0 vs 207.1 s). This is a pilot, not a final claim: the bounded controller
switched often (median 18×), start temperatures were not matched, and output
quality / long-run stability were not evaluated.

**3 — At a matched Q4 budget, the thermal edge is mostly Q4 time, not prediction.**
Holding Q4 residence time roughly equal (bounded look-ahead ~227 s vs a lower-threshold
reactive arm ~235 s on Q4), the two were close: median peak 62.0 vs 62.6 °C and 0.0 vs
0.0 s ≥63 °C (N=3 each). In this condition the lower temperature looks driven mainly by
how much time is spent on the lighter Q4 model, not by look-ahead itself.

## Implication

- Evaluate thermal control under an open-loop load (fixed arrival rate or request
  count). The earlier closed-loop runs remain valid only as throughput / energy
  evidence, not as evidence of reduced thermal exposure.
- Controlling for total Q4 time (a lower-threshold reactive arm at matched Q4
  residence) made bounded look-ahead's thermal edge largely disappear — so on this
  workload the thermal benefit is better explained by Q4 time allocation than by
  prediction. The remaining open question is switch economy: whether a
  minimum-residence (dwell) rule reaches the same thermal result with fewer, less
  disruptive switches.

## Q4-time matched counterfactual protocol

This check separated two explanations:

1. bounded look-ahead helped because it switched **earlier**, or
2. bounded look-ahead helped mainly because it spent **more total time on Q4**.

To test that, I kept the bounded look-ahead runs fixed and added reactive-controller
arms with lower thresholds:

```text
reactive_up61_down59
reactive_up60_down58
reactive_up59_down57
```

Each candidate was run once with the same open-loop protocol:

```text
duration_sec = 600
arrival_interval_sec = 4.0
mode = controller
cooling = fan_on
```

Then I chose the reactive candidate whose `q4_time_sec` was closest to the bounded
look-ahead median. `reactive_up61_down59` was the closest match and was extended
to N=3. The comparison was:

```text
same-ish Q4 residence time -> compare peak temp, seconds >= 63 C, and switches
```

Analysis helper:

```bash
python scripts/analyze_q4_budget_match.py --temp-up 63 \
  --run bounded_001=data/m2/DATE/lookahead_open_loop_10min_4s_001/bounded \
  --run bounded_002=data/m2/DATE/lookahead_open_loop_10min_4s_002/bounded \
  --run bounded_003=data/m2/DATE/lookahead_open_loop_10min_4s_003/bounded \
  --run reactive_up61_down59_001=data/m2/DATE/q4_budget_match/reactive_up61_down59_001 \
  --run reactive_up60_down58_001=data/m2/DATE/q4_budget_match/reactive_up60_down58_001 \
  --run reactive_up59_down57_001=data/m2/DATE/q4_budget_match/reactive_up59_down57_001 \
  --out-json data/m2/DATE/q4_budget_match/q4_budget_match_summary.json \
  --out-csv data/m2/DATE/q4_budget_match/q4_budget_match_summary.csv
```

Interpretation stayed conservative:

- if matched reactive suppresses temperature similarly, Q4 time allocation may be
  the main factor;
- if bounded is still cooler at similar Q4 time, earlier timing may matter;
- if matched reactive has fewer switches, bounded look-ahead's next engineering
  issue is switch chatter, not raw thermal response.

## Minimum-residence follow-up

The follow-up is not "more prediction." It is a switch-economy test: if Q4 time
allocation is the main thermal lever, the next controller should commit to the
lighter model for a minimum residence period and see whether it can keep similar
thermal behavior with fewer Q8/Q4 transitions.

This should be treated as a single pilot first, not a new claim:

```text
bounded look-ahead + min_residence_sec
duration_sec = 600
arrival_interval_sec = 4.0
compare against the Q4-budget-matched runs above
```

The result should be reported even if it fails. A successful pilot would motivate
N=3; an unsuccessful one would simply say that minimum residence did not improve
this controller under the tested settings.

### Single-run pilot: bounded look-ahead + 120 s Q4 residence (2026-06-20)

I ran one 10-minute open-loop pilot after adding `min_residence_sec=120.0` to the
bounded look-ahead controller. This is **not** enough for a new README claim; it is
a directional check for whether the next round is worth running.

```text
duration_sec = 600
arrival_interval_sec = 4.0
completed_requests = 150
look_ahead_sec = 30
min_residence_sec = 120
start_temp_c = 47.2
throttled_hex = 0x0
git_commit = 7f18082c0c64df3121230898a3991c94c9bc933b
```

Observed result:

| Run | Peak temp | Seconds >= 63 C | Q4 time | Q4 fraction | Q4 switches | Q8 switches | Residence blocks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `bounded_dwell120_001` | 62.0 C | 0.0 s | 376.8 s | 0.628 | 4 | 3 | 110 |

Evidence: `data/m2/2026-06-20/min_residence/bounded_dwell120_001`, archived as
`data/m2/2026-06-20/artifacts/min_residence_bounded_dwell120_001_2026-06-20.tar.gz`
(SHA-256 `9e15650bea2275c5388e0fb02466dcce9edbc7a3c519626176cc6cb1d87da440`).

What this honestly says:

- The minimum-residence guard did what it was designed to do: transitions dropped
  from the earlier bounded median of 36 total switches to 7 total switches in
  this pilot.
- The run stayed below 63 C and completed all 150 requests with no throttling or
  safety stop.
- It used much more Q4 time than the Q4-budget-matched comparison (376.8 s here
  vs 226.7 s for bounded look-ahead and 235.3 s for matched reactive). Therefore
  this does **not** prove that dwell gets the same thermal result at the same Q4
  budget.

### Smaller residence sweep: 30 / 60 / 90 s plus 60 s N=3 confirmation

I then ran a gated residence sweep. Each new run waited for the Pi to cool below
50 C and required `get_throttled=0x0` before starting. The 0-second baseline is
the earlier bounded look-ahead N=3 median; the 120-second point is the single
pilot above. The 60-second point initially looked promising, so I repeated it to
N=3 before interpreting it.

```text
duration_sec = 600
arrival_interval_sec = 4.0
cooling = fan_on
start gate = temp <= 50 C and throttled_hex == 0x0
```

| Arm | N | Q4 fraction | Q4 time | Total switches | Residence blocks | Peak temp | Seconds >= 63 C |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dwell 0 s | 3 | 0.378 | 226.8 s | 36 | 0 | 62.0 C | 0.0 s |
| dwell 30 s | 1 | 0.446 | 267.4 s | 17 | 49 | 63.1 C | 2.0 s |
| dwell 60 s | 3 | 0.562 | 337.0 s | 11 | 64 | 62.6 C | 0.0 s |
| dwell 90 s | 1 | 0.714 | 428.4 s | 9 | 106 | 60.9 C | 0.0 s |
| dwell 120 s | 1 | 0.628 | 376.8 s | 7 | 110 | 62.0 C | 0.0 s |

![Minimum-residence dwell sweep: total switches fall from 36 to 7 only as the Q4 residence fraction rises (0.378 up to 0.714); the 30 s point crossed 63 C. N=3 at 0 s and 60 s, N=1 otherwise.](assets/lookahead_dwell_sweep.svg)

Evidence:

- `data/m2/2026-06-20/min_residence_tradeoff_5pt_with_dwell60_n3.csv`
- `data/m2/2026-06-20/min_residence_tradeoff_5pt_with_dwell60_n3.json`
- Archive:
  `data/m2/2026-06-20/artifacts/min_residence_gated_sweep_2026-06-20.tar.gz`
  (SHA-256 `a773e4addda4c691e29f8e4352437a824f1c7f847be090b61c241be5b0624239`)

What this honestly says:

- A minimum-residence rule can reduce switch frequency: dwell 60 s reduced the
  median total switch count from 36 to 11.
- That reduction was not free. The same dwell 60 s median increased Q4 fraction
  from 0.378 to 0.562 and Q4 time from 226.8 s to 337.0 s.
- The single best-looking 60-second run did not generalize cleanly after N=3.
  This is exactly why the follow-up was needed.
- Longer residence values increasingly fought the controller (`residence_blocked`
  rose to 106-110 at 90-120 s), so the current evidence does not justify claiming
  an optimal dwell setting.

The useful finding is therefore narrower: **anti-flap control trades switch
economy against Q4 residence time on this workload.** The next design step should
not be "more dwell"; it should define how much extra Q4 time is acceptable for a
given reduction in switching.
