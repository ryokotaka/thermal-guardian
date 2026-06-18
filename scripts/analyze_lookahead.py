#!/usr/bin/env python3
"""Analyze thermal dynamics and compare reactive vs look-ahead controller runs.

This script does DATA work only — it derives quantities from telemetry and draws
a plot. Interpreting them into a finding is the author's job (see the
"Findings: thermal dynamics and look-ahead control" section of the README).

It is dependency-free (standard library only) and emits a self-contained SVG.

Per run it reports, from `telemetry.csv` (columns `elapsed_sec`, `temp_c`):
  - tau_63_sec   : first-order time constant via the 63.2% rise method
  - time_to_up_s : when temperature first reached the upper threshold
  - peak_temp_c  : maximum temperature
  - overshoot_c  : peak_temp_c - temp_up (how far it ran past the threshold)
  - secs_above_up: total time spent at/above the upper threshold

Usage:
    python scripts/analyze_lookahead.py \
        --temp-up 63 \
        --run reactive=data/m2/.../reactive/telemetry.csv \
        --run predictive=data/m2/.../predictive/telemetry.csv \
        --out-json data/m2/.../lookahead_summary.json \
        --out-svg docs/assets/m2_lookahead_compare.svg
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

PALETTE = ["#3f72d4", "#d9822b", "#1f9d76", "#9aa6b2"]


def parse_telemetry(path: str) -> list[tuple[float, float]]:
    series: list[tuple[float, float]] = []
    with open(path, newline="") as fp:
        for row in csv.DictReader(fp):
            try:
                series.append((float(row["elapsed_sec"]), float(row["temp_c"])))
            except (KeyError, ValueError):
                continue
    series.sort(key=lambda p: p[0])
    return series


def derive(series: list[tuple[float, float]], temp_up: float) -> dict:
    if len(series) < 2:
        raise ValueError("telemetry needs at least 2 rows")
    t0_temp = series[0][1]
    peak = max(v for _, v in series)
    # Plateau estimate: mean of the last 10% of samples (or the max as a floor).
    tail = series[max(1, int(len(series) * 0.9)):]
    plateau = max(sum(v for _, v in tail) / len(tail), peak * 0.999)
    # 63.2% rise time = first-order time constant tau.
    target = t0_temp + 0.632 * (plateau - t0_temp)
    tau = next((e for e, v in series if v >= target), None)
    time_to_up = next((e for e, v in series if v >= temp_up), None)
    above = [e for e, v in series if v >= temp_up]
    secs_above = (max(above) - min(above)) if above else 0.0
    return {
        "rows": len(series),
        "duration_sec": round(series[-1][0], 1),
        "start_temp_c": round(t0_temp, 2),
        "plateau_temp_c": round(plateau, 2),
        "peak_temp_c": round(peak, 2),
        "tau_63_sec": round(tau, 1) if tau is not None else None,
        "time_to_up_sec": round(time_to_up, 1) if time_to_up is not None else None,
        "overshoot_c": round(peak - temp_up, 2),
        "secs_above_up": round(secs_above, 1),
    }


def build_svg(runs: dict[str, list[tuple[float, float]]], temp_up: float,
              derived: dict[str, dict]) -> str:
    all_pts = [p for s in runs.values() for p in s]
    xmax = max(e for e, _ in all_pts)
    ymin = min(v for _, v in all_pts)
    ymax = max(max(v for _, v in all_pts), temp_up)
    ylo, yhi = ymin - 2, ymax + 2
    L, R, T, B, W, H = 70, 920, 100, 430, 960, 520

    def X(e: float) -> float:
        return L + (e / xmax) * (R - L) if xmax else L

    def Y(v: float) -> float:
        return B - (v - ylo) / (yhi - ylo) * (B - T)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="Arial, Helvetica, sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#f6f8f8"/>',
        '<text x="40" y="44" font-size="22" font-weight="700" fill="#16241f">'
        'Look-ahead vs reactive: CPU temperature over time</text>',
        '<text x="40" y="70" font-size="13" fill="#56635f">Data view only '
        '— the finding/implication is written by the author.</text>',
        f'<rect x="{L}" y="{T}" width="{R-L}" height="{B-T}" fill="#ffffff" stroke="#e3e9e7"/>',
    ]
    # y gridlines/labels
    steps = 5
    for i in range(steps + 1):
        v = ylo + (yhi - ylo) * i / steps
        y = Y(v)
        parts.append(f'<line x1="{L}" y1="{y:.1f}" x2="{R}" y2="{y:.1f}" stroke="#eef2f0"/>')
        parts.append(f'<text x="{L-8}" y="{y+4:.1f}" text-anchor="end" font-size="11" '
                     f'fill="#8a948f">{v:.0f}</text>')
    # x labels (minutes)
    for i in range(7):
        e = xmax * i / 6
        x = X(e)
        parts.append(f'<text x="{x:.1f}" y="{B+18:.1f}" text-anchor="middle" '
                     f'font-size="11" fill="#8a948f">{e/60:.0f}</text>')
    parts.append(f'<text x="{(L+R)/2:.0f}" y="{B+38}" text-anchor="middle" '
                 f'font-size="12" fill="#56635f">minutes into run</text>')
    # upper-threshold line
    yt = Y(temp_up)
    parts.append(f'<line x1="{L}" y1="{yt:.1f}" x2="{R}" y2="{yt:.1f}" stroke="#c9882f" '
                 f'stroke-width="1.3" stroke-dasharray="5 4"/>')
    parts.append(f'<text x="{R-4}" y="{yt-5:.1f}" text-anchor="end" font-size="11" '
                 f'fill="#a96f1f">upper threshold {temp_up:.0f} °C</text>')
    # per-run lines + time-to-up markers
    legend = []
    for idx, (label, series) in enumerate(runs.items()):
        color = PALETTE[idx % len(PALETTE)]
        pts = " ".join(f"{X(e):.1f},{Y(v):.1f}" for e, v in series)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.2"/>')
        ttu = derived[label]["time_to_up_sec"]
        if ttu is not None:
            parts.append(f'<line x1="{X(ttu):.1f}" y1="{yt:.1f}" x2="{X(ttu):.1f}" '
                         f'y2="{B}" stroke="{color}" stroke-width="1" stroke-dasharray="3 3" '
                         f'opacity="0.5"/>')
        legend.append((label, color))
    lx = L
    for label, color in legend:
        parts.append(f'<rect x="{lx}" y="{B+52}" width="16" height="4" fill="{color}"/>')
        parts.append(f'<text x="{lx+22}" y="{B+59}" font-size="12.5" fill="#45524f">{label}</text>')
        lx += 60 + len(label) * 8
    parts.append('</svg>')
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--temp-up", type=float, required=True, help="upper threshold (C)")
    ap.add_argument("--run", action="append", required=True,
                    metavar="LABEL=telemetry.csv", help="repeatable; e.g. reactive=path")
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--out-svg", default=None)
    args = ap.parse_args()

    runs: dict[str, list[tuple[float, float]]] = {}
    derived: dict[str, dict] = {}
    for spec in args.run:
        if "=" not in spec:
            raise SystemExit(f"--run expects LABEL=path, got: {spec}")
        label, path = spec.split("=", 1)
        series = parse_telemetry(path)
        runs[label] = series
        derived[label] = derive(series, args.temp_up)

    summary = {"temp_up_c": args.temp_up, "runs": derived}
    print(json.dumps(summary, indent=2))
    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(json.dumps(summary, indent=2))
    if args.out_svg:
        Path(args.out_svg).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_svg).write_text(build_svg(runs, args.temp_up, derived))


if __name__ == "__main__":
    main()
