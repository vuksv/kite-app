#!/usr/bin/env python3
"""Compare KiteLog jumps with a reference list (Surfr phone app, WOO, video) and fit a_eff.

usage: calibrate.py <kitelog.fit|jumps.csv> <reference.csv> [--window 12]

reference.csv columns (header required, extra columns ignored):
  time      - local clock time "HH:MM:SS" or ISO "2026-11-04T14:03:21" or unix seconds
  height    - metres (optional)
  airtime   - seconds (optional)
  tz        - optional per-file override: pass --tz "-03:00" instead
Matches each reference jump to the nearest KiteLog jump within --window seconds, prints the
pairs, detection agreement, airtime bias and the least-squares a_eff for h = a_eff*T^2/8.
"""
import argparse, datetime as dt, os, sys
import numpy as np, pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("kitelog"); ap.add_argument("reference")
ap.add_argument("--window", type=float, default=12.0)
ap.add_argument("--tz", default=None, help="UTC offset of the reference clock, e.g. -03:00 (default: local machine)")
ap.add_argument("--date", default=None, help="date for HH:MM:SS references (default: date of the session)")
a = ap.parse_args()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if a.kitelog.lower().endswith(".fit"):
    from kitelog.fitio import read_fit
    from kitelog.detect import build_streams, detect_jumps, DetectParams
    from kitelog.height import estimate_height, HeightParams
    from kitelog.session import jumps_table
    rec, _ = read_fit(a.kitelog); s = build_streams(rec); cands, F = detect_jumps(s, DetectParams())
    kl = jumps_table(s, cands, [estimate_height(c.t_to, c.t_land, s.press_t, s.press, HeightParams()) for c in cands])
else:
    kl = pd.read_csv(a.kitelog)
kl = kl[kl.accepted].copy()
if kl.empty:
    sys.exit("no accepted KiteLog jumps")
ref = pd.read_csv(a.reference)
tz = dt.timezone.utc
if a.tz:
    sign = -1 if a.tz.startswith("-") else 1; hh, mm = a.tz.lstrip("+-").split(":"); tz = dt.timezone(sign * dt.timedelta(hours=int(hh), minutes=int(mm)))
elif os.environ.get("TZ") is None:
    tz = dt.datetime.now().astimezone().tzinfo
session_date = dt.datetime.fromtimestamp(float(kl.ts_to.min()), tz).date() if a.date is None else dt.date.fromisoformat(a.date)

def to_unix(v):
    v = str(v).strip()
    try:
        return float(v)
    except ValueError:
        pass
    if "T" in v or " " in v:
        d = dt.datetime.fromisoformat(v.replace(" ", "T"))
    else:
        d = dt.datetime.combine(session_date, dt.time.fromisoformat(v))
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz)
    return d.timestamp()

ref["t"] = ref["time"].map(to_unix)
rows, used = [], set()
for _, r in ref.sort_values("t").iterrows():
    d = (kl.ts_to - r.t).abs()
    cand = d[(d <= a.window) & (~kl.index.isin(used))]
    if cand.empty:
        rows.append(dict(ref_time=dt.datetime.fromtimestamp(r.t, tz).strftime("%H:%M:%S"), ref_h=r.get("height", np.nan), ref_air=r.get("airtime", np.nan), kl="-", dt_s=np.nan, kl_air=np.nan, kl_h=np.nan))
        continue
    i = cand.idxmin(); used.add(i); k = kl.loc[i]
    rows.append(dict(ref_time=dt.datetime.fromtimestamp(r.t, tz).strftime("%H:%M:%S"), ref_h=r.get("height", np.nan), ref_air=r.get("airtime", np.nan),
                     kl=int(k.jump), dt_s=round(k.ts_to - r.t, 1), kl_air=round(k.airtime_s, 2), kl_h=round(k.height_m, 2)))
out = pd.DataFrame(rows); pd.set_option("display.width", 200)
print(out.to_string(index=False))
m = out[out.kl != "-"]
print(f"\nreference jumps {len(out)}, matched {len(m)}, KiteLog jumps not in reference {len(kl) - len(m)}")
if "airtime" in ref and m.ref_air.notna().any():
    e = (m.kl_air - m.ref_air.astype(float)).dropna()
    print(f"airtime: KiteLog - reference = {e.mean():+.2f} s mean, {e.abs().mean():.2f} s MAE (n={len(e)})")
if "height" in ref and m.ref_h.notna().any():
    mm = m[m.ref_h.notna()]
    T = mm.kl_air.astype(float).to_numpy(); h = mm.ref_h.astype(float).to_numpy()
    a_eff = 8 * np.sum(h * T ** 2) / np.sum(T ** 4)
    pred = a_eff * T ** 2 / 8
    print(f"height: best a_eff = {a_eff:.2f} m/s^2 (the pipeline now uses 1.39 on T + 1.25 s); residual MAE {np.abs(pred - h).mean():.2f} m over {len(h)} jumps")
    print("   -> set 'Airtime-model a_eff' in the web settings to that value, or analyze.py --a-eff")
