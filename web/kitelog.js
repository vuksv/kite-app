// kitelog.js - jump analysis pipeline (JavaScript port of tools/kitelog/{packing,detect,height,session}.py).
// Pure functions, no dependencies; runs in the browser and in Node.  Keep the two
// implementations in sync - tools/web/test/compare.mjs checks them against each other.

export const FS = 25;
const G = 9.80665, R_AIR = 287.05, CODE_INVALID = -128, N_ACC = 13;
export const ACC_NAMES = Array.from({ length: N_ACC }, (_, i) => 'acc_' + i);

// ------------------------------------------------------------------ packing
export function decodeCode(c) {
  if (c === CODE_INVALID) return NaN;
  const s = c < 0 ? -1 : 1, m = Math.abs(c);
  return s * (m <= 96 ? m / 32 : 3 + (m - 96) / 8);
}
export function unpackPair(d) {
  let v = Math.round(d);
  const out = new Array(6);
  for (let i = 5; i >= 0; i--) { out[i] = (v % 256) - 128; v = Math.floor(v / 256); }
  return out;
}
export function unpackBatch(doubles) {            // -> array of [x,y,z] codes (invalid slots removed)
  const rows = [];
  for (const d of doubles) {
    if (d == null || !Number.isFinite(d)) continue;
    const c = unpackPair(d);
    if (!(c[0] === CODE_INVALID && c[1] === CODE_INVALID && c[2] === CODE_INVALID)) rows.push([c[0], c[1], c[2]]);
    if (!(c[3] === CODE_INVALID && c[4] === CODE_INVALID && c[5] === CODE_INVALID)) rows.push([c[3], c[4], c[5]]);
  }
  return rows;
}
export function unpackMeta(m) {
  m = m >>> 0;
  return { seq: m & 0xFFFF, nValid: (m >>> 16) & 0x1F, state: (m >>> 21) & 0x7, flags: (m >>> 24) & 0x7F };
}

// ------------------------------------------------------------------ numerics
export function interp(x, xp, fp) {               // np.interp (xp ascending), x scalar
  const n = xp.length;
  if (n === 0) return NaN;
  if (x <= xp[0]) return fp[0];
  if (x >= xp[n - 1]) return fp[n - 1];
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xp[mid] <= x) lo = mid; else hi = mid; }
  const w = (x - xp[lo]) / (xp[hi] - xp[lo]);
  return fp[lo] + w * (fp[hi] - fp[lo]);
}
function median(a) {
  const b = Array.from(a).filter(Number.isFinite).sort((p, q) => p - q);
  const n = b.length;
  if (!n) return NaN;
  return n % 2 ? b[(n - 1) / 2] : 0.5 * (b[n / 2 - 1] + b[n / 2]);
}

