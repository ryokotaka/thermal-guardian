# M0 Checklist

> **What this is:** the step-by-step bring-up check for getting two models
> running on the Pi. **Who it's for:** anyone reproducing the setup. **Bottom
> line:** confirms the models load and answer — not a performance claim.

This checklist is for the M0 acceptance pass on Raspberry Pi 5. It is not a
performance claim. Record only facts that were actually checked on the device.

## Before Running

- Use Raspberry Pi 5 4GB with Raspberry Pi OS Bookworm 64-bit.
- Keep the public examples as placeholders.
- Copy `m0.example.json` to `m0.local.json` and fill in approved local model
  paths only after the model choice is approved by the author.
- For an acceptance run, set `rss_output` in `m0.local.json` to a dated evidence
  path such as `data/m0/YYYY-MM-DD/server_rss.csv`.
- Confirm `m0.local.json` is ignored before adding files:

```bash
git check-ignore m0.local.json
```

## Approved Model Candidate

The author approved the following M0 bring-up candidate on 2026-06-14:

- Model repo: `Qwen/Qwen2.5-1.5B-Instruct-GGUF`
- License: Apache-2.0, as shown on the Hugging Face model card
- Q8 file: `qwen2.5-1.5b-instruct-q8_0.gguf`
- Q4 file: `qwen2.5-1.5b-instruct-q4_k_m.gguf`

Memory caution:

- The model card lists Q8_0 at about 1.89 GB and Q4_K_M at about 1.12 GB.
- The combined file size is about 3 GB before runtime overhead, KV cache, and
  two `llama-server` processes.
- Raspberry Pi 5 4GB acceptance still requires `rss`, `chat-smoke`, and power
  checks. Model selection alone does not establish an M0 pass, long-run
  stability, latency improvement, or J/token improvement.

## Server Bring-Up

Print the commands first:

```bash
python -m thermal_guardian.m0 start --config m0.local.json --dry-run
```

Start both `llama-server` processes:

```bash
python -m thermal_guardian.m0 start --config m0.local.json
```

Check both health endpoints:

```bash
python -m thermal_guardian.m0 check --config m0.local.json
```

Send one minimal chat request to each server:

```bash
python -m thermal_guardian.m0 chat-smoke \
  --config m0.local.json \
  --output data/m0/YYYY-MM-DD/chat_smoke.csv
```

Record RSS for both saved PIDs:

```bash
python -m thermal_guardian.m0 rss --config m0.local.json
```

## Power-Meter Acceptance Check

Take one PMIC sample while idle:

```bash
python -m thermal_guardian.m0 pmic-sample \
  --output data/m0/YYYY-MM-DD/pmic_idle.csv \
  --label idle
```

Take one PMIC sample while the Pi is under load:

```bash
python -m thermal_guardian.m0 pmic-sample \
  --output data/m0/YYYY-MM-DD/pmic_load.csv \
  --label load
```

The `pmic-sample` command prints whether `EXT5V_V` is above 4.9V. Treat that as
a check prompt, not as automatic acceptance. The author must confirm the final
power setup.

Also confirm manually:

- `vcgencmd get_throttled` has no new throttling bits during the acceptance run.
- The USB power meter can record offline for 30 minutes and export CSV once.
- The selected cable is eMarked and 0.5m or shorter.

## DECISIONS.md Entries

Append a one-line decision only after the author has confirmed it. Do not record
planned or assumed values as decisions.

Useful M0 decision entries:

- approved model family and exact Q8/Q4 GGUF filenames
- USB power meter model
- selected cable
- M0 acceptance date and short evidence path

## M0 Passing Statement

After the checks above pass, the supported statement is:

`M0 passes: two approved llama-server processes can stay up, both answer a
minimal chat request, and RSS plus power-setup check logs are recorded.`

M0 alone is not evidence of latency improvement, J/token improvement, long-run
stability, or thermal-control effectiveness.
