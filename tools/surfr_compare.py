#!/usr/bin/env python3
"""Compare KiteLog jumps with a Surfr phone-app jump list recorded in parallel.

usage: surfr_compare.py <session.fit> <surfr.csv> [--quiet]

surfr.csv (as in sessions/*_surfr.csv): n,height,airtime,distance,t_since_surfr_start_s,time_precision_s
Surfr's start is found from the data: the offset that puts the most second-precision Surfr jumps
next to a strong (>= 2.5 g) impulse on the wrist.  Surfr stamps a jump ~2 s after the take-off pop.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kitelog.fitio import read_fit                                   # noqa: E402
from kitelog.detect import build_streams, detect_jumps, compute_features, DetectParams   # noqa: E402
from kitelog.height import estimate_height, HeightParams             # noqa: E402
from kitelog.session import jumps_table                              # noqa: E402

POP_TO_SURFR_S = 2.0
MATCH_TOL_S = 2.5


def load(fit_path, prm: DetectParams | None = None, hprm: HeightParams | None = None):
    rec, _ = read_fit(fit_path)
    s = build_streams(rec)
    cands, F = detect_jumps(s, prm or DetectParams())
    j = jumps_table(s, cands, [estimate_height(c.t_to, c.t_land, s.press_t, s.press, hprm or HeightParams()) for c in cands])
    return rec, s, F, j


def find_offset(s, F, ref: pd.DataFrame) -> tuple[float, int]:
    """UTC of Surfr's start: the offset at which the most second-precision Surfr jumps show the full
    jump pattern on the wrist - a pop (>= 2.0 g) 1-3 s before Surfr's stamp AND an impact (>= 2.3 g)
    0.3-4 s after it.  Matching single impulses is ambiguous (pops and impacts look alike), which put
    session 8 2.5 s off.  The result is then refined to the median pop time."""
    pk, _ = find_peaks(F.m4, height=2.0, distance=6)
    pts = F.t[pk] * s.ts_slope + s.ts_offset
    big = pts[F.m4[pk] >= 2.3]
    ev = ref[ref.time_precision_s == 1].t_since_surfr_start_s.to_numpy(float)
    t_first = F.t[0] * s.ts_slope + s.ts_offset

    def has(arr, lo, hi):              # for each event: is there a peak in [lo, hi] (absolute times)?
        i = np.searchsorted(arr, lo)
        return (i < len(arr)) & (arr[np.minimum(i, len(arr) - 1)] <= hi)

    best = (-1, 0.0)
    for st in np.arange(t_first - 4000, t_first + 1200, 0.5):
        e = st + ev
        sc = int((has(pts, e - 3.0, e - 1.0) & has(big, e + 0.3, e + 4.0)).sum())
        if sc > best[0]:
            best = (sc, st)
    sc, st = best
    e = st + ev
    res = []
    for t in e:                        # refine: median offset of the pop from stamp - POP_TO_SURFR_S
        w = pts[(pts >= t - 3.0) & (pts <= t - 1.0)]
        if len(w):
            res.append(w[np.argmax(F.m4[pk][(pts >= t - 3.0) & (pts <= t - 1.0)])] - (t - POP_TO_SURFR_S))
    return st + (float(np.median(res)) if res else 0.0), sc


def compare(fit_path, ref_path, prm=None, hprm=None, offset=None):
    rec, s, F, j = load(fit_path, prm, hprm)
    ref = pd.read_csv(ref_path, comment="#")
    if offset is None:
        offset, _ = find_offset(s, F, ref)
    acc = j[j.accepted].copy()
    t_lo = F.t[0] * s.ts_slope + s.ts_offset
    t_hi = F.t[-1] * s.ts_slope + s.ts_offset
    ref = ref.assign(ts=offset + ref.t_since_surfr_start_s - POP_TO_SURFR_S)
    ref = ref[(ref.ts > t_lo + 3) & (ref.ts < t_hi - 3)]            # only jumps the watch was recording for
    exact = ref[ref.time_precision_s == 1]
    rows, used = [], set()
    for _, r in exact.iterrows():
        d = acc.ts_to - r.ts
        c = d[(d.abs() <= MATCH_TOL_S) & (~acc.index.isin(used))]
        if len(c):
            i = c.abs().idxmin(); used.add(i); k = acc.loc[i]
            rows.append(dict(n=int(r.n), s_h=r.height, s_air=r.airtime, k_h=k.height_m, k_air=k.airtime_s, found=True))
        else:
            rows.append(dict(n=int(r.n), s_h=r.height, s_air=r.airtime, k_h=np.nan, k_air=np.nan, found=False))
    M = pd.DataFrame(rows)
    span_hi = exact.ts.max() + 30 if len(exact) else t_lo
    extra = acc[(acc.ts_to >= t_lo) & (acc.ts_to <= span_hi) & (~acc.index.isin(used))]
    coarse = ref[ref.time_precision_s != 1]
    late = acc[acc.ts_to > span_hi]
    return dict(offset=offset, matches=M, extra=extra, n_exact=len(exact), n_coarse=len(coarse),
                n_late_kitelog=len(late), n_ref_total=len(ref), n_kitelog_total=len(acc), jumps=acc)


def match_all(acc_ts: np.ndarray, ref: pd.DataFrame, offset: float, lo: float, hi: float):
    """Whole-session matching.  Each Surfr jump gets a window for the KiteLog take-off: exact times
    +-MATCH_TOL_S around stamp - POP_TO_SURFR_S; minute-rounded times anywhere in that minute
    (+-MATCH_TOL_S).  Exact jumps are matched first (nearest), then minute ones in time order.
    Returns (pairs [(ref row index, kitelog index)], n_ref_in_overlap, n_kitelog_in_overlap)."""
    ref = ref.copy()
    ref["c"] = offset + ref.t_since_surfr_start_s - POP_TO_SURFR_S
    ref["w0"] = np.where(ref.time_precision_s == 1, ref.c - MATCH_TOL_S, ref.c - MATCH_TOL_S)
    ref["w1"] = np.where(ref.time_precision_s == 1, ref.c + MATCH_TOL_S, ref.c + 60 + MATCH_TOL_S)
    ref = ref[(ref.c > lo + 3) & (ref.c < hi - 3)]
    k_in = np.flatnonzero((acc_ts >= lo) & (acc_ts <= hi))
    used, pairs = set(), []
    for _, r in ref[ref.time_precision_s == 1].iterrows():
        cand = [k for k in k_in if k not in used and r.w0 <= acc_ts[k] <= r.w1]
        if cand:
            k = min(cand, key=lambda k: abs(acc_ts[k] - r.c)); used.add(k); pairs.append((r.name, k))
    for _, r in ref[ref.time_precision_s != 1].sort_values("c").iterrows():
        cand = [k for k in k_in if k not in used and r.w0 <= acc_ts[k] <= r.w1]
        if cand:
            k = min(cand, key=lambda k: acc_ts[k]); used.add(k); pairs.append((r.name, k))
    return pairs, len(ref), len(k_in)


def whole_session(fit_path, ref_path, prm=None, hprm=None, offset=None):
    """Recall/precision over the whole overlap (exact + minute-rounded Surfr jumps)."""
    rec, s, F, j = load(fit_path, prm, hprm)
    ref = pd.read_csv(ref_path, comment="#")
    if offset is None:
        offset, _ = find_offset(s, F, ref)
    acc = j[j.accepted].reset_index(drop=True)
    lo, hi = F.t[0] * s.ts_slope + s.ts_offset, F.t[-1] * s.ts_slope + s.ts_offset
    pairs, n_ref, n_k = match_all(acc.ts_to.to_numpy(float), ref, offset, lo, hi)
    tp = len(pairs)
    return dict(found=tp, n_ref=n_ref, n_kitelog=n_k, recall=tp / max(1, n_ref), precision=tp / max(1, n_k),
                pairs=[(ref.loc[i], acc.loc[k]) for i, k in pairs])


def summary(r) -> dict:
    M = r["matches"]; m = M[M.found]
    out = dict(exact=r["n_exact"], found=len(m), recall=len(m) / max(1, r["n_exact"]), extra=len(r["extra"]),
               precision=len(m) / max(1, len(m) + len(r["extra"])),
               total_ref=r["n_ref_total"], total_kitelog=r["n_kitelog_total"])
    if len(m):
        e = m.k_h - m.s_h
        out.update(h_mae=float(e.abs().mean()), h_bias=float(e.mean()), h_mape=float((e.abs() / m.s_h).mean() * 100),
                   h_corr=float(np.corrcoef(m.k_h, m.s_h)[0, 1]) if len(m) > 2 else np.nan,
                   air_bias=float((m.k_air - m.s_air).mean()), air_mae=float((m.k_air - m.s_air).abs().mean()))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("fit"); ap.add_argument("surfr")
    a = ap.parse_args()
    r = compare(a.fit, a.surfr)
    sm = summary(r)
    print(f"Surfr start {pd.to_datetime(r['offset'], unit='s'):%H:%M:%S} UTC")
    print(f"exact-time Surfr jumps {sm['exact']}: found {sm['found']} ({sm['recall']*100:.0f} %), KiteLog extras {sm['extra']} "
          f"(precision {sm['precision']*100:.0f} %); whole overlap: Surfr {sm['total_ref']} vs KiteLog {sm['total_kitelog']}")
    w = whole_session(a.fit, a.surfr, offset=r["offset"])
    print(f"whole session (exact + minute-rounded Surfr times): found {w['found']}/{w['n_ref']} ({w['recall']*100:.0f} %), "
          f"precision {w['precision']*100:.0f} % ({w['n_kitelog']} KiteLog jumps)")
    if "h_mae" in sm:
        print(f"height vs Surfr: MAE {sm['h_mae']:.2f} m ({sm['h_mape']:.0f} %), bias {sm['h_bias']:+.2f} m, corr {sm['h_corr']:.2f}; "
              f"airtime KiteLog - Surfr {sm['air_bias']:+.2f} s (MAE {sm['air_mae']:.2f} s)")
