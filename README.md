# Thermal Guardian

A temperature-aware LLM router for the Raspberry Pi 5. It starts with a Q8 model,
uses a lighter Q4 version when the CPU gets hot, and returns to Q8 after it cools.
Both models run locally behind one chat endpoint.

I built this to test whether switching models could keep a small device serving
requests under sustained heat. In the fan-off experiment, the controller completed
all 200 requests in each of three runs. Fixed Q8 triggered the experiment's safety
stop in all three. Fixed Q4 also completed every run; whether using Q8 some of the
time improves the answers is still untested.

![Fan-off experiment: fixed Q8 reached the test safety stop; the controller and fixed Q4 completed all 200 requests](docs/assets/m3_thermal_continuity.svg)

## Results

The experiments used a Raspberry Pi 5 (4 GB) and Qwen2.5-1.5B-Instruct, served by
`llama.cpp` as `Q8_0` and `Q4_K_M` GGUF models. Q8 and Q4 refer to different weight
quantizations of the same model. Output quality was not evaluated.

**Fan disconnected, heatsink attached, airflow blocked:** one request every six
seconds for 20 minutes, three runs per mode. Values below are medians.

| Mode | Completed requests | Peak CPU temperature | Runs reaching the safety stop |
| --- | ---: | ---: | ---: |
| Fixed Q8 | 100 / 200 | 81.2 °C | 3 / 3 |
| Controller | 200 / 200 | 77.9 °C | 0 / 3 |
| Fixed Q4 | 200 / 200 | 79.0 °C | 0 / 3 |

Fixed Q8 recorded `get_throttled=0x80000`, the
[historical soft-temperature-limit flag](https://www.raspberrypi.com/documentation/computers/os.html#get_throttled),
and the test harness stopped the load. This was a controlled stop, not an
observed device shutdown. The controller spent about 78% of the time on Q4. Its
77.9 °C median peak also exceeded the 71.1 °C switching threshold, so that threshold
is not a temperature guarantee.

Starts ranged from 55.4 to 58.7 °C; two runs affected by suspected external airflow
were excluded and repeated. The [M3 protocol and results](docs/m3_thermal_stress_protocol.md)
record those conditions and exclusions.

**With active cooling**, all five 30-minute runs per mode finished without a
throttle flag or safety stop. Fixed Q4 had the best median speed and energy per
token: 11.27 tok/s and 0.677 J/token, compared with the controller's 11.23 tok/s and
0.731 J/token. Energy came from manual USB-meter readings for each run. These
closed-loop runs sent the next request after the previous response, so faster
modes completed more work. See the [fan-on results](docs/m2_full_fan_on_n5_results.md).

I also tried predicting temperature from its recent slope. At similar time spent
on Q4, the median peak differed by only 0.6 °C from a lower-threshold reactive
controller. That comparison did not establish a separate benefit from prediction.
The [look-ahead notebook](docs/findings_lookahead.md) covers the controls and the
trade-off between fewer switches and more time on Q4.

## Try it locally

Requires Python 3.11 or newer. No model downloads or Raspberry Pi are needed for
the demo.

```bash
git clone https://github.com/ryokotaka/thermal-guardian.git
cd thermal-guardian
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
python -m thermal_guardian.router --dry-run --fake-monitor
```

In another terminal:

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-Edge-Prompt-Id: demo-001' \
  -d '{"model":"thermal-guardian","messages":[{"role":"user","content":"Say hello."}],"stream":false}'
```

The response says `dry-run routed to q8`. `--dry-run` skips model inference;
`--fake-monitor` supplies a constant simulated 40 °C reading. Neither demonstrates
real thermal behavior. Requests and controller samples are written to
`logs/requests.csv` and `logs/events.csv`.

To try HTTP forwarding, run each of these commands in its own terminal with the
virtual environment activated. Stop the dry-run router first.

```bash
python scripts/fake_llama_server.py --port 8081 --name q8
python scripts/fake_llama_server.py --port 8082 --name q4
python -m thermal_guardian.router --config config.example.json --fake-monitor
```

Repeat the same `curl` request; the reply now comes from the fake Q8 backend.

## Run on a Raspberry Pi

Install the package as above. You also need `vcgencmd`, a working `llama-server`
build, and the two GGUF model files. The measured setup used Raspberry Pi OS
Bookworm 64-bit; model weights are not included.

```bash
cp m0.example.json m0.local.json
cp config.example.json config.local.json
```

Edit `m0.local.json` with your `llama-server` executable and model paths. Check that
the ports match `q8_url` and `q4_url` in `config.local.json`, then start the backends:

```bash
python -m thermal_guardian.m0 start --config m0.local.json
python -m thermal_guardian.m0 check --config m0.local.json
python -m thermal_guardian.router --config config.local.json
```

Use the same chat request from the local demo. Leave out `--fake-monitor` for real
runs. If required telemetry is missing or malformed, the router returns HTTP 503
instead of treating the device as cool. It resumes accepting requests when it can
read telemetry again. The M2 experiment runner records a safety stop on telemetry
failure.

The default switch points are 70 °C for Q8 → Q4 and 60 °C for Q4 → Q8, with at least
ten seconds between switches. The gap between thresholds prevents repeated
switches around a single temperature. These defaults differ from the thresholds
used in the experiments; use the linked protocols to reproduce them.

The [M0 checklist](docs/m0_checklist.md) covers model setup. The
[M2 protocol](docs/m2_full_protocol.md) covers comparisons and power measurements.
The [M3 protocol](docs/m3_thermal_stress_protocol.md) includes the fan-off start
gates and stop rules; its 82 °C cap belongs to the test harness, not the router.

## Scope

The implemented API is `POST /v1/chat/completions` with a buffered response.
Non-streaming chat is the tested path; streaming, other OpenAI endpoints,
authentication, and automatic retries to the other model are not implemented.
The server binds to localhost by default.

Both models stay loaded. Switching changes where the next request goes; it does
not migrate an in-flight request or unload model weights. The controller uses
temperature thresholds, with optional look-ahead and minimum Q4 residence time.
It does not assess answer quality or guarantee a maximum CPU temperature.

These results cover one device, one prompt workload, and the cooling conditions
above. Raw CSVs, meter photos, and local configs are excluded from this repository.
The [evidence notes](docs/evidence_log.md) and linked experiment reports document
the recorded results; some reports include hashes for locally held archives. The
public repository alone is not enough to recompute every aggregate.

Related project: [Pose Guardian](https://github.com/ryokotaka/pose-guardian), which
switches pose-estimation models under CPU and resource pressure.

## License

[Apache 2.0](LICENSE). Third-party models and runtimes have their own licenses.
