#!/usr/bin/env python3
"""Analyse a KiteLog recording (FIT from the watch, or records.csv from the simulator)."""
import argparse, json, os
import numpy as np, pandas as pd
from kitelog.detect import build_streams, detect_jumps, DetectParams
from kitelog.height import estimate_height, HeightParams
from kitelog.session import jumps_table, session_stats
from kitelog.fitio import read_fit
from kitelog import report
from dataclasses import asdict

ap = argparse.ArgumentParser()
ap.add_argument("input", help=".fit or records.csv")
ap.add_argument("--out", default=None)
ap.add_argument("--truth", default=None, help="truth.json from the simulator (adds comparison)")
ap.add_argument("--plots", action="store_true")
ap.add_argument("--a-eff", type=float, default=3.2, help="airtime-model net acceleration (m/s^2)")
a = ap.parse_args()
out = a.out or os.path.splitext(a.input)[0] + "_analysis"
os.makedirs(out, exist_ok=True)
if a.input.lower().endswith(".fit"):
    rec, info = read_fit(a.input)
    print("FIT:", {k: v for k, v in info.items() if k != "field_descriptions"})
else:
    rec = pd.read_csv(a.input)
if "acc_0" not in rec or rec["acc_0"].notna().sum() == 0:
    raise SystemExit(f"no accelerometer batches in {len(rec)} records - the sensor listener never fired "
                     "(PLAN.md S1: check the b/n counter on the watch; in the simulator, play activity data)")
s = build_streams(rec)
print(f"streams: {len(s.t)} samples, valid {s.valid.mean():.4f}, batches {s.n_batches}, dup {s.n_dup}, gaps {s.n_gap}")
cands, F = detect_jumps(s, DetectParams())
hp = HeightParams(a_eff=a.a_eff)
heights = [estimate_height(c.t_to, c.t_land, s.press_t, s.press, hp) for c in cands]
jumps = jumps_table(s, cands, heights)
stats = session_stats(s, jumps)
jumps.to_csv(os.path.join(out, "jumps.csv"), index=False)
json.dump(asdict(stats), open(os.path.join(out, "session.json"), "w"), indent=1)
report.export_gpx(s, os.path.join(out, "track.gpx"))
report.export_map_html(s, jumps, os.path.join(out, "map.html"))
acc = jumps[jumps["accepted"]] if len(jumps) else jumps
print(f"jumps: {len(acc)} accepted / {len(jumps)} candidates;  max height {stats.max_height_m:.1f} m, "
      f"max airtime {stats.max_airtime_s:.2f} s, longest {stats.longest_jump_m:.0f} m; tacks {stats.tacks}; "
      f"distance {stats.distance_m/1000:.1f} km; max speed {stats.max_speed_kmh:.1f} km/h")
cols = ["jump", "airtime_s", "height_m", "height_method", "distance_m", "takeoff_speed_kmh", "takeoff_peak_g", "landing_peak_g", "n_baro_samples"]
if len(acc):
    with pd.option_context("display.width", 160, "display.max_rows", 200):
        print(acc[cols].round(2).to_string(index=False))
if a.truth:
    truth = json.load(open(a.truth))
    off = truth["tmr_offset_s"]
    print("\ntruth comparison (height: true -> est):")
    for j in truth["jumps"]:
        m = [k for k, c in enumerate(cands) if c.t_to < j["t_land"] + off and c.t_land > j["t_to"] + off and c.accepted]
        if m:
            row = jumps.iloc[m[0]]
            print(f"  {j['h_max']:5.2f} -> {row.height_m:5.2f} m   airtime {j['airtime']:.2f} -> {row.airtime_s:.2f} s   dist {j['distance']:.0f} -> {row.distance_m:.0f} m")
        else:
            print(f"  {j['h_max']:5.2f} m MISSED")
if a.plots:
    report.plot_session(s, F, jumps, os.path.join(out, "session.png"))
    for k, (c, h) in enumerate(zip(cands, heights)):
        if c.accepted:
            report.plot_jump(s, F, c, h, os.path.join(out, f"jump_{k+1:02d}.png"), prm=hp)
    print("plots ->", out)
