"""Jump detection from the recorded streams.

A kite jump leaves four marks on the wrist, and the detector asks for all of them:

  1. a take-off POP    - a peak of the accelerometer magnitude |f| (low-passed at 4 Hz) >= 2.0 g
  2. a landing IMPACT  - the FIRST peak >= 2.4 g that follows the pop by 1.5 .. 7.5 s
  3. arms overhead     - between riding (3 .. 0.5 s before the pop) and the air the average
                         direction of the acceleration in the watch frame turns by >= 40 deg:
                         riding, the arms reach forward to the bar; in the air the rider hangs under it
  4. a float           - somewhere in the air the 1 Hz level of |f| drops to <= 0.9 g

plus three GPS checks: the rider was moving at the pop (>= 15 km/h), did not come out of the
"jump" faster than they went in, and a long flight (> 4 s) started at >= 25 km/h.  Of two candidates that overlap, the one with the harder
pop + impact wins (this picks the real pop over a bump just before it).

Take-off = the pop peak, landing = the impact peak, airtime = the time between them.

Everything works on magnitudes and on an angle *change*, so how the watch sits on the wrist
does not matter.  Chosen on 4 days recorded in parallel with the Surfr phone app (154 jumps):
see docs/ANALYSIS.md.  The earlier detector ("vibration stops" + a pop/impact pair with seven
roughness / level / speed gates, ~45 thresholds) scored lower and is gone.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import signal

from . import packing as P

FS = P.SAMPLE_RATE


@dataclass
class DetectParams:
    lp_transient_hz: float = 4.0     # |f| low-pass that carries the pop and the impact
    lp_level_hz: float = 1.0         # |f| low-pass for the level in the air
    pop_g: float = 2.0               # take-off peak
    impact_g: float = 2.4            # landing peak: the first one this hard after min_air_s
    min_air_s: float = 1.5           # pop -> impact; 1.5 s is a ~1.4 m hop, 3 s a ~3 m jump
    max_air_s: float = 7.5
    min_tilt_deg: float = 40.0       # wrist turn between riding and the air (Surfr-confirmed jumps: 5 % below 48 deg, chop: median 17 deg)
    max_dip_g: float = 0.90          # lowest 1 Hz level in the air (confirmed jumps: 90 % below 0.85 g, chop pairs: 90 % above 0.80 g)
    min_speed_mps: float = 4.2       # 15 km/h at the pop: standing, walking and water starts are not jumps
    max_speed_gain_mps: float = 0.42 # speed in the 1.5 s after landing may not all be > take-off speed + 1.5 km/h
                                     # (confirmed jumps: never above +1.2 km/h; riding off after a turn: +2 .. +6 km/h)
    long_air_s: float = 4.0          # a flight longer than this (a ~4.6 m jump) ...
    long_air_min_speed_mps: float = 6.9   # ... needs 25 km/h at the pop: all 45 confirmed long jumps took off at >= 25.9 km/h,
                                     # the four unconfirmed 5-8 m "jumps" of the calm day (session 10) at 19-24 km/h
    min_valid_frac: float = 0.6      # accelerometer samples present between pop and impact


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
    ts_offset: float         # ts (unix s) ~= ts_slope * t + ts_offset
    ts_slope: float = 1.0
    sample_dt: float = 1.0 / FS
    n_dup: int = 0
    n_gap: int = 0
    n_batches: int = 0


def build_streams(records: pd.DataFrame) -> Streams:
    # records written before the first accelerometer batch carry no samples and a
    # placeholder tmr (0 or invalid) that would sort them out of time order
    rec = records[records["acc_0"].notna()].copy()
    # FIT "invalid" placeholders (UINT32 0xFFFFFFFF) appear on a record written before the first
    # batch arrived (seen on the 2026-11-05 afternoon session): they would stretch the timeline
    # to 46 days.  Drop them, and any record whose timer disagrees with its timestamp by > 60 s.
    rec = rec[(rec["tmr"].astype("int64") != 0xFFFFFFFF) & (rec["meta"].astype("int64") != 0xFFFFFFFF) & (rec["tmr"].astype("int64") > 0)]
    if len(rec) >= 10:
        lag = rec["ts"].to_numpy(dtype=float) - rec["tmr"].to_numpy(dtype=float) / 1000.0
        rec = rec[np.abs(lag - np.median(lag)) < 60.0]
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
    # Batches arrive every ~0.99 s on the Instinct 2X (25 samples at ~25.3 Hz), and the
    # record clock (RTC) and System.getTimer() drift apart by ~0.2 %: use the measured
    # batch spacing for the sample grid and a linear fit for the ts <-> tmr mapping.
    db = np.diff(t_batch)
    db = db[(db > 0.5) & (db < 1.5)]
    dt = (float(np.median(db)) if len(db) >= 5 else 1.0) / FS
    if len(t_batch) >= 20:
        slope, icpt = np.polyfit(t_batch, ts, 1)
    else:
        slope, icpt = 1.0, float(np.median(ts - t_batch))
    ts_offset = float(icpt)
    # assemble samples
    sample_t, sample_f = [], []
    n_gap = 0
    for i, row in rec_u.iterrows():
        codes = P.unpack_batch([row[n] for n in P.ACC_FIELD_NAMES])
        n = len(codes)
        if n == 0:
            continue
        g = P.decode_code_array(codes)
        tt = t_batch[i] - (n - 1 - np.arange(n)) * dt
        sample_t.append(tt)
        sample_f.append(g)
        if i > 0 and t_batch[i] - t_batch[i - 1] > 1.5:
            n_gap += 1
    st = np.concatenate(sample_t)
    sf = np.concatenate(sample_f)
    # uniform grid
    t0, t1 = st[0], st[-1]
    grid = np.arange(0, int(round((t1 - t0) / dt)) + 1) * dt + t0
    idx = np.rint((st - t0) / dt).astype(int)
    f = np.full((len(grid), 3), np.nan)
    ok = (idx >= 0) & (idx < len(grid))
    f[idx[ok]] = sf[ok]
    valid = ~np.isnan(f[:, 0])
    press = rec_u["press"].to_numpy(dtype=float)
    gps_t = (ts - icpt) / slope - 0.5     # fix typically precedes the record write
    return Streams(t=grid, f=f, valid=valid, press_t=t_batch, press=press, gps_t=gps_t,
                   lat=rec_u["lat"].to_numpy(dtype=float), lon=rec_u["lon"].to_numpy(dtype=float),
                   speed=rec_u["speed"].to_numpy(dtype=float), ts_offset=ts_offset, ts_slope=float(slope), sample_dt=dt,
                   n_dup=n_dup, n_gap=n_gap, n_batches=len(rec_u))


def _lp(x, fc, order=2):
    sos = signal.butter(order, fc, btype="low", fs=FS, output="sos")
    return signal.sosfiltfilt(sos, x)


@dataclass
class Features:
    t: np.ndarray
    mag: np.ndarray
    mag_f: np.ndarray     # magnitude with gaps bridged
    m4: np.ndarray
    m1: np.ndarray
    speed: np.ndarray     # GPS speed interpolated onto the 25 Hz grid
    csum: np.ndarray      # (n+1,3) running sum of the acceleration vector (missing samples = 0)
    ccnt: np.ndarray      # (n+1,) running count of valid samples


def compute_features(s: Streams, p: DetectParams | None = None) -> Features:
    p = p or DetectParams()
    mag = np.linalg.norm(s.f, axis=1)
    valid = ~np.isnan(mag)
    mag_filled = mag.copy()
    if valid.sum() >= 2:
        mag_filled[~valid] = np.interp(np.flatnonzero(~valid), np.flatnonzero(valid), mag[valid])   # bridge gaps
    else:
        mag_filled = np.where(valid, mag, 1.0)
    m4 = _lp(mag_filled, p.lp_transient_hz)
    m1 = _lp(mag_filled, p.lp_level_hz)
    gps_ok = ~np.isnan(s.speed)
    if gps_ok.sum() >= 2:
        speed = np.interp(s.t, s.gps_t[gps_ok], s.speed[gps_ok])
    else:
        speed = np.full_like(s.t, np.nan)
    csum = np.vstack([np.zeros(3), np.cumsum(np.where(valid[:, None], s.f, 0.0), axis=0)])
    ccnt = np.r_[0, np.cumsum(valid)]
    return Features(s.t, mag, mag_filled, m4, m1, speed, csum, ccnt)


def mean_vector(F: Features, i0: int, i1: int):
    """Average acceleration vector over samples [i0, i1) (valid samples only), or None."""
    i0, i1 = max(0, i0), min(len(F.t), i1)
    if i1 <= i0 or F.ccnt[i1] - F.ccnt[i0] < 3:
        return None
    return (F.csum[i1] - F.csum[i0]) / (F.ccnt[i1] - F.ccnt[i0])


def tilt_deg(u, v) -> float:
    """Angle between two average acceleration vectors (deg); nan if either is missing."""
    if u is None or v is None:
        return float("nan")
    d = float(np.linalg.norm(u) * np.linalg.norm(v))
    if d < 1e-9:
        return float("nan")
    return float(np.degrees(np.arccos(np.clip(float(u @ v) / d, -1.0, 1.0))))


@dataclass
class JumpCandidate:
    t_to: float               # take-off = pop peak (tmr clock, s)
    t_land: float             # landing = impact peak
    airtime: float
    takeoff_peak_g: float
    landing_peak_g: float
    flight_level_g: float     # median 1 Hz level in the air
    dip_g: float              # lowest 1 Hz level in the air
    tilt_deg: float           # wrist turn, riding -> air
    speed_to: float           # GPS speed at the pop (m/s)
    speed_drop: float         # speed at the pop minus the lowest speed in the 1.5 s after landing (m/s)
    accepted: bool = True
    reject_reason: str = ""
    flags: str = ""


PRE_WIN_S = (3.0, 0.5)        # "riding" reference window: 3.0 .. 0.5 s before the pop
AIR_MARGIN_S = (0.3, 0.25)    # the air = pop + 0.3 s .. impact - 0.25 s (keeps the two transients out)
POST_WIN_S = 1.5              # speed after landing is looked at for this long
OVERLAP_GUARD_S = 0.5


def detect_jumps(s: Streams, p: DetectParams | None = None, feats: Features | None = None):
    """-> (candidates sorted by take-off time, features).  Every pop that has an impact after it
    becomes a candidate; `accepted` / `reject_reason` say which ones are jumps."""
    p = p or DetectParams()
    F = feats or compute_features(s, p)
    n = len(F.t)
    pk, _ = signal.find_peaks(F.m4, height=min(p.pop_g, p.impact_g), distance=6)
    impacts = pk[F.m4[pk] >= p.impact_g]
    have_gps = bool(np.isfinite(F.speed).any())
    n_pre0, n_pre1 = int(PRE_WIN_S[0] * FS), int(PRE_WIN_S[1] * FS)
    m0, m1_ = int(AIR_MARGIN_S[0] * FS), int(AIR_MARGIN_S[1] * FS)
    n_post = int(POST_WIN_S * FS)
    cands: list[JumpCandidate] = []
    for a in pk[F.m4[pk] >= p.pop_g]:
        a = int(a)
        k = int(np.searchsorted(impacts, a + int(np.ceil(p.min_air_s * FS - 1e-9))))
        if k >= len(impacts):
            continue
        b = int(impacts[k])                                   # the first hard impact after the pop
        if b - a > p.max_air_s * FS or a < n_pre0 or b + n_post > n:
            continue
        i0, i1 = a + m0, b - m1_
        tilt = tilt_deg(mean_vector(F, a - n_pre0, a - n_pre1), mean_vector(F, i0, i1))
        dip = float(F.m1[i0:i1].min())
        v_to = float(F.speed[a]) if have_gps else float("nan")
        v_drop = float(v_to - np.nanmin(F.speed[b:b + n_post])) if have_gps else float("nan")
        reasons = []
        if s.valid[a:b].mean() < p.min_valid_frac or np.isnan(tilt):
            reasons.append("data_gap")
        if have_gps and v_to < p.min_speed_mps:
            reasons.append("slow")
        if not np.isnan(tilt) and tilt < p.min_tilt_deg:
            reasons.append("no_tilt")
        if dip > p.max_dip_g:
            reasons.append("no_float")
        if have_gps and v_drop < -p.max_speed_gain_mps:
            reasons.append("speed_gain")
        if have_gps and (b - a) / FS > p.long_air_s and v_to < p.long_air_min_speed_mps:
            reasons.append("slow_for_airtime")
        cands.append(JumpCandidate(t_to=float(F.t[a]), t_land=float(F.t[b]), airtime=float(F.t[b] - F.t[a]),
                                   takeoff_peak_g=float(F.m4[a]), landing_peak_g=float(F.m4[b]),
                                   flight_level_g=float(np.median(F.m1[i0:i1])), dip_g=dip, tilt_deg=tilt,
                                   speed_to=v_to, speed_drop=v_drop, accepted=not reasons, reject_reason=",".join(reasons)))
    # overlapping jumps: the harder pop + impact wins
    kept: list[JumpCandidate] = []
    for c in sorted((c for c in cands if c.accepted), key=lambda c: -(c.takeoff_peak_g + c.landing_peak_g)):
        if all(c.t_land + OVERLAP_GUARD_S < q.t_to or c.t_to > q.t_land + OVERLAP_GUARD_S for q in kept):
            kept.append(c)
        else:
            c.accepted, c.reject_reason = False, "overlap"
    cands.sort(key=lambda c: c.t_to)
    return cands, F
