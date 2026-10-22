"""Jump height estimators.

Inputs per jump: take-off/landing times from the accelerometer, the 1 Hz raw
pressure samples around the jump, GPS speed.

Estimators (all returned, `best` picks one):
  h_max_sample : max of baro-derived height samples inside the flight (biased low:
                 1 Hz sampling misses the apex)
  h_quad       : parabola through the three samples around the max
  h_fit        : physics fit of JumpModel(a_up, a_dn, lag) to the samples with the
                 airtime fixed by the accelerometer -> apex of the fitted curve
  h_airtime    : a_eff * T^2 / 8 fallback (no barometer needed)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

from .physics import JumpModel, pressure_to_rel_height, height_from_airtime, ballistic_height, G


@dataclass
class HeightParams:
    pre_win: tuple = (-14.0, -1.5)     # seconds relative to t_to for the baseline window
    post_win: tuple = (3.0, 16.0)      # seconds relative to t_land
    temp_c: float = 15.0
    a_eff: float = 3.2
    lag_range: tuple = (-0.2, 1.2)     # unknown latency of the 1 Hz pressure read (s)
    a_grid: tuple = (1.0, 9.8, 36)
    min_fit_samples: int = 2
    splash_jump_pa: float = 60.0       # baseline window outlier rejection (5 m)
    fit_rmse_abs_m: float = 0.6        # fit accepted when rmse < max(abs, rel * h)
    fit_rmse_rel: float = 0.12
    quad_rmse_max_m: float = 1.5       # beyond this the trace is junk -> airtime model


@dataclass
class HeightResult:
    h_max_sample: float = np.nan
    h_quad: float = np.nan
    h_fit: float = np.nan
    h_airtime: float = np.nan
    h_ballistic: float = np.nan
    best: float = np.nan
    method: str = ""
    n_flight_samples: int = 0
    fit_rmse_m: float = np.nan
    fit_a_up: float = np.nan
    fit_a_dn: float = np.nan
    fit_lag_s: float = np.nan
    baseline_pre_pa: float = np.nan
    baseline_post_pa: float = np.nan
    baseline_ok: bool = False
    baro_ok: bool = False
    flag: str = ""


def _robust_level(p):
    p = p[np.isfinite(p)]
    if len(p) < 2:
        return np.nan
    med = np.median(p)
    mad = np.median(np.abs(p - med)) * 1.4826 + 1e-6
    keep = np.abs(p - med) < max(3 * mad, 8.0)
    return float(np.median(p[keep])) if keep.sum() >= 2 else float(med)


def estimate_height(t_to: float, t_land: float, press_t: np.ndarray, press: np.ndarray,
                    prm: HeightParams | None = None) -> HeightResult:
    prm = prm or HeightParams()
    T = t_land - t_to
    r = HeightResult(h_airtime=height_from_airtime(T, prm.a_eff), h_ballistic=ballistic_height(T))
    pre = press[(press_t >= t_to + prm.pre_win[0]) & (press_t <= t_to + prm.pre_win[1])]
    post = press[(press_t >= t_land + prm.post_win[0]) & (press_t <= t_land + prm.post_win[1])]
    p_pre, p_post = _robust_level(pre), _robust_level(post)
    r.baseline_pre_pa, r.baseline_post_pa = p_pre, p_post
    if np.isnan(p_pre) and np.isnan(p_post):
        r.best, r.method, r.flag = r.h_airtime, "airtime", "no_baseline"
        return r
    if np.isnan(p_pre):
        p_pre = p_post
    if np.isnan(p_post):
        p_post = p_pre
    r.baseline_ok = abs(p_pre - p_post) < prm.splash_jump_pa
    t_c0 = t_to + np.mean(prm.pre_win)
    t_c1 = t_land + np.mean(prm.post_win)
    sel = (press_t >= t_to - 0.6) & (press_t <= t_land + 0.6) & np.isfinite(press)
    tt = press_t[sel]
    pp = press[sel]
    if len(tt) == 0:
        r.best, r.method, r.flag = r.h_airtime, "airtime", "no_flight_samples"
        return r
    p_base = p_pre + (p_post - p_pre) * np.clip((tt - t_c0) / max(t_c1 - t_c0, 1e-6), 0, 1)
    h = pressure_to_rel_height(pp, p_base, prm.temp_c)
    inflight = (tt >= t_to) & (tt <= t_land)
    r.n_flight_samples = int(inflight.sum())
    if r.n_flight_samples == 0:
        r.best, r.method, r.flag = r.h_airtime, "airtime", "no_flight_samples"
        return r
    hf = h[inflight]
    r.h_max_sample = float(np.max(hf))
    # quadratic through the max and its neighbours (on the full window incl. +-0.6 s)
    im = int(np.argmax(np.where(inflight, h, -np.inf)))
    if 0 < im < len(h) - 1:
        y0, y1, y2 = h[im - 1], h[im], h[im + 1]
        denom = (y0 - 2 * y1 + y2)
        if denom < -1e-9:
            xs = 0.5 * (y0 - y2) / denom
            r.h_quad = float(y1 - 0.25 * (y0 - y2) * xs)
        else:
            r.h_quad = float(y1)
    else:
        r.h_quad = r.h_max_sample
    # physics fit (vectorised grid search over a_up, a_dn and the read latency)
    if len(tt) >= prm.min_fit_samples:
        a_lo, a_hi, na = prm.a_grid
        grid = np.linspace(a_lo, a_hi, int(na))
        A_up = grid[:, None, None]
        A_dn = grid[None, :, None]
        v0 = T / (1.0 / A_up + 1.0 / np.sqrt(A_up * A_dn))
        t_up = v0 / A_up
        h_max = v0 ** 2 / (2 * A_up)
        best = (np.inf, None)
        for lag in np.linspace(prm.lag_range[0], prm.lag_range[1], 8):
            tau = (tt - t_to - lag)[None, None, :]
            tau_c = np.clip(tau, 0, T)
            hm = np.where(tau_c <= t_up, v0 * tau_c - 0.5 * A_up * tau_c ** 2, h_max - 0.5 * A_dn * (tau_c - t_up) ** 2)
            hm = np.clip(hm, 0, None)
            hm = np.where((tau < 0) | (tau > T), 0.0, hm)
            sse = np.sum((hm - h[None, None, :]) ** 2, axis=2)
            i, j = np.unravel_index(int(np.argmin(sse)), sse.shape)
            if sse[i, j] < best[0]:
                best = (float(sse[i, j]), (float(grid[i]), float(grid[j]), float(lag), float(h_max[i, j, 0])))
        sse, (a_up, a_dn, lag, hmax) = best
        r.h_fit, r.fit_a_up, r.fit_a_dn, r.fit_lag_s = hmax, a_up, a_dn, lag
        r.fit_rmse_m = float(np.sqrt(sse / len(tt)))
    # choose: the physics fit is trusted only when it explains the samples; a bad
    # fit with a sane baseline means the pressure trace itself is corrupt (splash,
    # port blocked) and the airtime model is the safer fallback.
    fit_ok = np.isfinite(r.h_fit) and r.fit_rmse_m < max(prm.fit_rmse_abs_m, prm.fit_rmse_rel * r.h_fit)
    r.baro_ok = r.baseline_ok and fit_ok and r.h_fit <= 1.3 * r.h_ballistic + 0.5
    if r.baro_ok:
        r.best, r.method = r.h_fit, "baro_fit"
    elif r.baseline_ok and np.isfinite(r.h_quad) and np.isfinite(r.fit_rmse_m) and r.fit_rmse_m < prm.quad_rmse_max_m \
            and r.h_quad <= 1.3 * r.h_ballistic + 0.5:
        r.best, r.method, r.flag = r.h_quad, "baro_quad", "fit_rejected"
    else:
        r.best, r.method, r.flag = r.h_airtime, "airtime", "baro_unreliable"
    return r