function biquadLowpass(fc, fs) {                   // 2nd-order Butterworth, bilinear transform (== scipy.signal.butter(2, fc, fs=fs))
  const k = Math.tan(Math.PI * fc / fs), k2 = k * k, norm = 1 / (1 + Math.SQRT2 * k + k2);
  return { b0: k2 * norm, b1: 2 * k2 * norm, b2: k2 * norm, a1: 2 * (k2 - 1) * norm, a2: (1 - Math.SQRT2 * k + k2) * norm };
}
function filtOnce(x, c, reverse) {
  const n = x.length, y = new Float64Array(n);
  // steady-state initial conditions for a constant input equal to the first processed sample
  const x0 = reverse ? x[n - 1] : x[0];
  let x1 = x0, x2 = x0, y1 = x0, y2 = x0;
  for (let k = 0; k < n; k++) {
    const i = reverse ? n - 1 - k : k;
    const xi = x[i];
    const yi = c.b0 * xi + c.b1 * x1 + c.b2 * x2 - c.a1 * y1 - c.a2 * y2;
    x2 = x1; x1 = xi; y2 = y1; y1 = yi; y[i] = yi;
  }
  return y;
}
export function lowpass(x, fc, fs = FS) {           // zero-phase (forward + backward) with odd extension padding
  const pad = 9, n = x.length;
  if (n < 2 * pad + 2) return Float64Array.from(x);
  const ext = new Float64Array(n + 2 * pad);
  for (let i = 0; i < pad; i++) ext[i] = 2 * x[0] - x[pad - i];
  for (let i = 0; i < n; i++) ext[pad + i] = x[i];
  for (let i = 0; i < pad; i++) ext[pad + n + i] = 2 * x[n - 1] - x[n - 2 - i];
  const c = biquadLowpass(fc, fs);
  const y = filtOnce(filtOnce(ext, c, false), c, true);
  return y.slice(pad, pad + n);
}
export function buildStreams(records) {
  // records written before the first accelerometer batch carry no samples and a placeholder tmr
  const recs = records.filter(r => r.dev && r.dev.tmr != null && r.dev.meta != null && r.dev.acc_0 != null).map(r => ({ ...r, tmr: r.dev.tmr, meta: unpackMeta(r.dev.meta) }));
  recs.sort((a, b) => a.tmr - b.tmr);
  const uniq = [];
  let nDup = 0;
  for (const r of recs) { if (uniq.length && uniq[uniq.length - 1].tmr === r.tmr) nDup++; else uniq.push(r); }
  if (!uniq.length) throw new Error('No KiteLog developer fields in this file (is it a KiteLog recording?)');
  const tBatch = uniq.map(r => r.tmr / 1000);
  // measured batch spacing (~0.99 s on the Instinct 2X) -> sample spacing; linear ts<->tmr map (clocks drift ~0.2 %)
  const db = tBatch.slice(1).map((t, i) => t - tBatch[i]).filter(d => d > 0.5 && d < 1.5);
  const dt = (db.length >= 5 ? median(db) : 1.0) / FS;
  let slope = 1.0, icpt = median(uniq.map((r, i) => r.ts - tBatch[i]));
  if (uniq.length >= 20) {
    const n = uniq.length; let sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (let i = 0; i < n; i++) { sx += tBatch[i]; sy += uniq[i].ts; sxx += tBatch[i] * tBatch[i]; sxy += tBatch[i] * uniq[i].ts; }
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx); icpt = (sy - slope * sx) / n;
  }
  const tsOffset = icpt;
  const st = [], sf = [];
  let nGap = 0;
  uniq.forEach((r, i) => {
    const codes = unpackBatch(ACC_NAMES.map(k => r.dev[k]));
    const n = codes.length;
    if (!n) return;
    for (let k = 0; k < n; k++) { st.push(tBatch[i] - (n - 1 - k) * dt); sf.push([decodeCode(codes[k][0]), decodeCode(codes[k][1]), decodeCode(codes[k][2])]); }
    if (i > 0 && tBatch[i] - tBatch[i - 1] > 1.5) nGap++;
  });
  const t0 = st[0], t1 = st[st.length - 1];
  const len = Math.round((t1 - t0) / dt) + 1;
  const t = new Float64Array(len);
  for (let i = 0; i < len; i++) t[i] = t0 + i * dt;
  const fx = new Float64Array(len).fill(NaN), fy = new Float64Array(len).fill(NaN), fz = new Float64Array(len).fill(NaN);
  const valid = new Uint8Array(len);
  for (let k = 0; k < st.length; k++) {
    const idx = Math.round((st[k] - t0) / dt);
    if (idx >= 0 && idx < len) { fx[idx] = sf[k][0]; fy[idx] = sf[k][1]; fz[idx] = sf[k][2]; valid[idx] = 1; }
  }
  return {
    t, fx, fy, fz, valid,
    pressT: Float64Array.from(tBatch), press: Float64Array.from(uniq.map(r => r.dev.press != null ? r.dev.press : NaN)),
    gpsT: Float64Array.from(uniq.map(r => (r.ts - icpt) / slope - 0.5)),
    lat: Float64Array.from(uniq.map(r => r.lat != null ? r.lat : NaN)), lon: Float64Array.from(uniq.map(r => r.lon != null ? r.lon : NaN)),
    speed: Float64Array.from(uniq.map(r => r.speed != null ? r.speed : NaN)),
    tsOffset, tsSlope: slope, sampleDt: dt, nDup, nGap, nBatches: uniq.length,
    baroValidFrac: uniq.filter(r => r.dev.press != null).length / uniq.length,
    gpsFixFrac: uniq.filter(r => r.lat != null).length / uniq.length,
  };
}

