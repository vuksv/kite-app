"""Jump detection from the recorded streams.

Signal chain (all on the accelerometer *magnitude*, so the unknown wrist
orientation does not matter - we have no gyroscope on the Instinct 2X):

  |f|  --LP 4 Hz-->  m4   : transients (take-off load peak, landing impact)
  |f|  --LP 1 Hz-->  m1   : flight level (~0.5-0.9 g when the kite carries you)
  |f| - LP3Hz(|f|)  -> hp : chop vibration residual
  rough = RMS(hp, 0.32 s window)
  rough_ref = rolling 60 s median of rough           (adapts to water state / speed)

A jump candidate is a stretch where rough < max(k * rough_ref, floor) for >= 0.5 s,
with m1 inside the kite-supported band, bracketed by a load peak before and an
impact peak after, while the GPS speed says we were planing.
Take-off time  = where m4 falls through the midpoint after the load peak.
Landing time   = onset of the impact rise after the smooth stretch.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd
from scipy import signal

from . import packing as P

FS = P.SAMPLE_RATE


@dataclass
class DetectParams:
    lp_transient_hz: float = 4.0
    lp_level_hz: float = 1.0
    hp_split_hz: float = 3.0
    rough_win_s: float = 0.32
    rough_ref_win_s: float = 60.0
    rough_ratio: float = 0.30
    rough_floor_g: float = 0.035
    level_min_g: float = 0.25
    level_max_g: float = 1.45
    min_smooth_s: float = 0.32
    merge_gap_s: float = 0.6         # blips inside the flight (kite re-trim at the apex) are bridged if no impact in between
    takeoff_search_s: float = 1.2
    takeoff_min_peak_g: float = 1.35
    landing_search_s: float = 0.9
    landing_min_peak_g: float = 1.6
    landing_rise_g: float = 0.5
    min_speed_mps: float = 3.0
    min_flight_speed_mps: float = 2.5
    min_valid_frac: float = 0.6
    max_data_gap_s: float = 1.5
    min_flight_speed_ratio: float = 0.4
    max_airtime_s: float = 14.0
    takeoff_point: str = "peak"      # "peak" = load-pulse maximum, "mid" = half-way down the pulse


@dataclass
class Streams:
    """Uniform 25 Hz accelerometer grid (NaN where data is missing) + 1 Hz streams."""
    t: np.ndarray            # seconds, tmr clock
    f: np.ndarray            # (n,3) g
    valid: np.ndarray        # bool
    press_t: np.ndarray
    press: np.ndarray
    gps_t: np.ndarray        # tmr clock
    lat: np.ndarray
    lon: np.ndarray
    speed: np.ndarray
    ts_offset: float         # ts (unix s) ~= t + ts_offset
    n_dup: int = 0
    n_gap: int = 0
    n_batches: int = 0


def build_streams(records: pd.DataFrame) -> Streams:
    # records written before the first accelerometer batch carry no samples and a
    # placeholder tmr (0 or invalid) that would sort them out of time order
    rec = records[records["acc_0"].notna()].copy()
    meta = rec["meta"].astype(np.int64).map(P.unpack_meta)
    rec["seq"] = [m["seq"] for m in meta]
    rec["n_valid"] = [m["n_valid"] for m in meta]
    rec = rec.sort_values("tmr").reset_index(drop=True)
    # duplicates = same batch written twice (writer tick faster than batch arrival)
    dup = rec.duplicated(subset=["tmr"], keep="first")
    n_dup = int(dup.sum())
    rec_u = rec[~dup].reset_index(drop=True)
    t_batch = rec_u["tmr"].to_numpy(dtype=float) / 1000.0
    ts = rec_u["ts"].to_numpy(dtype=float)
    ts_offset = float(np.median(ts - t_batch))
    # assemble samples
    sample_t, sample_f = [], []
    n_gap = 0
    for i, row in rec_u.iterrows():
        codes = P.unpack_batch([row[n] for n in P.ACC_FIELD_NAMES])
        n = len(codes)
        if n == 0:
            continue
        g = P.decode_code_array(codes)
        tt = t_batch[i] - (n - 1 - np.arange(n)) / FS
        sample_t.append(tt)
        sample_f.append(g)
        if i > 0 and t_batch[i] - t_batch[i - 1] > 1.5:
            n_gap += 1
    st = np.concatenate(sample_t)
    sf = np.concatenate(sample_f)
    # uniform grid
    t0, t1 = st[0], st[-1]
    grid = np.arange(0, int(round((t1 - t0) * FS)) + 1) / FS + t0
    idx = np.rint((st - t0) * FS).astype(int)
    f = np.full((len(grid), 3), np.nan)
    ok = (idx >= 0) & (idx < len(grid))
    f[idx[ok]] = sf[ok]
    valid = ~np.isnan(f[:, 0])
    press = rec_u["press"].to_numpy(dtype=float)
    gps_t = ts - ts_offset - 0.5     # fix typically precedes the record write
    return Streams(t=grid, f=f, valid=valid, press_t=t_batch, press=press, gps_t=gps_t,
                   lat=rec_u["lat"].to_numpy(dtype=float), lon=rec_u["lon"].to_numpy(dtype=float),
                   speed=rec_u["speed"].to_numpy(dtype=float), ts_offset=ts_offset,
                   n_dup=n_dup, n_gap=n_gap, n_batches=len(rec_u))


def _lp(x, fc, order=2):
    sos = signal.butter(order, fc, btype="low", fs=FS, output="sos")
    return signal.sosfiltfilt(sos, x)


def _block_rolling_median(x, block, win_blocks):
    """Median per 1 s block, then a centred rolling median over `win_blocks` blocks
    (NaN blocks ignored), repeated back to sample rate.  Cheap enough for a phone
    browser; the JavaScript port in web/kitelog.js does exactly the same."""
    n = len(x)
    nb = (n + block - 1) // block
    bm = np.full(nb, np.nan)
    for b in range(nb):
        seg = x[b * block:(b + 1) * block]
        if np.isfinite(seg).any():
            bm[b] = np.nanmedian(seg)
    half = win_blocks // 2
    ref = np.full(nb, np.nan)
    for b in range(nb):
        seg = bm[max(0, b - half):b + half + 1]
        seg = seg[np.isfinite(seg)]
        if len(seg) >= 5:
            ref[b] = np.median(seg)
    ok = np.isfinite(ref)
    if ok.any():
        ref = np.interp(np.arange(nb), np.flatnonzero(ok), ref[ok])
    else:
        ref[:] = 0.2
    return np.repeat(ref, block)[:n]


def _moving_rms(x, n):
    k = np.ones(n) / n
    return np.sqrt(np.convolve(x * x, k, mode="same"))


@dataclass
class Features:
    t: np.ndarray
    mag: np.ndarray
    mag_f: np.ndarray     # magnitude with gaps bridged
    m4: np.ndarray
    m1: np.ndarray
    rough: np.ndarray
    rough_ref: np.ndarray
    speed: np.ndarray     # GPS speed interpolated onto the 25 Hz grid (NaN-free, ffilled)


def compute_features(s: Streams, p: DetectParams) -> Features:
    f = s.f.copy()
    mag = np.linalg.norm(f, axis=1)
    valid = ~np.isnan(mag)
    mag_filled = mag.copy()
    if valid.sum() >= 2:
        mag_filled[~valid] = np.interp(np.flatnonzero(~valid), np.flatnonzero(valid), mag[valid])   # bridge gaps
    else:
        mag_filled = np.where(valid, mag, 1.0)
    m4 = _lp(mag_filled, p.lp_transient_hz)
    m1 = _lp(mag_filled, p.lp_level_hz)
    hp = mag_filled - _lp(mag_filled, p.hp_split_hz)
    rough = _moving_rms(hp, max(3, int(p.rough_win_s * FS)))
    rough[~s.valid] = np.nan
    rough_ref = _block_rolling_median(rough, FS, int(p.rough_ref_win_s))
    rough = np.where(np.isnan(rough), np.inf, rough)
    gps_ok = ~np.isnan(s.speed)
    if gps_ok.sum() >= 2:
        speed = np.interp(s.t, s.gps_t[gps_ok], s.speed[gps_ok])
    else:
        speed = np.full_like(s.t, np.nan)
    return Features(s.t, mag, mag_filled, m4, m1, rough, rough_ref, speed)


@dataclass
class JumpCandidate:
    t_smooth_start: float
    t_smooth_end: float
    t_to: float
    t_land: float
    airtime: float
    takeoff_peak_g: float
    landing_peak_g: float
    flight_level_g: float
    rough_in: float
    rough_ref: float
    speed_to: float
    accepted: bool = True
    reject_reason: str = ""
    flags: str = ""


def _last_peak(x, a0, a1, thr):
    """Index of the last local maximum of x[a0:a1] that exceeds thr, else None."""
    seg = x[a0:a1]
    if len(seg) < 3:
        return None
    pk, _ = signal.find_peaks(seg, height=thr, distance=3)
    if len(pk) == 0:
        return None
    return a0 + int(pk[-1])


def _segments(mask: np.ndarray):
    d = np.diff(np.r_[0, mask.astype(int), 0])
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts, ends))


def detect_jumps(s: Streams, p: DetectParams | None = None, feats: Features | None = None):
    p = p or DetectParams()
    F = feats or compute_features(s, p)
    n = len(F.t)
    thr = np.maximum(p.rough_ratio * F.rough_ref, p.rough_floor_g)
    smooth = (F.rough < thr) & (F.m1 > p.level_min_g) & (F.m1 < p.level_max_g) & s.valid
    segs = _segments(smooth)
    # merge close segments
    merged = []
    for a, b in segs:
        if merged:
            pb = merged[-1][1]
            gap = a - pb
            quiet_blip = gap <= p.merge_gap_s * FS and F.m4[pb:a].max() < p.landing_min_peak_g
            data_gap = gap <= p.max_data_gap_s * FS and (~s.valid[pb:a]).mean() > 0.8      # dropped record inside the flight
            if quiet_blip or data_gap:
                merged[-1] = (merged[-1][0], b)
                continue
        merged.append((a, b))
    cands: list[JumpCandidate] = []
    for a, b in merged:
        if (b - a) < p.min_smooth_s * FS:
            continue
        if a < int(2 * FS) or b > n - int(2 * FS):
            continue                                   # touches the stream edge: cannot see both transients
        # take-off: load peak before the smooth stretch
        a0 = max(0, a - int(p.takeoff_search_s * FS))
        a1 = min(n, a + int(0.15 * FS))
        flight_level = float(np.nanmedian(F.m1[a:b]))
        i_pk = _last_peak(F.m4, a0, a1, p.takeoff_min_peak_g)
        if i_pk is None:
            i_pk = a0 + int(np.argmax(F.m4[a0:a1]))
        pk_to = float(F.m4[i_pk])
        takeoff_in_gap = False
        if pk_to < p.takeoff_min_peak_g and (~s.valid[a0:a1]).any():
            takeoff_in_gap = True
            i_pk = a0 + int(np.flatnonzero(~s.valid[a0:a1])[-1])       # last missing sample before the flight
            pk_to = np.nan
        if p.takeoff_point == "mid" and not takeoff_in_gap:
            mid = 0.5 * (pk_to + flight_level)
            j = i_pk
            while j < b and F.m4[j] > mid:
                j += 1
            t_to = F.t[j]
        else:
            t_to = F.t[i_pk]
        # landing: impact onset after the smooth stretch
        b1 = min(n, b + int(p.landing_search_s * FS))
        i_lp = b + int(np.argmax(F.m4[b:b1])) if b1 > b else b
        pk_land = float(F.m4[i_lp])
        landing_in_gap = False
        if pk_land < p.landing_min_peak_g and (~s.valid[b:b1]).any():
            landing_in_gap = True
            k = b + int(np.flatnonzero(~s.valid[b:b1])[0])              # first missing sample after the flight
            pk_land = np.nan
        else:
            i_raw = b + int(np.nanargmax(F.mag_f[b:b1])) if b1 > b else b
            k = i_raw
            while k > a and F.mag_f[k] > flight_level + p.landing_rise_g:
                k -= 1
            k = min(k + 1, i_raw)
        t_land = F.t[k]
        airtime = t_land - t_to
        spd = float(F.speed[a]) if not np.isnan(F.speed[a]) else np.nan
        fl = F.speed[a:b]
        spd_min = float(np.nanmin(fl)) if np.isfinite(fl).any() else np.nan
        c = JumpCandidate(F.t[a], F.t[b - 1], t_to, t_land, airtime, pk_to, pk_land, flight_level,
                          float(np.nanmean(np.where(np.isinf(F.rough[a:b]), np.nan, F.rough[a:b]))),
                          float(np.nanmedian(F.rough_ref[a:b])), spd)
        reasons = []
        flags = []
        if takeoff_in_gap:
            flags.append("takeoff_in_gap")
        elif pk_to < p.takeoff_min_peak_g:
            reasons.append("no_takeoff_peak")
        if landing_in_gap:
            flags.append("landing_in_gap")
        elif pk_land < p.landing_min_peak_g:
            reasons.append("no_landing_peak")
        if takeoff_in_gap and landing_in_gap:
            reasons.append("both_transients_in_gap")
        vf = s.valid[a:b].mean()
        if vf < p.min_valid_frac:
            reasons.append("gap_inside")           # a bridged gap looks perfectly smooth - not evidence of flight
        elif vf < 1.0:
            flags.append("gap_in_flight")
        c.flags = ",".join(flags)
        if not np.isnan(spd) and spd < p.min_speed_mps:
            reasons.append("slow")
        if not np.isnan(spd_min) and spd_min < max(p.min_flight_speed_mps, p.min_flight_speed_ratio * spd):
            reasons.append("speed_collapse")      # transitions / crashes: speed dies while a jump keeps most of it
        if airtime <= 0.3 or airtime > p.max_airtime_s:
            reasons.append("airtime_range")
        c.accepted = not reasons
        c.reject_reason = ",".join(reasons)
        cands.append(c)
    return cands, F
