#!/usr/bin/env python3
"""Generate a synthetic kite session (records.csv + truth.json [+ synthetic.fit])."""
import argparse, json, os
from kitelog.simulate import simulate, SimConfig
from kitelog.fitio import write_fit

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="out/sim")
ap.add_argument("--duration", type=float, default=1500)
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--chop", type=float, default=1.0)
ap.add_argument("--baro-noise", type=float, default=3.0)
ap.add_argument("--dyn", type=float, default=0.15)
ap.add_argument("--fit", action="store_true", help="also write synthetic.fit (fit_tool)")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
cfg = SimConfig(duration_s=a.duration, seed=a.seed, chop=a.chop, baro_noise_pa=a.baro_noise, dyn_coupling=a.dyn)
rec, truth, ideal = simulate(cfg)
rec.to_csv(os.path.join(a.out, "records.csv"), index=False)
ideal.to_csv(os.path.join(a.out, "ideal_25hz.csv"), index=False)
json.dump(truth, open(os.path.join(a.out, "truth.json"), "w"), indent=1, default=float)
if a.fit:
    write_fit(rec, os.path.join(a.out, "synthetic.fit"))
print(f"{len(rec)} records, {len(truth['jumps'])} jumps, {truth['tack_count']} tacks -> {a.out}")