// ------------------------------------------------------------------ detection
// A jump = a take-off POP, then the first hard IMPACT 1.5-7.5 s later, with the wrist turned
// (arms overhead under the bar) and a float (low g) in between; plus three GPS checks.
// Same algorithm and numbers as tools/kitelog/detect.py - see there and docs/ANALYSIS.md.
export const DEFAULT_DETECT = {
  lpTransientHz: 4.0, lpLevelHz: 1.0,
  popG: 2.0,                 // take-off peak of |f| (4 Hz low-pass)
  impactG: 2.4,              // landing peak: the first one this hard after minAirS
  minAirS: 1.5, maxAirS: 7.5,
  minTiltDeg: 40,            // wrist turn between riding (3..0.5 s before the pop) and the air
  maxDipG: 0.90,             // lowest 1 Hz level in the air
  minSpeedMps: 4.2,          // 15 km/h at the pop
  maxSpeedGainMps: 0.42,     // speed after landing may not all be > take-off speed + 1.5 km/h
  longAirS: 4.0, longAirMinSpeedMps: 6.9,   // a flight > 4 s needs 25 km/h at the pop
  minValidFrac: 0.6,
};
const PRE_WIN_S = [3.0, 0.5], AIR_MARGIN_S = [0.3, 0.25], POST_WIN_S = 1.5, OVERLAP_GUARD_S = 0.5;

export function computeFeatures(s, p = DEFAULT_DETECT) {
  const n = s.t.length, mag = new Float64Array(n);
  for (let i = 0; i < n; i++) mag[i] = s.valid[i] ? Math.hypot(s.fx[i], s.fy[i], s.fz[i]) : NaN;
  const magF = Float64Array.from(mag);
  const vi = [], vv = [];
  for (let i = 0; i < n; i++) if (s.valid[i]) { vi.push(i); vv.push(mag[i]); }
  for (let i = 0; i < n; i++) if (!s.valid[i]) magF[i] = vi.length >= 2 ? interp(i, vi, vv) : 1.0;
  const m4 = lowpass(magF, p.lpTransientHz), m1 = lowpass(magF, p.lpLevelHz);
  const gi = [], gv = [];
  for (let i = 0; i < s.gpsT.length; i++) if (Number.isFinite(s.speed[i])) { gi.push(s.gpsT[i]); gv.push(s.speed[i]); }
  const speed = new Float64Array(n);
  for (let i = 0; i < n; i++) speed[i] = gi.length >= 2 ? interp(s.t[i], gi, gv) : NaN;
  // running sums of the acceleration vector (missing samples = 0) for window averages
  const cx = new Float64Array(n + 1), cy = new Float64Array(n + 1), cz = new Float64Array(n + 1), cn = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) {
    const v = s.valid[i];
    cx[i + 1] = cx[i] + (v ? s.fx[i] : 0); cy[i + 1] = cy[i] + (v ? s.fy[i] : 0); cz[i + 1] = cz[i] + (v ? s.fz[i] : 0); cn[i + 1] = cn[i] + (v ? 1 : 0);
  }
  return { t: s.t, mag, magF, m4, m1, speed, cx, cy, cz, cn };
}

