"""Session-level analysis: jump distances, speeds, tacks, track export."""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .detect import Streams, JumpCandidate
from .height import HeightResult


def local_xy(lat, lon, lat0=None, lon0=None):
    lat = np.asarray(lat, float)
    lon = np.asarray(lon, float)
    if lat0 is None:
        lat0 = np.nanmean(lat)
        lon0 = np.nanmean(lon)
    kx = 111_132.0 * math.cos(math.radians(lat0))
    ky = 111_132.0
    return (lon - lon0) * kx, (lat - lat0) * ky


def jump_distance(s: Streams, t_to: float, t_land: float) -> dict:
    """Two estimators: integral of GPS speed over the airtime (robust to position
    noise) and displacement between interpolated positions (robust to speed bias)."""
    ok = np.isfinite(s.speed)
    out = dict(dist_speed=np.nan, dist_pos=np.nan, v_to=np.nan, v_land=np.nan, v_max_air=np.nan)
    if ok.sum() < 2:
        return out
    tt = s.gps_t[ok]
    grid = np.arange(t_to, t_land, 0.05)
    v = np.interp(grid, tt, s.speed[ok])
    out["dist_speed"] = float(np.sum(v) * 0.05)
    out["v_to"] = float(np.interp(t_to, tt, s.speed[ok]))
    out["v_land"] = float(np.interp(t_land, tt, s.speed[ok]))
    out["v_max_air"] = float(np.max(v)) if len(v) else np.nan
    okp = ok & np.isfinite(s.lat) & np.isfinite(s.lon)
    if okp.sum() >= 2:
        x, y = local_xy(s.lat[okp], s.lon[okp])
        tp = s.gps_t[okp]
        x0, y0 = np.interp(t_to, tp, x), np.interp(t_to, tp, y)
        x1, y1 = np.interp(t_land, tp, x), np.interp(t_land, tp, y)
        out["dist_pos"] = float(math.hypot(x1 - x0, y1 - y0))
    return out


def baro_consistent(c: JumpCandidate, h: HeightResult, a_floor: float = 1.0) -> bool:
    """A real jump of airtime T rises at least ~a_floor*T^2/8 (a_floor = 1 m/s^2 is an
    extremely floaty kite).  With a trustworthy baro baseline, a long 'flight' that
    shows no height is a transition or a quiet glide, not a jump."""
    if not h.baseline_ok or not np.isfinite(h.h_fit) or c.airtime < 2.0:
        return True
    return h.h_fit >= 0.5 * a_floor * c.airtime ** 2 / 8.0


def jumps_table(s: Streams, cands: list[JumpCandidate], heights: list[HeightResult]) -> pd.DataFrame:
    rows = []
    for k, (c, h) in enumerate(zip(cands, heights)):
        d = jump_distance(s, c.t_to, c.t_land)
        accepted, reason = c.accepted, c.reject_reason
        if accepted and not baro_consistent(c, h):
            accepted, reason = False, "baro_inconsistent"
        rows.append(dict(
            jump=k + 1,
            accepted=accepted,
            reject_reason=reason,
            flags=c.flags,
            t_to=c.t_to, t_land=c.t_land,
            ts_to=c.t_to + s.ts_offset,
            airtime_s=c.airtime,
            height_m=h.best, height_method=h.method, height_flag=h.flag,
            h_fit=h.h_fit, h_quad=h.h_quad, h_max_sample=h.h_max_sample, h_airtime=h.h_airtime,
            fit_rmse_m=h.fit_rmse_m, fit_a_up=h.fit_a_up, fit_a_dn=h.fit_a_dn, fit_lag_s=h.fit_lag_s,
            n_baro_samples=h.n_flight_samples,
            distance_m=d["dist_speed"] if np.isfinite(d["dist_speed"]) else d["dist_pos"],
            dist_speed_m=d["dist_speed"], dist_pos_m=d["dist_pos"],
            takeoff_speed_mps=d["v_to"], landing_speed_mps=d["v_land"],
            takeoff_peak_g=c.takeoff_peak_g, landing_peak_g=c.landing_peak_g, flight_level_g=c.flight_level_g,
            rough_in_g=c.rough_in, rough_ref_g=c.rough_ref,
        ))
    return pd.DataFrame(rows)


@dataclass
class SessionStats:
    duration_s: float
    riding_time_s: float
    distance_m: float
    max_speed_mps: float
    max_speed_10s_mps: float
    avg_riding_speed_mps: float
    tacks: int
    jumps: int
    max_height_m: float
    max_airtime_s: float
    total_airtime_s: float
    longest_jump_m: float
    n_batches: int
    n_dup_batches: int
    n_gaps: int


def heading_from_track(x, y, t):
    dx, dy = np.gradient(x, t), np.gradient(y, t)
    return np.degrees(np.arctan2(dx, dy)) % 360.0


def count_tacks(s: Streams, min_speed=3.0, turn_deg=100.0, window_s=15.0) -> int:
    ok = np.isfinite(s.lat) & np.isfinite(s.lon) & np.isfinite(s.speed)
    if ok.sum() < 10:
        return 0
    x, y = local_xy(s.lat[ok], s.lon[ok])
    t = s.gps_t[ok]
    # smooth 5 s
    k = np.ones(5) / 5
    xs, ys = np.convolve(x, k, mode="same"), np.convolve(y, k, mode="same")
    hdg = heading_from_track(xs, ys, t)
    spd = s.speed[ok]
    tacks = 0
    last_turn_t = -1e9
    i = 0
    n = len(t)
    for i in range(n):
        if spd[i] < min_speed or t[i] - last_turn_t < window_s:
            continue
        j = np.searchsorted(t, t[i] + window_s)
        if j >= n:
            break
        d = abs((hdg[j] - hdg[i] + 180.0) % 360.0 - 180.0)
        if d > turn_deg and spd[j] >= min_speed:
            tacks += 1
            last_turn_t = t[j]
    return tacks


def session_stats(s: Streams, jumps: pd.DataFrame) -> SessionStats:
    ok = np.isfinite(s.speed)
    spd = s.speed[ok]
    t = s.gps_t[ok]
    dt = np.diff(t, prepend=t[0] - 1.0) if len(t) else np.array([])
    dt = np.clip(dt, 0, 5)
    riding = spd >= 3.0
    k = 10
    spd10 = np.convolve(spd, np.ones(k) / k, mode="valid") if len(spd) >= k else spd
    acc = jumps[jumps["accepted"]] if len(jumps) else jumps
    return SessionStats(
        duration_s=float(s.t[-1] - s.t[0]),
        riding_time_s=float(np.sum(dt[riding])),
        distance_m=float(np.sum(spd * dt)),
        max_speed_mps=float(np.max(spd)) if len(spd) else np.nan,
        max_speed_10s_mps=float(np.max(spd10)) if len(spd10) else np.nan,
        avg_riding_speed_mps=float(np.mean(spd[riding])) if riding.any() else np.nan,
        tacks=count_tacks(s),
        jumps=int(len(acc)),
        max_height_m=float(acc["height_m"].max()) if len(acc) else 0.0,
        max_airtime_s=float(acc["airtime_s"].max()) if len(acc) else 0.0,
        total_airtime_s=float(acc["airtime_s"].sum()) if len(acc) else 0.0,
        longest_jump_m=float(acc["distance_m"].max()) if len(acc) else 0.0,
        n_batches=s.n_batches, n_dup_batches=s.n_dup, n_gaps=s.n_gap,
    )
