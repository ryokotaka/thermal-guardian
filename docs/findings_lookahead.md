# Findings: thermal dynamics & look-ahead control

> **Working doc.** This is the "question → measurement → finding → implication"
> scaffold for the look-ahead investigation. The assistant fills *question* and
> *measurement* (apparatus, protocol, plots, derived numbers). The **finding and
> implication are written by the author** from the run data — the assistant does
> not write them. Once the finding is backed by reproducible data, lift it into a
> `## Findings` section of the README.

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
- Reactive config: `config.m2.fan_on.example.json` (no `look_ahead_sec` → 0).
- Look-ahead config: `config.m2.fan_on.predictive.example.json`
  (`look_ahead_sec: 30.0` — a **starting** horizon to sweep/tune, not a tuned value).

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

## Finding

> _Author writes this from the run data. Not filled by the assistant._

## Implication

> _Author writes this. Not filled by the assistant._
