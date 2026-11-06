// goproalign.js - line a GoPro clip up with a KiteLog session (pure functions, browser + Node).
import { lowpass, interp } from './kitelog.js';

export const FS = 25;
const G = 9.80665;

/** KiteLog session -> low-passed wrist |f| on a uniform 25 Hz UTC grid + accepted jumps. */
export function sessionSignal(res) {
  const F = res.features, s = res.streams;
  const u0 = F.t[0] * s.tsSlope + s.tsOffset, u1 = F.t[F.t.length - 1] * s.tsSlope + s.tsOffset;
  const n = Math.floor((u1 - u0) * FS), m4 = new Float64Array(n), tu = Array.from(F.t, t => t * s.tsSlope + s.tsOffset);
  for (let i = 0; i < n; i++) m4[i] = interp(u0 + i / FS, tu, F.m4);
  return { u0, m4, jumps: res.jumps.filter(j => j.accepted) };
}

/** GoPro ACCL (m/s^2, ~200 Hz) -> |a| in g, 25 Hz, same 4 Hz low-pass as KiteLog's transient channel. */
export function goproMagnitude(gp) {
  const a = gp.streams.ACCL; if (!a || a.v.length < 50) return null;
  const n = Math.floor(gp.durationS * FS), out = new Float64Array(n), cnt = new Float64Array(n), sum = new Float64Array(n);
  for (let i = 0; i < a.t.length; i++) { const k = Math.floor(a.t[i] * FS); if (k >= 0 && k < n) { sum[k] += Math.hypot(a.v[i][0], a.v[i][1], a.v[i][2]) / G; cnt[k]++; } }
  let last = 1; for (let k = 0; k < n; k++) { if (cnt[k]) last = sum[k] / cnt[k]; out[k] = last; }
  return lowpass(out, 4, FS);
}

/** Hits and slams only: both devices feel them, whatever they are strapped to. */
export const impulses = (x) => Float64Array.from(x, v => Math.max(0, Math.min(v, 6) - 1.4));

/** Normalised cross-correlation of x against y for lags lo..hi (y index = lag + k). */
export function ncc(x, y, lo, hi, step = 1) {
  const n = x.length; let sx = 0, sxx = 0; for (const v of x) { sx += v; sxx += v * v; }
  const vx = sxx - sx * sx / n; let best = { r: -1, lag: 0 }; const all = [];
  for (let lag = lo; lag <= hi; lag += step) {
    let sy = 0, syy = 0, sxy = 0, m = 0;
    for (let k = 0; k < n; k++) { const j = lag + k; if (j < 0 || j >= y.length) continue; const yv = y[j]; sy += yv; syy += yv * yv; sxy += x[k] * yv; m++; }
    if (m < n * 0.8) continue;
    const vy = syy - sy * sy / m, r = (sxy - sx * sy / m) / Math.sqrt(Math.max(vx * vy, 1e-12));
    all.push([lag, r]); if (r > best.r) best = { r, lag };
  }
  return { ...best, all };
}
const decimate = (arr, f) => Float64Array.from({ length: Math.floor(arr.length / f) }, (_, i) => { let s = 0; for (let k = 0; k < f; k++) s += arr[i * f + k]; return s / f; });

/** Refine a GPS-clock start (UTC of video 0) by +-range s against one session. */
export function refineGps(mag, sig, startUtc, range = 10) {
  const x = impulses(mag), y = impulses(sig.m4), base = Math.round((startUtc - sig.u0) * FS);
  const c = ncc(x, y, base - range * FS, base + range * FS);
  return { r: c.r, shift: (c.lag - base) / FS };
}

/** Search a whole session by motion (no GPS on the camera). Returns UTC of video 0, r, ratio to next-best peak. */
export function searchByMotion(mag, sig) {
  const x = impulses(mag), y = impulses(sig.m4);
  const xc = decimate(x, 5), yc = decimate(y, 5);
  const c = ncc(xc, yc, -Math.floor(xc.length * 0.2), yc.length - Math.floor(xc.length * 0.8));
  const fine = ncc(x, y, c.lag * 5 - 2 * FS, c.lag * 5 + 2 * FS);
  const second = Math.max(-1, ...c.all.filter(([l]) => Math.abs(l - c.lag) > 25).map(([, r]) => r));
  return { offset: sig.u0 + fine.lag / FS, r: fine.r, ratio: c.r / Math.max(second, 1e-3) };
}