/** Average acceleration vector over samples [i0, i1) (valid samples only), or null. */
export function meanVector(F, i0, i1) {
  i0 = Math.max(0, i0); i1 = Math.min(F.t.length, i1);
  const c = F.cn[i1] - F.cn[i0];
  if (i1 <= i0 || c < 3) return null;
  return [(F.cx[i1] - F.cx[i0]) / c, (F.cy[i1] - F.cy[i0]) / c, (F.cz[i1] - F.cz[i0]) / c];
}
/** Angle between two average acceleration vectors (deg); NaN if either is missing. */
export function tiltDeg(u, v) {
  if (!u || !v) return NaN;
  const d = Math.hypot(u[0], u[1], u[2]) * Math.hypot(v[0], v[1], v[2]);
  if (d < 1e-9) return NaN;
  return Math.acos(Math.max(-1, Math.min(1, (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / d))) * 180 / Math.PI;
}

// local maxima >= h, at least `dist` samples apart - the higher peak wins (== scipy.signal.find_peaks(height, distance))
function peaks(x, h, dist) {
  const c = [];
  for (let i = 1; i < x.length - 1; i++) if (x[i] >= h && x[i] > x[i - 1] && x[i] > x[i + 1]) c.push(i);
  const order = c.map((_, k) => k).sort((a, b) => x[c[a]] - x[c[b]]);
  const keep = new Uint8Array(c.length).fill(1);
  for (let q = order.length - 1; q >= 0; q--) {
    const j = order[q];
    if (!keep[j]) continue;
    for (let k = j - 1; k >= 0 && c[j] - c[k] < dist; k--) keep[k] = 0;
    for (let k = j + 1; k < c.length && c[k] - c[j] < dist; k++) keep[k] = 0;
  }
  return c.filter((_, k) => keep[k]);
}

export function detectJumps(s, p = DEFAULT_DETECT, F = null) {
  F = F || computeFeatures(s, p);
  const n = F.t.length, pk = peaks(F.m4, Math.min(p.popG, p.impactG), 6);
  const impacts = pk.filter(i => F.m4[i] >= p.impactG);
  let haveGps = false; for (let i = 0; i < n && !haveGps; i++) if (Number.isFinite(F.speed[i])) haveGps = true;
  const nPre0 = Math.floor(PRE_WIN_S[0] * FS), nPre1 = Math.floor(PRE_WIN_S[1] * FS);
  const m0 = Math.floor(AIR_MARGIN_S[0] * FS), m1 = Math.floor(AIR_MARGIN_S[1] * FS), nPost = Math.floor(POST_WIN_S * FS);
  const minAir = Math.ceil(p.minAirS * FS - 1e-9);
  const cands = [];
  let k = 0;
  for (const a of pk) {
    if (F.m4[a] < p.popG) continue;
    while (k < impacts.length && impacts[k] < a + minAir) k++;      // pk is ascending, so k only moves forward
    if (k >= impacts.length) break;
    const b = impacts[k];                                            // the first hard impact after the pop
    if (b - a > p.maxAirS * FS || a < nPre0 || b + nPost > n) continue;
    const i0 = a + m0, i1 = b - m1;
    const tilt = tiltDeg(meanVector(F, a - nPre0, a - nPre1), meanVector(F, i0, i1));
    let dip = Infinity; for (let i = i0; i < i1; i++) if (F.m1[i] < dip) dip = F.m1[i];
    const vTo = haveGps ? F.speed[a] : NaN;
    let vMin = Infinity; for (let i = b; i < b + nPost; i++) if (F.speed[i] < vMin) vMin = F.speed[i];
    const vDrop = haveGps ? vTo - vMin : NaN;
    let nv = 0; for (let i = a; i < b; i++) nv += s.valid[i];
    const reasons = [];
    if (nv / (b - a) < p.minValidFrac || tilt !== tilt) reasons.push('data_gap');
    if (haveGps && vTo < p.minSpeedMps) reasons.push('slow');
    if (tilt === tilt && tilt < p.minTiltDeg) reasons.push('no_tilt');
    if (dip > p.maxDipG) reasons.push('no_float');
    if (haveGps && vDrop < -p.maxSpeedGainMps) reasons.push('speed_gain');
    if (haveGps && (b - a) / FS > p.longAirS && vTo < p.longAirMinSpeedMps) reasons.push('slow_for_airtime');
    cands.push({ tTo: F.t[a], tLand: F.t[b], airtime: F.t[b] - F.t[a], takeoffPeakG: F.m4[a], landingPeakG: F.m4[b],
      flightLevelG: median(F.m1.subarray(i0, i1)), dipG: dip, tiltDeg: tilt, speedTo: vTo, speedDrop: vDrop,
      accepted: reasons.length === 0, rejectReason: reasons.join(','), flags: '' });
  }
  // overlapping jumps: the harder pop + impact wins
  const kept = [];
  for (const c of cands.filter(c => c.accepted).sort((x, y) => (y.takeoffPeakG + y.landingPeakG) - (x.takeoffPeakG + x.landingPeakG))) {
    if (kept.every(q => c.tLand + OVERLAP_GUARD_S < q.tTo || c.tTo > q.tLand + OVERLAP_GUARD_S)) kept.push(c);
    else { c.accepted = false; c.rejectReason = 'overlap'; }
  }
  cands.sort((x, y) => x.tTo - y.tTo);
  return { cands, F };
}

// ------------------------------------------------------------------ height
export const DEFAULT_HEIGHT = { preWin: [-14, -1.5], postWin: [3, 16], tempC: 15, aEff: 1.18, airtimeOffsetS: 1.6, useBaro: false, lagRange: [-0.2, 1.2], aGrid: [1.0, 9.8, 36],
  minFitSamples: 2, splashJumpPa: 60, splashStepPa: 40, fitRmseAbsM: 0.6, fitRmseRel: 0.12, quadRmseMaxM: 1.5 };

// h = aEff (T + offset)^2 / 8, T = pop peak -> impact peak; fitted on 114 Surfr-matched jumps (4 days): 0.67 m (14 %) off on jumps >= 3 m.
// The barometer estimators below are diagnostics unless useBaro is set: on the water the port is wet (2.4-2.8 m off against Surfr).
export function heightFromAirtime(T, aEff = 1.18, offset = 1.6) { return aEff * (T + offset) ** 2 / 8; }
export function ballisticHeight(T) { return G * T * T / 8; }
function robustLevel(p) {
  p = p.filter(Number.isFinite);
  if (p.length < 2) return NaN;
  const med = median(p), mad = median(p.map(v => Math.abs(v - med))) * 1.4826 + 1e-6;
  const keep = p.filter(v => Math.abs(v - med) < Math.max(3 * mad, 8));
  return keep.length >= 2 ? median(keep) : med;
}
export function jumpModel(T, aUp, aDn) {
  const v0 = T / (1 / aUp + 1 / Math.sqrt(aUp * aDn)), tUp = v0 / aUp, hMax = v0 * v0 / (2 * aUp);
  return { v0, tUp, hMax, aUp, aDn, T, h: (tau) => { tau = Math.min(Math.max(tau, 0), T); return Math.max(0, tau <= tUp ? v0 * tau - 0.5 * aUp * tau * tau : hMax - 0.5 * aDn * (tau - tUp) ** 2); } };
}

export function estimateHeight(tTo, tLand, pressT, press, prm = DEFAULT_HEIGHT) {
  const T = tLand - tTo;
  const r = { hMaxSample: NaN, hQuad: NaN, hFit: NaN, hAirtime: heightFromAirtime(T, prm.aEff, prm.airtimeOffsetS ?? 1.25), hBallistic: ballisticHeight(T), best: NaN, method: '',
    nFlightSamples: 0, hFlightStd: NaN, splashed: false, fitRmseM: NaN, fitAUp: NaN, fitADn: NaN, fitLagS: NaN, baselinePrePa: NaN, baselinePostPa: NaN, baselineOk: false, baroOk: false, flag: '', samples: [] };
  const pre = [], post = [];
  for (let i = 0; i < pressT.length; i++) {
    if (pressT[i] >= tTo + prm.preWin[0] && pressT[i] <= tTo + prm.preWin[1]) pre.push(press[i]);
    if (pressT[i] >= tLand + prm.postWin[0] && pressT[i] <= tLand + prm.postWin[1]) post.push(press[i]);
  }
  let pPre = robustLevel(pre), pPost = robustLevel(post);
  r.baselinePrePa = pPre; r.baselinePostPa = pPost;
  if (Number.isNaN(pPre) && Number.isNaN(pPost)) { r.best = r.hAirtime; r.method = 'airtime'; r.flag = 'no_baseline'; return r; }
  if (Number.isNaN(pPre)) pPre = pPost;
  if (Number.isNaN(pPost)) pPost = pPre;
  r.baselineOk = Math.abs(pPre - pPost) < prm.splashJumpPa;
  { // water on the port: step changes of tens-hundreds of Pa between consecutive samples anywhere around the jump
    let last = null, maxStep = 0;
    for (let i = 0; i < pressT.length; i++) if (pressT[i] >= tTo + prm.preWin[0] && pressT[i] <= tLand + prm.postWin[1] && Number.isFinite(press[i])) { if (last !== null) maxStep = Math.max(maxStep, Math.abs(press[i] - last)); last = press[i]; }
    r.splashed = maxStep > prm.splashStepPa;
    if (r.splashed) r.baselineOk = false;
  }
  const tc0 = tTo + (prm.preWin[0] + prm.preWin[1]) / 2, tc1 = tLand + (prm.postWin[0] + prm.postWin[1]) / 2;
  const tt = [], hh = [];
  const rho = (0.5 * (pPre + pPost)) / (R_AIR * (prm.tempC + 273.15));
  for (let i = 0; i < pressT.length; i++) {
    if (pressT[i] >= tTo - 0.6 && pressT[i] <= tLand + 0.6 && Number.isFinite(press[i])) {
      const w = Math.min(1, Math.max(0, (pressT[i] - tc0) / Math.max(tc1 - tc0, 1e-6)));
      const base = pPre + (pPost - pPre) * w;
      tt.push(pressT[i]); hh.push((base - press[i]) / (rho * G));
    }
  }
  r.samples = tt.map((t, i) => [t, hh[i]]);
  if (!tt.length) { r.best = r.hAirtime; r.method = 'airtime'; r.flag = 'no_flight_samples'; return r; }
  const inflight = tt.map(t => t >= tTo && t <= tLand);
  r.nFlightSamples = inflight.filter(Boolean).length;
  if (!r.nFlightSamples) { r.best = r.hAirtime; r.method = 'airtime'; r.flag = 'no_flight_samples'; return r; }
  let im = -1, hm = -Infinity;
  for (let i = 0; i < tt.length; i++) if (inflight[i] && hh[i] > hm) { hm = hh[i]; im = i; }
  r.hMaxSample = hm;
  { const hf = hh.filter((_, i) => inflight[i]); const m = hf.reduce((a, b) => a + b, 0) / hf.length; r.hFlightStd = hf.length >= 2 ? Math.sqrt(hf.reduce((a, b) => a + (b - m) ** 2, 0) / hf.length) : 0; }
  if (im > 0 && im < tt.length - 1) {
    const y0 = hh[im - 1], y1 = hh[im], y2 = hh[im + 1], den = y0 - 2 * y1 + y2;
    if (den < -1e-9) { const xs = 0.5 * (y0 - y2) / den; r.hQuad = y1 - 0.25 * (y0 - y2) * xs; } else r.hQuad = y1;
  } else r.hQuad = r.hMaxSample;
  if (tt.length >= prm.minFitSamples) {
    const [aLo, aHi, na] = prm.aGrid, grid = Array.from({ length: na }, (_, i) => aLo + (aHi - aLo) * i / (na - 1));
    let best = { sse: Infinity };
    for (let li = 0; li < 8; li++) {
      const lag = prm.lagRange[0] + (prm.lagRange[1] - prm.lagRange[0]) * li / 7;
      for (const aUp of grid) for (const aDn of grid) {
        const jm = jumpModel(T, aUp, aDn);
        let sse = 0;
        for (let i = 0; i < tt.length; i++) {
          const tau = tt[i] - tTo - lag;
          const h = (tau < 0 || tau > T) ? 0 : jm.h(tau);
          sse += (h - hh[i]) ** 2;
        }
        if (sse < best.sse) best = { sse, aUp, aDn, lag, hMax: jm.hMax };
      }
    }
    r.hFit = best.hMax; r.fitAUp = best.aUp; r.fitADn = best.aDn; r.fitLagS = best.lag; r.fitRmseM = Math.sqrt(best.sse / tt.length);
  }
  const fitOk = Number.isFinite(r.hFit) && r.fitRmseM < Math.max(prm.fitRmseAbsM, prm.fitRmseRel * r.hFit);
  r.baroOk = r.baselineOk && fitOk && r.hFit <= 1.3 * r.hBallistic + 0.5;
  if (!prm.useBaro) { r.best = r.hAirtime; r.method = 'airtime'; }
  else if (r.baroOk) { r.best = r.hFit; r.method = 'baro_fit'; }
  else if (r.baselineOk && Number.isFinite(r.hQuad) && r.hQuad <= 1.3 * r.hBallistic + 0.5) { r.best = r.hQuad; r.method = 'baro_quad'; r.flag = (Number.isFinite(r.fitRmseM) && r.fitRmseM < prm.quadRmseMaxM) ? 'fit_rejected' : 'baro_noisy'; }
  else { r.best = r.hAirtime; r.method = 'airtime'; r.flag = r.splashed ? 'baro_wet' : 'baro_unreliable'; }
  return r;
}

function sessionStatsFromSpeed(s, jumps) {
  const ti = [], tv = [];
  for (let i = 0; i < s.gpsT.length; i++) if (Number.isFinite(s.speed[i])) { ti.push(s.gpsT[i]); tv.push(s.speed[i]); }
  let dist = 0, riding = 0, vmax = 0;
  for (let i = 0; i < ti.length; i++) { const dt = Math.min(5, i ? ti[i] - ti[i - 1] : 1); dist += tv[i] * dt; if (tv[i] >= 3) riding += dt; vmax = Math.max(vmax, tv[i]); }
  let v10 = 0; for (let i = 0; i + 10 <= tv.length; i++) { let a = 0; for (let k = 0; k < 10; k++) a += tv[i + k]; v10 = Math.max(v10, a / 10); }
  const ridingSpeeds = tv.filter(v => v >= 3), acc = jumps.filter(j => j.accepted);
  return {
    durationS: s.t.length ? s.t[s.t.length - 1] - s.t[0] : 0, ridingTimeS: riding, distanceM: dist, maxSpeedMps: vmax, maxSpeed10sMps: v10,
    avgRidingSpeedMps: ridingSpeeds.length ? ridingSpeeds.reduce((a, b) => a + b, 0) / ridingSpeeds.length : NaN,
    tacks: countTacks(s), jumps: acc.length, bigJumps: acc.filter(j => j.height >= 3).length,
    maxHeightM: acc.length ? Math.max(...acc.map(j => j.height)) : 0, maxAirtimeS: acc.length ? Math.max(...acc.map(j => j.airtime)) : 0,
    totalAirtimeS: acc.reduce((a, j) => a + j.airtime, 0), longestJumpM: acc.length ? Math.max(...acc.map(j => j.distance)) : 0,
    nBatches: s.nBatches, nDupBatches: s.nDup, nGaps: s.nGap, baroValidFrac: s.baroValidFrac, gpsFixFrac: s.gpsFixFrac, gpsOnly: !!s.gpsOnly,
  };
}

export function analyzeGpsOnly(records) {
  const s = buildGpsStreams(records);
  const F = { t: s.gpsT, mag: new Float64Array(0), magF: new Float64Array(0), m4: new Float64Array(0), m1: new Float64Array(0), rough: new Float64Array(0), roughRef: new Float64Array(0), speed: s.speed, jerk: new Float64Array(0) };
  return { streams: s, features: F, jumps: [], stats: sessionStatsFromSpeed(s, []), gpsOnly: true };
}

// ------------------------------------------------------------------ session
export function localXY(lat, lon, lat0, lon0) {
  const kx = 111132 * Math.cos(lat0 * Math.PI / 180), ky = 111132;
  return [(lon - lon0) * kx, (lat - lat0) * ky];
}
export function jumpDistance(s, tTo, tLand) {
  const out = { distSpeed: NaN, distPos: NaN, vTo: NaN, vLand: NaN, vMaxAir: NaN };
  const ti = [], tv = [];
  for (let i = 0; i < s.gpsT.length; i++) if (Number.isFinite(s.speed[i])) { ti.push(s.gpsT[i]); tv.push(s.speed[i]); }
  if (ti.length < 2) return out;
  let sum = 0, vmax = -Infinity, cnt = 0;
  for (let t = tTo; t < tLand; t += 0.05) { const v = interp(t, ti, tv); sum += v; vmax = Math.max(vmax, v); cnt++; }
  out.distSpeed = sum * 0.05; out.vTo = interp(tTo, ti, tv); out.vLand = interp(tLand, ti, tv); out.vMaxAir = cnt ? vmax : NaN;
  const pi = [], px = [], py = [];
  let lat0 = NaN, lon0 = NaN, n = 0;
  for (let i = 0; i < s.gpsT.length; i++) if (Number.isFinite(s.lat[i]) && Number.isFinite(s.lon[i]) && Number.isFinite(s.speed[i])) { lat0 = Number.isNaN(lat0) ? 0 : lat0; lat0 += s.lat[i]; lon0 = Number.isNaN(lon0) ? 0 : lon0; lon0 += s.lon[i]; n++; }
  if (n >= 2) {
    lat0 /= n; lon0 /= n;
    for (let i = 0; i < s.gpsT.length; i++) if (Number.isFinite(s.lat[i]) && Number.isFinite(s.lon[i]) && Number.isFinite(s.speed[i])) { const [x, y] = localXY(s.lat[i], s.lon[i], lat0, lon0); pi.push(s.gpsT[i]); px.push(x); py.push(y); }
    out.distPos = Math.hypot(interp(tLand, pi, px) - interp(tTo, pi, px), interp(tLand, pi, py) - interp(tTo, pi, py));
  }
  return out;
}
export function countTacks(s, minSpeed = 3.0, turnDeg = 100, windowS = 15) {
  const t = [], lat = [], lon = [], spd = [];
  for (let i = 0; i < s.gpsT.length; i++) if (Number.isFinite(s.lat[i]) && Number.isFinite(s.lon[i]) && Number.isFinite(s.speed[i])) { t.push(s.gpsT[i]); lat.push(s.lat[i]); lon.push(s.lon[i]); spd.push(s.speed[i]); }
  const n = t.length;
  if (n < 10) return 0;
  const lat0 = lat.reduce((a, b) => a + b, 0) / n, lon0 = lon.reduce((a, b) => a + b, 0) / n;
  const x = [], y = [];
  for (let i = 0; i < n; i++) { const [xx, yy] = localXY(lat[i], lon[i], lat0, lon0); x.push(xx); y.push(yy); }
  const sm = (arr) => arr.map((_, i) => { let s = 0, c = 0; for (let k = i - 2; k <= i + 2; k++) if (k >= 0 && k < n) { s += arr[k]; c++; } return s * 5 / c / 5; });   // numpy 'same' with zero padding -> scaled by count/5 below
  const xs = arr5(x), ys = arr5(y);
  function arr5(arr) { return arr.map((_, i) => { let s = 0; for (let k = i - 2; k <= i + 2; k++) if (k >= 0 && k < n) s += arr[k]; return s / 5; }); }
  const hdg = new Array(n);
  for (let i = 0; i < n; i++) {
    const i0 = Math.max(0, i - 1), i1 = Math.min(n - 1, i + 1);
    const dx = (xs[i1] - xs[i0]) / (t[i1] - t[i0] || 1), dy = (ys[i1] - ys[i0]) / (t[i1] - t[i0] || 1);
    hdg[i] = (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360;
  }
  let tacks = 0, lastTurn = -1e9;
  for (let i = 0; i < n; i++) {
    if (spd[i] < minSpeed || t[i] - lastTurn < windowS) continue;
    let j = i; while (j < n && t[j] < t[i] + windowS) j++;
    if (j >= n) break;
    const d = Math.abs(((hdg[j] - hdg[i] + 180) % 360 + 360) % 360 - 180);
    if (d > turnDeg && spd[j] >= minSpeed) { tacks++; lastTurn = t[j]; }
  }
  return tacks;
}

export function hasAccel(records) { return records.some(r => r.dev && r.dev.acc_0 != null && r.dev.tmr != null); }

/** GPS-only streams for a recording without KiteLog samples (e.g. recorded with another app). */
export function buildGpsStreams(records) {
  const recs = records.filter(r => r.ts != null);
  const t = Float64Array.from(recs.map(r => r.ts));
  return {
    t, fx: new Float64Array(0), fy: new Float64Array(0), fz: new Float64Array(0), valid: new Uint8Array(0),
    pressT: t, press: Float64Array.from(recs.map(r => (r.dev && r.dev.press != null) ? r.dev.press : NaN)),
    gpsT: t, lat: Float64Array.from(recs.map(r => r.lat != null ? r.lat : NaN)), lon: Float64Array.from(recs.map(r => r.lon != null ? r.lon : NaN)),
    speed: Float64Array.from(recs.map(r => r.speed != null ? r.speed : NaN)),
    tsOffset: 0, tsSlope: 1, sampleDt: NaN, nDup: 0, nGap: 0, nBatches: 0, baroValidFrac: 0, gpsFixFrac: recs.filter(r => r.lat != null).length / Math.max(1, recs.length), gpsOnly: true,
  };
}

export function analyze(records, detectParams = DEFAULT_DETECT, heightParams = DEFAULT_HEIGHT) {
  if (!hasAccel(records)) return analyzeGpsOnly(records);
  const s = buildStreams(records);
  const { cands, F } = detectJumps(s, detectParams);
  const jumps = cands.map((c, k) => {
    const h = estimateHeight(c.tTo, c.tLand, s.pressT, s.press, heightParams);
    const d = jumpDistance(s, c.tTo, c.tLand);
    const accepted = c.accepted, reason = c.rejectReason;
    return { jump: k + 1, accepted, rejectReason: reason, flags: c.flags, tTo: c.tTo, tLand: c.tLand, tsTo: c.tTo * s.tsSlope + s.tsOffset,
      airtime: c.airtime, height: h.best, heightMethod: h.method, heightFlag: h.flag, hFit: h.hFit, hQuad: h.hQuad, hMaxSample: h.hMaxSample,
      hAirtime: h.hAirtime, fitRmse: h.fitRmseM, fitAUp: h.fitAUp, fitADn: h.fitADn, fitLag: h.fitLagS, nBaro: h.nFlightSamples, baroSamples: h.samples,
      distance: Number.isFinite(d.distSpeed) ? d.distSpeed : d.distPos, distSpeed: d.distSpeed, distPos: d.distPos,
      takeoffSpeed: d.vTo, landingSpeed: d.vLand, maxAirSpeed: d.vMaxAir,
      takeoffPeakG: c.takeoffPeakG, landingPeakG: c.landingPeakG, flightLevelG: c.flightLevelG, dipG: c.dipG, tiltDeg: c.tiltDeg, speedDrop: c.speedDrop, cand: c, h };
  });
  const stats = sessionStatsFromSpeed(s, jumps);
  return { streams: s, features: F, jumps, stats };
}
