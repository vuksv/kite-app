"""Synthetic kite session + sensor simulator.

Produces exactly the data structure the watch writes into the FIT file
(one row per 1 Hz record, 13 packed accelerometer DOUBLEs, raw pressure,
batch timer, meta word, plus native GPS fields) together with ground truth,
so the offline pipeline can be validated before a single real session.

Physics model (point mass rider, see physics.JumpModel):
  * riding      : specific force |f| = 1 g + chop vibration (2-12 Hz band noise
                  whose RMS grows with speed and the `chop` setting) + wave bumps
  * pre-takeoff : vertical acceleration half-sine over tau_load giving v0
  * flight      : net vertical accel -a_up then -a_dn  (kite carries 60-85 % of
                  body weight) -> |f| ~ 0.6-0.85 g, smooth
  * landing     : half-sine deceleration over tau_imp absorbing v_land
  * barometer   : P = P0 + drift - rho g h + c_dyn * 1/2 rho v_app^2 + noise + splash artefacts
  * GPS         : 1 Hz fixes with correlated position noise, Doppler speed noise, dropouts
  * FIT writer  : 1 Hz ticks with jitter -> occasional duplicated / skipped batches
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd
from scipy import signal

from . import packing as P
from .physics import G, JumpModel, RHO0, PA_PER_M

FS_PHYS = 200
FS_ACC = P.SAMPLE_RATE
DECIM = FS_PHYS // FS_ACC

PH_IDLE, PH_WALK, PH_WATERSTART, PH_RIDE, PH_LOAD, PH_FLIGHT, PH_IMPACT, PH_TRANSITION, PH_CRASH = range(9)
PHASE_NAMES = ["idle", "walk", "waterstart", "ride", "load", "flight", "impact", "transition", "crash"]


@dataclass
class SimConfig:
    duration_s: float = 1500.0
    seed: int = 0
    wind_mps: float = 10.0
    wind_from_deg: float = 0.0
    chop: float = 1.0                 # 0.3 glassy ... 2.0 rough
    jump_rate_per_min: float = 0.8    # average jumps per minute of riding
    jump_h_min: float = 0.5
    jump_h_max: float = 18.0
    crash_prob_per_tack: float = 0.06
    # barometer
    p0_pa: float = 101325.0
    baro_noise_pa: float = 3.0        # white noise per 1 Hz sample (~0.25 m)
    baro_drift_pa_per_h: float = 25.0
    dyn_coupling: float = 0.15        # fraction of 1/2 rho v_app^2 the port sees
    splash_rate_per_h: float = 6.0
    wave_amp_m: float = 0.2
    # gps
    gps_pos_noise_m: float = 1.5
    gps_speed_noise_mps: float = 0.25
    gps_dropout_prob: float = 0.02
    # accel / recording
    accel_noise_g: float = 0.015
    record_drop_prob: float = 0.003
    writer_jitter_s: float = 0.06
    tmr_offset_s: float = 12345.678
    baro_lag_s: float = -1.0           # <0 -> random U(0, 0.9) per session
    epoch0: float = 1_750_000_000.0   # unix seconds of first record


@dataclass
class JumpTruth:
    idx: int
    t_to: float          # sim time of board leaving water
    t_land: float        # sim time of first water contact
    airtime: float
    h_max: float
    v0: float
    a_up: float
    a_dn: float
    v_to: float          # horizontal speed at takeoff
    distance: float      # horizontal displacement takeoff->landing (m)
    tau_load: float
    tau_imp: float


def _half_sine(n: int) -> np.ndarray:
    return np.sin(np.linspace(0, math.pi, n, endpoint=False))


def _band_noise(rng, n, fs, lo=2.0, hi=12.0):
    w = rng.standard_normal(n)
    sos = signal.butter(2, [lo, hi], btype="band", fs=fs, output="sos")
    x = signal.sosfilt(sos, w)
    return x / (np.std(x) + 1e-12)


def _rot_from_angles(roll, pitch, yaw):
    cr, sr = np.cos(roll), np.sin(roll)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    # R = Rz(yaw) Ry(pitch) Rx(roll); shapes (n,3,3)
    R = np.empty((len(roll), 3, 3))
    R[:, 0, 0] = cy * cp
    R[:, 0, 1] = cy * sp * sr - sy * cr
    R[:, 0, 2] = cy * sp * cr + sy * sr
    R[:, 1, 0] = sy * cp
    R[:, 1, 1] = sy * sp * sr + cy * cr
    R[:, 1, 2] = sy * sp * cr - cy * sr
    R[:, 2, 0] = -sp
    R[:, 2, 1] = cp * sr
    R[:, 2, 2] = cp * cr
    return R


class SessionSim:
    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg.seed)
        self.n = int(cfg.duration_s * FS_PHYS)
        self.t = np.arange(self.n) / FS_PHYS
        self.phase = np.full(self.n, PH_IDLE, dtype=np.int8)
        self.v_h = np.zeros(self.n)          # horizontal speed over ground (m/s)
        self.psi = np.zeros(self.n)          # heading (rad, 0 = north, cw)
        self.a_z = np.zeros(self.n)          # rider vertical accel (m/s^2, + up)
        self.h = np.zeros(self.n)            # wrist height above takeoff baseline (m)
        self.vib = np.zeros(self.n)          # vibration RMS schedule (g)
        self.jumps: list[JumpTruth] = []
        self._jump_spans: list[tuple[int, int]] = []
        self.tack_count = 0

    # ------------------------------------------------------------------ timeline
    def build_timeline(self):
        rng, cfg = self.rng, self.cfg
        i = 0
        i = self._segment(i, 40.0, PH_IDLE)
        i = self._segment(i, 30.0, PH_WALK)
        i = self._segment(i, 20.0, PH_WATERSTART)
        tack_sign = 1
        down = math.radians(cfg.wind_from_deg + 180.0)
        while i < self.n - 10 * FS_PHYS:
            dur = rng.uniform(25.0, 80.0)
            heading = down + tack_sign * math.radians(rng.uniform(45.0, 70.0))
            i = self._ride_segment(i, dur, heading)
            self.tack_count += 1
            if rng.random() < cfg.crash_prob_per_tack:
                i = self._segment(i, rng.uniform(8.0, 20.0), PH_CRASH)
            else:
                i = self._transition(i, 7.0, heading, down - tack_sign * math.radians(rng.uniform(45.0, 70.0)))
            tack_sign = -tack_sign
        self._segment(i, (self.n - i) / FS_PHYS, PH_IDLE)
        self._finalize()

    def _segment(self, i0, dur, ph):
        i1 = min(self.n, i0 + int(dur * FS_PHYS))
        self.phase[i0:i1] = ph
        sl = slice(i0, i1)
        if ph == PH_IDLE:
            self.v_h[sl] = 0.0
            self.vib[sl] = 0.02
        elif ph == PH_WALK:
            self.v_h[sl] = 1.3
            self.vib[sl] = 0.12
            self.psi[sl] = self.psi[max(i0 - 1, 0)]
        elif ph == PH_WATERSTART:
            self.v_h[sl] = np.linspace(0.3, 2.5, i1 - i0)
            self.vib[sl] = 0.30
        elif ph == PH_CRASH:
            n = i1 - i0
            self.v_h[sl] = np.maximum(0.0, self.v_h[max(i0 - 1, 0)] * np.exp(-np.arange(n) / (1.5 * FS_PHYS)))
            self.vib[sl] = 0.45
            self.psi[sl] = self.psi[max(i0 - 1, 0)]
            # crash impact at start
            k = int(0.25 * FS_PHYS)
            if i0 + 2 * k <= self.n:
                self.a_z[i0:i0 + k] += -self.rng.uniform(20, 45) * _half_sine(k)
                self.a_z[i0 + k:i0 + 2 * k] += self.rng.uniform(10, 25) * _half_sine(k)
        return i1

    def _transition(self, i0, dur, psi_from, psi_to):
        i1 = min(self.n, i0 + int(dur * FS_PHYS))
        n = i1 - i0
        self.phase[i0:i1] = PH_TRANSITION
        v_in = self.v_h[max(i0 - 1, 0)]
        prof = np.concatenate([np.linspace(v_in, 1.5, n // 2), np.linspace(1.5, 7.0, n - n // 2)])
        self.v_h[i0:i1] = prof
        self.psi[i0:i1] = psi_from + (psi_to - psi_from) * np.linspace(0, 1, n)
        self.vib[i0:i1] = 0.22
        self.a_z[i0:i1] += 2.0 * np.sin(np.linspace(0, 2 * math.pi, n))  # body dip/rise
        return i1

    def _ride_segment(self, i0, dur, heading):
        rng, cfg = self.rng, self.cfg
        i1 = min(self.n, i0 + int(dur * FS_PHYS))
        n = i1 - i0
        self.phase[i0:i1] = PH_RIDE
        v_mean = rng.uniform(7.0, 13.0)
        tt = self.t[i0:i1]
        v = v_mean + 0.8 * np.sin(2 * math.pi * tt / rng.uniform(9, 20)) + 0.4 * np.sin(2 * math.pi * tt / rng.uniform(3, 6))
        # ramp from entry speed
        v_in = self.v_h[max(i0 - 1, 0)]
        ramp = np.minimum(1.0, np.arange(n) / (4.0 * FS_PHYS))
        self.v_h[i0:i1] = v_in + (v - v_in) * ramp
        self.psi[i0:i1] = heading + math.radians(4.0) * np.sin(2 * math.pi * tt / 11.0)
        self.vib[i0:i1] = cfg.chop * (0.10 + 0.025 * self.v_h[i0:i1])
        # jumps inside the segment
        n_j = rng.poisson(cfg.jump_rate_per_min * dur / 60.0)
        t_cursor = 6.0
        for _ in range(n_j):
            if t_cursor > dur - 14.0:
                break
            t_j = t_cursor + rng.uniform(0.0, min(10.0, dur - 14.0 - t_cursor))
            end = self._insert_jump(i0 + int(t_j * FS_PHYS))
            if end is None:
                break
            t_cursor = (end - i0) / FS_PHYS + 4.0
        return i1

    def _insert_jump(self, i_load):
        rng, cfg = self.rng, self.cfg
        h_max = math.exp(rng.uniform(math.log(cfg.jump_h_min), math.log(cfg.jump_h_max)))
        a_up = rng.uniform(2.0, 5.0)
        a_dn = rng.uniform(1.2, min(3.5, a_up))
        jm = JumpModel.from_height(h_max, a_up, a_dn)
        tau_load = rng.uniform(0.25, 0.5)
        tau_imp = rng.uniform(0.12, 0.35)
        n_load, n_fl, n_imp = int(tau_load * FS_PHYS), int(jm.airtime * FS_PHYS), int(tau_imp * FS_PHYS)
        # The board leaves the water at the PEAK of the load pulse; the kite keeps
        # yanking for the second half of the pulse while the rider swings under it.
        i_to = i_load + n_load // 2
        i_ramp_end = i_load + n_load
        i_land = i_ramp_end + n_fl
        i_end = i_land + n_imp
        if i_end + int(3 * FS_PHYS) >= self.n:
            return None
        # --- load pulse: half-sine vertical accel integrating to v0 (over the full pulse)
        A = jm.v0 * math.pi / (2 * tau_load)
        self.a_z[i_load:i_ramp_end] = A * _half_sine(n_load)
        self.phase[i_load:i_to] = PH_LOAD
        self.phase[i_to:i_ramp_end] = PH_FLIGHT
        self.vib[i_load:i_to] *= 0.7
        self.vib[i_to:i_ramp_end] = 0.012
        # --- flight (after the pulse): piecewise constant net acceleration
        tau = (np.arange(n_fl)) / FS_PHYS
        blend = 1.0 / (1.0 + np.exp(-(tau - jm.t_up) / 0.12))      # symmetric sigmoid, +-0.3 s
        self.a_z[i_ramp_end:i_land] = -a_up * (1 - blend) - a_dn * blend
        self.phase[i_ramp_end:i_land] = PH_FLIGHT
        self.vib[i_ramp_end:i_land] = 0.012
        v_to = float(self.v_h[i_to])
        loss = rng.uniform(0.15, 0.40)
        n_air = i_land - i_to
        self.v_h[i_to:i_land] = v_to * (1.0 - loss * np.arange(n_air) / n_air)
        # --- impact: absorb v_land over tau_imp
        A_imp = jm.v_land * math.pi / (2 * tau_imp)
        self.a_z[i_land:i_end] = A_imp * _half_sine(n_imp)
        self.phase[i_land:i_end] = PH_IMPACT
        self.vib[i_land:i_end] = 0.35
        # speed recovery after landing (2.5 s)
        n_rec = int(2.5 * FS_PHYS)
        i_rec = min(self.n, i_end + n_rec)
        v_land_h = self.v_h[i_land - 1]
        target = self.v_h[i_rec - 1]
        self.v_h[i_end:i_rec] = v_land_h + (target - v_land_h) * np.linspace(0, 1, i_rec - i_end)
        self.vib[i_end:i_rec] *= 1.3
        # horizontal distance
        dist = float(np.sum(self.v_h[i_to:i_land]) / FS_PHYS)
        # h_max is filled in by _finalize() from the integrated trajectory
        self.jumps.append(JumpTruth(len(self.jumps), self.t[i_to], self.t[i_land], (i_land - i_to) / FS_PHYS,
                                    float("nan"), jm.v0, a_up, a_dn, v_to, dist, tau_load, tau_imp))
        self._jump_spans.append((i_load, i_end))
        return i_rec

    def _finalize(self):
        """Integrate a_z -> h for the jump segments, add wave motion while riding."""
        cfg, rng = self.cfg, self.rng
        # vertical kinematics: integrate only over load+flight+impact blocks
        dt = 1.0 / FS_PHYS
        for jt, (s, e) in zip(self.jumps, self._jump_spans):
            vz = np.cumsum(self.a_z[s:e]) * dt
            hz = np.cumsum(vz) * dt
            self.h[s:e] = np.clip(hz, 0, None)
            jt.h_max = float(np.max(hz))
        # slow wave height on water (affects the barometer only - rider rides over swell)
        on_water = np.isin(self.phase, [PH_RIDE, PH_LOAD, PH_FLIGHT, PH_IMPACT, PH_TRANSITION, PH_WATERSTART, PH_CRASH])
        wave = cfg.wave_amp_m * (np.sin(2 * math.pi * self.t / 7.0 + rng.uniform(0, 6)) + 0.5 * np.sin(2 * math.pi * self.t / 2.9 + rng.uniform(0, 6)))
        self.h_wave = np.where(on_water, wave, 0.0)

    # ------------------------------------------------------------------ sensors
    def specific_force_watch(self):
        """(n,3) specific force in the watch frame, in g, 200 Hz, including vibration."""
        rng, cfg = self.rng, self.cfg
        n = self.n
        dt = 1.0 / FS_PHYS
        # horizontal acceleration in world frame from speed/heading changes
        vx = self.v_h * np.sin(self.psi)
        vy = self.v_h * np.cos(self.psi)
        ax = np.gradient(vx, dt)
        ay = np.gradient(vy, dt)
        # specific force = a - g_vec, with g_vec = (0,0,-g): f_z = a_z + g
        f_world = np.stack([ax, ay, self.a_z + G], axis=1) / G
        # watch orientation: slow wobble of the arm, larger during transitions
        tt = self.t
        base_roll = math.radians(rng.uniform(-40, 40))
        base_pitch = math.radians(rng.uniform(-30, 30))
        roll = base_roll + math.radians(18) * np.sin(2 * math.pi * tt / rng.uniform(5, 12)) + math.radians(8) * np.sin(2 * math.pi * tt / rng.uniform(1.5, 3.5))
        pitch = base_pitch + math.radians(14) * np.sin(2 * math.pi * tt / rng.uniform(6, 15) + 1.0)
        yaw = self.psi + math.radians(rng.uniform(-60, 60))
        R = _rot_from_angles(roll, pitch, yaw)
        f_watch = np.einsum("nji,nj->ni", R, f_world)  # R^T f
        # vibration (band-limited) + wave bumps, scaled by schedule
        for ax_i in range(3):
            f_watch[:, ax_i] += self.vib * _band_noise(rng, n, FS_PHYS) * rng.uniform(0.8, 1.2)
        # random wave bumps while riding
        riding = np.flatnonzero(self.phase == PH_RIDE)
        n_bumps = int(len(riding) / FS_PHYS * 0.35 * cfg.chop)
        for idx in rng.choice(riding, size=min(n_bumps, len(riding)), replace=False):
            k = int(rng.uniform(0.08, 0.2) * FS_PHYS)
            amp = rng.uniform(0.4, 1.6) * cfg.chop
            if idx + k < n:
                f_watch[idx:idx + k, 2] += amp * _half_sine(k) * rng.choice([-1, 1])
        # walking: 1.8 Hz steps
        walk = self.phase == PH_WALK
        f_watch[walk, 2] += 0.35 * np.sin(2 * math.pi * 1.8 * tt[walk])
        return f_watch

    def barometer(self, t_samples, v_app_fn):
        """Raw ambient pressure (Pa) at the given times."""
        rng, cfg = self.rng, self.cfg
        t_samples = np.asarray(t_samples)
        h_wrist = np.interp(t_samples, self.t, self.h + self.h_wave)
        drift = cfg.baro_drift_pa_per_h * t_samples / 3600.0 + np.cumsum(rng.standard_normal(len(t_samples))) * 0.15
        v_app = v_app_fn(t_samples)
        dyn = cfg.dyn_coupling * 0.5 * RHO0 * v_app ** 2
        p = cfg.p0_pa + drift - PA_PER_M * h_wrist - dyn + rng.standard_normal(len(t_samples)) * cfg.baro_noise_pa
        # splash artefacts: port blocked by water -> big transient excursion
        n_spl = rng.poisson(cfg.splash_rate_per_h * cfg.duration_s / 3600.0)
        for _ in range(n_spl):
            t0 = rng.uniform(0, cfg.duration_s)
            amp = rng.uniform(-250, 150)
            tau = rng.uniform(3, 25)
            p += amp * np.exp(-np.clip(t_samples - t0, 0, None) / tau) * (t_samples >= t0)
        return p

    # ------------------------------------------------------------------ records
    def run(self):
        cfg, rng = self.cfg, self.rng
        self.build_timeline()
        f = self.specific_force_watch()
        # 25 Hz anti-aliased samples
        n25 = self.n // DECIM
        f25 = f[: n25 * DECIM].reshape(n25, DECIM, 3).mean(axis=1)
        f25 += rng.standard_normal(f25.shape) * cfg.accel_noise_g
        t25 = (np.arange(n25) + DECIM / 2) / FS_ACC / 1.0 - (DECIM / 2) / FS_PHYS  # sample time centre
        t25 = np.arange(n25) / FS_ACC + (DECIM - 1) / (2 * FS_PHYS)
        codes = P.encode_g_array(f25.ravel()).reshape(n25, 3)
        # accelerometer clock phase: batches end at integer seconds of (t + phi_a)
        phi_a = rng.uniform(0, 1)
        batch_id = np.floor(t25 + phi_a).astype(int)
        batches = {}
        for b in np.unique(batch_id):
            sel = np.flatnonzero(batch_id == b)
            t_end = b + 1 - phi_a
            if t_end > cfg.duration_s:
                continue
            batches[b] = dict(t_end=t_end, codes=codes[sel], f=f25[sel], t=t25[sel])
        b_ids = np.array(sorted(batches))
        b_tend = np.array([batches[b]["t_end"] for b in b_ids])
        # barometer sampled at each batch callback
        wind = cfg.wind_mps
        wind_dir = math.radians(cfg.wind_from_deg)
        def v_app_fn(ts):
            v = np.interp(ts, self.t, self.v_h)
            psi = np.interp(ts, self.t, self.psi)
            # rider velocity minus wind velocity (wind blows towards wind_from+180)
            vx = v * np.sin(psi) - wind * np.sin(wind_dir + math.pi)
            vy = v * np.cos(psi) - wind * np.cos(wind_dir + math.pi)
            return np.hypot(vx, vy)
        baro_lag = cfg.baro_lag_s if cfg.baro_lag_s >= 0 else rng.uniform(0.0, 0.9)
        self.baro_lag = baro_lag
        p_batch = self.barometer(b_tend - baro_lag, v_app_fn)
        # GPS path (true) integrated at 200 Hz, in local metres
        dt = 1.0 / FS_PHYS
        x = np.cumsum(self.v_h * np.sin(self.psi)) * dt
        y = np.cumsum(self.v_h * np.cos(self.psi)) * dt
        self.x, self.y = x, y
        lat0, lon0 = 52.37, 4.55
        m_per_deg_lat = 111_132.0
        m_per_deg_lon = 111_132.0 * math.cos(math.radians(lat0))
        # FIT writer ticks
        phi_w = rng.uniform(0, 1)
        n_rec = int(cfg.duration_s) - 1
        rows = []
        seq_of_batch = {b: k for k, b in enumerate(b_ids)}
        pos_err = np.zeros(2)
        for j in range(n_rec):
            t_w = j + phi_w + rng.uniform(-cfg.writer_jitter_s, cfg.writer_jitter_s)
            if t_w <= 0 or t_w > cfg.duration_s:
                continue
            if rng.random() < cfg.record_drop_prob:
                continue
            k = np.searchsorted(b_tend, t_w, side="right") - 1
            if k < 0:
                continue
            b = b_ids[k]
            bt = batches[b]
            tmr = int(round((bt["t_end"] + cfg.tmr_offset_s) * 1000.0))
            acc_fields = P.pack_batch(bt["codes"])
            meta = P.pack_meta(seq_of_batch[b], len(bt["codes"]), 1, 0b11)
            # gps fix some 0.1-0.9 s before the tick
            t_fix = t_w - rng.uniform(0.1, 0.9)
            pos_err = 0.85 * pos_err + rng.standard_normal(2) * cfg.gps_pos_noise_m * 0.55
            if rng.random() < cfg.gps_dropout_prob:
                lat = lon = spd = np.nan
            else:
                xi = np.interp(t_fix, self.t, x) + pos_err[0] + rng.standard_normal() * 0.6
                yi = np.interp(t_fix, self.t, y) + pos_err[1] + rng.standard_normal() * 0.6
                lat = lat0 + yi / m_per_deg_lat
                lon = lon0 + xi / m_per_deg_lon
                spd = max(0.0, np.interp(t_fix, self.t, self.v_h) + rng.standard_normal() * cfg.gps_speed_noise_mps)
            rows.append(dict(ts=cfg.epoch0 + j, lat=lat, lon=lon, speed=spd, alt=np.nan,
                             **{name: val for name, val in zip(P.ACC_FIELD_NAMES, acc_fields)},
                             press=float(p_batch[k]), tmr=tmr, meta=meta))
        records = pd.DataFrame(rows)
        truth = dict(
            tmr_offset_s=cfg.tmr_offset_s,
            epoch0=cfg.epoch0,
            baro_lag_s=float(baro_lag),
            tack_count=self.tack_count,
            total_distance_m=float(np.sum(self.v_h) * dt),
            jumps=[asdict(j) for j in self.jumps],
            config=asdict(cfg),
        )
        ideal = pd.DataFrame(dict(t=t25, fx=f25[:, 0], fy=f25[:, 1], fz=f25[:, 2],
                                  phase=self.phase[: n25 * DECIM: DECIM], h=self.h[: n25 * DECIM: DECIM],
                                  v=self.v_h[: n25 * DECIM: DECIM]))
        return records, truth, ideal


def simulate(cfg: SimConfig | None = None, **kw):
    cfg = cfg or SimConfig(**kw)
    return SessionSim(cfg).run()
