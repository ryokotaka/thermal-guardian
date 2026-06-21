# M3 Thermal-continuity protocol (proposed)

> **What this is:** a proposed experiment to test whether graceful degradation
> actually pays off: under a thermal stress that fixed Q8 cannot sustain, can the
> controller step down to Q4, avoid throttle / safety-stop, and keep serving the
> same open-loop demand? **Who it's for:** anyone judging whether the controller
> is worth its cost. **Status:** proposed, not yet run.

## Why this experiment

The fan-on N=5 evaluation never thermally stressed the Pi: nothing throttled, even
fixed Q8 (peak 65.3 C). So it showed the switching mechanism and its cost, not the
payoff. This protocol creates a controlled thermal stress and asks one question:

> Under a load that drives fixed Q8 toward throttling or a safety stop, does the
> controller — by stepping down to Q4 — avoid throttle / safety-stop and keep
> serving the same open-loop demand?

Primary thing it can show, directly measurable:

- **Continuity under thermal pressure.** If fixed Q8 reaches throttle or the
  safety stop, the controller keeps serving the same demand without either.

Secondary observation:

- **Temperature ceiling behavior.** The controller's configured ceiling is
  treated as a thermal-stress control point and a margin before throttling. It is
  not treated as a proven Raspberry Pi 5 wear threshold.

## What it will not show

- **Output quality.** Q4's answers may be worse; that is the separate
  quality-sensitive axis, not measured here.
- **Hardware wear or lifespan.** Raspberry Pi documentation gives throttling
  thresholds and an operating-temperature range, but not a device-specific
  lifespan-vs-temperature curve. This protocol therefore does not claim reduced
  wear or longer lifespan.
- **Energy efficiency.** Power is not required for the primary M3 claim. It can
  be logged as auxiliary context, but the acceptance condition is thermal
  continuity, not J/token or watts.
- Anything beyond this device, prompt, cooling condition, and ceiling.

## Fixed conditions

- **Hardware:** Raspberry Pi 5 (4 GB). The **heatsink stays attached**; only the
  active fan is removed for the stress condition, so passive cooling remains and
  the temperature climb is slower and safer.
- **Models:** the same Qwen2.5-1.5B `Q8_0` and `Q4_K_M` GGUF used in M2.
- **Workload:** open-loop, fixed arrival rate (`--arrival-interval-sec`), same
  prompt, `temperature = 0`, `max_tokens = 64`. Open-loop keeps the demand equal
  across arms, so the thermal comparison is not confounded by a faster model doing
  more work.
- **Cooling:** `fan_off` (heatsink on). A heavier-load `fan_on` variant is a
  fallback if fan-off climbs too fast to control.
- **Ceiling:** set the controller `temp_up_c` to the target ceiling and
  `temp_down_c` a few degrees below. The ceiling must sit **above** Q4's fan-off
  equilibrium (see Step 0) so the controller can actually hold it.
- **Duration:** a fixed window (e.g. 1200 s) or until a safety stop, whichever
  comes first.
- **Start gate:** each run waits for CPU temp <= 50 C and `get_throttled = 0x0`.
- **Repetitions:** N=1 smoke first; if the effect appears, N=3.
- **Power logging:** optional. Do not make USB power-meter readings part of the
  pass/fail result unless the question changes to energy efficiency.

## Step 0: smoke run (find the operating point)

Before the comparison, run one short fan-off pilot to learn:

1. Q4's fan-off equilibrium temperature (sets the lowest usable ceiling).
2. An arrival rate that keeps the SoC busy without reaching the safety cap in
   seconds.

Pick the ceiling a few degrees above Q4's equilibrium, and clearly below the
throttle point.

## Arms

1. `fixed_q8` — expected to climb past the ceiling; record time-to-ceiling,
   time-to-throttle (if any), and whether it safety-stops.
2. `controller` — switches to Q4 near the ceiling; expected to plateau at or below
   it and serve the full window.
3. `fixed_q4` — reference: where Q4 alone settles fan-off.

Rotate arm order, and let the Pi cool fully (temp <= 50 C, `get_throttled = 0x0`)
between runs.

## Safety rules (mandatory)

- Hard safety stop at `safety_temp_c` (<= 82 C) or on any `get_throttled` change.
- A safety stop on `fixed_q8` **is a valid result, not a failure** — record it and
  do not push further.
- Keep the heatsink on. Do not run fan-off unattended.
- Minimize the number of fan-off runs; cool fully between them.

## Run commands

```bash
# fixed Q8 under fan-off stress
python -m thermal_guardian.m2 run \
  --config m2.local.json --mode q8_fixed \
  --output-dir data/m2/YYYY-MM-DD/m3_stress/q8_fixed_001 \
  --arrival-interval-sec <R> --duration-sec 1200 \
  --cooling fan_off --safety-temp-c 82 --prompt-id-prefix m3

# fixed Q4 reference
python -m thermal_guardian.m2 run \
  --config m2.local.json --mode q4_fixed \
  --output-dir data/m2/YYYY-MM-DD/m3_stress/q4_fixed_001 \
  --arrival-interval-sec <R> --duration-sec 1200 \
  --cooling fan_off --safety-temp-c 82 --prompt-id-prefix m3

# controller with the ceiling config (temp_up_c = ceiling)
python -m thermal_guardian.router --config config.m3.fan_off.local.json
python -m thermal_guardian.m2 run \
  --config m2.local.json --mode controller \
  --output-dir data/m2/YYYY-MM-DD/m3_stress/controller_001 \
  --arrival-interval-sec <R> --duration-sec 1200 \
  --cooling fan_off --safety-temp-c 82 --prompt-id-prefix m3
```

## Required metrics (per run)

`peak_temp_c`, `time_to_ceiling_sec`, `time_to_throttle_sec` (if any),
`seconds_above_ceiling`, `seconds_throttled`, `safety_stop` (bool),
`requests_completed`, `controller_survived_full_window` (bool), and `q4_fraction`
for the controller.

Optional context: USB meter or PMIC readings may be recorded, but they are not
needed to decide whether the controller preserved service continuity under
thermal pressure.

## What would count as the payoff

- `fixed_q8` reaches the ceiling / throttle / safety stop, while `controller` holds
  at or below the ceiling and serves the full window → graceful degradation
  demonstrated.
- `controller` only delays the crossing → honest partial result: it extends safe
  operating time but does not prevent the crossing under sustained fan-off.
- Nothing crosses the ceiling → the stress was too low; raise the arrival rate or
  reduce cooling.

Each of these is a reportable result.

## Completion criteria

- All three arms run from matched cool starts, N >= 1 (N=3 if the effect appears).
- Every run has telemetry, requests, and manifest logs (plus controller events),
  and its safety outcome recorded.
- A summary states the ceiling and arrival rate used, and separates measured facts
  from interpretation. The finding and its wording are written by the author.
