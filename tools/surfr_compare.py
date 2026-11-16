#!/usr/bin/env python3
"""Compare KiteLog jumps with a Surfr phone-app jump list recorded in parallel.

usage: surfr_compare.py <session.fit> <surfr.csv> [--big 3.0]
       surfr_compare.py --all [--big 3.0]          every sessions/*_surfr.csv, plus the pooled result

surfr.csv (as in sessions/*_surfr.csv): n,height,airtime,distance,t_since_surfr_start_s,time_precision_s
  time_precision_s = 1  : Surfr's time to the second (first hour of a Surfr session)
  time_precision_s = 60 : whole minutes only - t_since_surfr_start_s is the START of that minute

How the comparison works
  1. Surfr's start time is found from the data (find_offset): the offset at which the most
     second-precision Surfr jumps sit between a pop and an impact on the wrist.  Surfr stamps a
     jump ~2 s after the take-off pop.
  2. The two jump lists are aligned IN TIME ORDER (match): a Surfr jump with an exact time may pair
     with a KiteLog take-off within +-2.5 s, one with a minute-only time with any take-off in that
     minute; the alignment that pairs the most jumps wins.
  3. Scores, for all jumps and for "big" jumps (>= 3 m unless --big says otherwise):
       found      = share of Surfr's jumps that KiteLog also has           (recall)
       confirmed  = share of KiteLog's jumps that Surfr also has           (precision)
       height     = KiteLog minus Surfr on the pairs with an exact time
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kitelog.fitio import read_fit                                   # noqa: E402
from kitelog.detect import build_streams, detect_jumps, DetectParams   # noqa: E402
from kitelog.height import estimate_height, HeightParams             # noqa: E402
from kitelog.session import jumps_table                              # noqa: E402

POP_TO_SURFR_S = 2.0
MATCH_TOL_S = 2.5
BIG_M = 3.0


def load(fit_path, prm: DetectParams | None = None, hprm: HeightParams | None = None):
    rec, _ = read_fit(fit_path)
    s = build_streams(rec)
    cands, F = detect_jumps(s, prm or DetectParams())
    j = jumps_table(s, cands, [estimate_height(c.t_to, c.t_land, s.press_t, s.press, hprm or HeightParams()) for c in cands])
    return rec, s, F, j


def find_offset(s, F, ref: pd.DataFrame) -> tuple[float, int]:
    """UTC of Surfr's start: the offset at which the most second-precision Surfr jumps show the
    jump pattern on the wrist - a pop (>= 2.0 g) 1-3 s before Surfr's stamp AND an impact (>= 2.3 g)
    0.3-4 s after it.  (Matching single impulses is ambiguous: pops and impacts look alike.)
    The result is then refined to the median pop time."""
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
    res = []
    for t in st + ev:                  # refine: median offset of the pop from stamp - POP_TO_SURFR_S
        w = (pts >= t - 3.0) & (pts <= t - 1.0)
        if w.any():
            res.append(pts[w][np.argmax(F.m4[pk][w])] - (t - POP_TO_SURFR_S))
    return st + (float(np.median(res)) if res else 0.0), sc


def match(ref: pd.DataFrame, k_ts: np.ndarray) -> list[tuple[int, int]]:
    """Order-preserving alignment of Surfr jumps (rows of `ref` with columns c = expected take-off
    time, exact = bool) with KiteLog take-off times `k_ts` (sorted, unix s).
    Returns [(row position in ref, index into k_ts)]: the largest set of pairs, ties broken by
    the time error of the exact ones."""
    n, m = len(ref), len(k_ts)
    c, exact = ref.c.to_numpy(float), ref.exact.to_numpy(bool)
    w0, w1 = c - MATCH_TOL_S, np.where(exact, c + MATCH_TOL_S, c + 60 + MATCH_TOL_S)
    ok = (k_ts[None, :] >= w0[:, None]) & (k_ts[None, :] <= w1[:, None])
    gain = 1.0 - np.where(exact[:, None], np.abs(k_ts[None, :] - c[:, None]) / 100.0, 0.0)
    S = np.zeros((n + 1, m + 1)); B = np.zeros((n + 1, m + 1), int)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, b = S[i - 1, j], 1
            if S[i, j - 1] > best:
                best, b = S[i, j - 1], 2
            if ok[i - 1, j - 1] and S[i - 1, j - 1] + gain[i - 1, j - 1] > best:
                best, b = S[i - 1, j - 1] + gain[i - 1, j - 1], 3
            S[i, j], B[i, j] = best, b
    i, j, pairs = n, m, []
    while i > 0 and j > 0:
        if B[i, j] == 3:
            pairs.append((i - 1, j - 1)); i -= 1; j -= 1
        elif B[i, j] == 1:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def compare(fit_path, ref_path, prm=None, hprm=None, offset=None, big=BIG_M) -> dict:
    """Counts for one session.  Keys: n_surfr, n_kitelog, n_pairs (all jumps); big_surfr,
    big_surfr_found (Surfr jumps >= big, and how many KiteLog has); big_kitelog,
    big_kitelog_confirmed (KiteLog jumps >= big, and how many Surfr has); h_err (KiteLog - Surfr
    height on exact-time pairs with Surfr >= big); pairs = DataFrame of the matched jumps."""
    rec, s, F, j = load(fit_path, prm, hprm)
    ref = pd.read_csv(ref_path, comment="#")
    if offset is None:
        offset, _ = find_offset(s, F, ref)
    lo, hi = F.t[0] * s.ts_slope + s.ts_offset, F.t[-1] * s.ts_slope + s.ts_offset
    ref = ref.assign(c=offset + ref.t_since_surfr_start_s - POP_TO_SURFR_S, exact=ref.time_precision_s == 1)
    ref = ref[(ref.c > lo + 3) & (ref.c < hi - 3)].reset_index(drop=True)      # only jumps the watch was recording for
    acc = j[j.accepted].sort_values("ts_to").reset_index(drop=True)
    pr = match(ref, acc.ts_to.to_numpy(float))
    r2k = dict(pr); k2r = {k: i for i, k in pr}
    rb, kb = ref.height >= big, acc.height_m >= big
    P = pd.DataFrame([dict(n=int(ref.n[i]), exact=bool(ref.exact[i]), s_h=ref.height[i], s_air=ref.airtime[i],
                           k_h=acc.height_m[k], k_air=acc.airtime_s[k], ts=acc.ts_to[k]) for i, k in pr])
    ex = P[P.exact & (P.s_h >= big)] if len(P) else P
    return dict(offset=offset, n_surfr=len(ref), n_kitelog=len(acc), n_pairs=len(pr),
                big_surfr=int(rb.sum()), big_surfr_found=sum(1 for i in ref.index[rb] if i in r2k),
                big_kitelog=int(kb.sum()), big_kitelog_confirmed=sum(1 for k in acc.index[kb] if k in k2r),
                h_err=(ex.k_h - ex.s_h).tolist() if len(ex) else [], h_ref=ex.s_h.tolist() if len(ex) else [],
                pairs=P, jumps=acc, ref=ref,
                missed=ref[[i not in r2k for i in ref.index]], extra=acc[[k not in k2r for k in acc.index]])


COUNT_KEYS = ("n_surfr", "n_kitelog", "n_pairs", "big_surfr", "big_surfr_found", "big_kitelog", "big_kitelog_confirmed")


def pool(results: list[dict]) -> dict:
    out = {k: sum(r[k] for r in results) for k in COUNT_KEYS}
    out["h_err"] = sum((r["h_err"] for r in results), []); out["h_ref"] = sum((r["h_ref"] for r in results), [])
    return out


def scores(r: dict) -> dict:
    """Percentages from the counts of compare() or pool()."""
    e, h = np.array(r["h_err"], float), np.array(r["h_ref"], float)
    return dict(found=r["n_pairs"] / max(1, r["n_surfr"]), confirmed=r["n_pairs"] / max(1, r["n_kitelog"]),
                big_found=r["big_surfr_found"] / max(1, r["big_surfr"]),
                big_confirmed=r["big_kitelog_confirmed"] / max(1, r["big_kitelog"]),
                h_mae=float(np.abs(e).mean()) if len(e) else np.nan, h_bias=float(e.mean()) if len(e) else np.nan,
                h_mape=float((np.abs(e) / h).mean() * 100) if len(e) else np.nan,
                h_within_1m=float((np.abs(e) <= 1.0).mean()) if len(e) else np.nan)


def describe(name: str, r: dict, big: float) -> str:
    sc = scores(r)
    return (f"{name:<22} jumps >= {big:g} m: found {r['big_surfr_found']}/{r['big_surfr']} ({sc['big_found']:.0%}), "
            f"confirmed {r['big_kitelog_confirmed']}/{r['big_kitelog']} ({sc['big_confirmed']:.0%}) | "
            f"all jumps: found {r['n_pairs']}/{r['n_surfr']} ({sc['found']:.0%}), confirmed {r['n_pairs']}/{r['n_kitelog']} ({sc['confirmed']:.0%}) | "
            f"height {sc['h_mae']:.2f} m off ({sc['h_mape']:.0f} %), bias {sc['h_bias']:+.2f} m, {sc['h_within_1m']:.0%} within 1 m")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("fit", nargs="?"); ap.add_argument("surfr", nargs="?")
    ap.add_argument("--all", action="store_true"); ap.add_argument("--big", type=float, default=BIG_M)
    a = ap.parse_args()
    if a.all:
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sessions")
        res = []
        for ref in sorted(glob.glob(os.path.join(root, "*_surfr.csv"))):
            if not (pd.read_csv(ref, comment="#").time_precision_s == 1).any():
                continue                                   # no exact times -> Surfr's start cannot be found
            r = compare(ref.replace("_surfr.csv", ".fit"), ref, big=a.big)
            if r["n_surfr"] < 12:
                continue                                   # too little overlap to align (session 6: Surfr stopped early)
            res.append(r); print(describe(os.path.basename(ref)[:-10], r, a.big))
        print(describe("ALL", pool(res), a.big))
    else:
        r = compare(a.fit, a.surfr, big=a.big)
        print(f"Surfr start {pd.to_datetime(r['offset'], unit='s'):%H:%M:%S} UTC")
        print(describe(os.path.basename(a.fit), r, a.big))
        if len(r["missed"]):
            print("Surfr jumps KiteLog does not have:", ", ".join(f"#{int(n)} {h:.1f} m" for n, h in zip(r["missed"].n, r["missed"].height)))
        if len(r["extra"]):
            print("KiteLog jumps Surfr does not have:", ", ".join(f"{pd.to_datetime(t, unit='s'):%H:%M:%S} {h:.1f} m" for t, h in zip(r["extra"].ts_to, r["extra"].height_m)))
