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
function nanmax(a) { let m = -Infinity; for (const v of a) if (v === v && v > m) m = v; return m; }
function nanmin(a) { let m = Infinity; for (const v of a) if (v === v && v < m) m = v; return m; }
function nanmean(a) { let s = 0, n = 0; for (const v of a) if (v === v) { s += v; n++; } return n ? s / n : NaN; }

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
function movingRms(x, n) {                         // numpy 'same' convolution window alignment
  const len = x.length, cs = new Float64Array(len + 1);
  for (let i = 0; i < len; i++) cs[i + 1] = cs[i] + x[i] * x[i];
  const before = n - 1 - Math.floor((n - 1) / 2);
  const out = new Float64Array(len);
  for (let i = 0; i < len; i++) {
    const a = Math.max(0, i - before), b = Math.min(len, i - before + n);
    out[i] = Math.sqrt((cs[b] - cs[a]) / n);
  }
  return out;
}
function blockRollingMedian(x, block, winBlocks) {
  const n = x.length, nb = Math.ceil(n / block), bm = new Float64Array(nb).fill(NaN);
  for (let b = 0; b < nb; b++) bm[b] = median(x.subarray(b * block, Math.min(n, (b + 1) * block)));
  const half = Math.floor(winBlocks / 2), ref = new Float64Array(nb).fill(NaN);
  for (let b = 0; b < nb; b++) {
    const seg = Array.from(bm.subarray(Math.max(0, b - half), Math.min(nb, b + half + 1))).filter(Number.isFinite);
    if (seg.length >= 5) ref[b] = median(seg);
  }
  const okIdx = [], okVal = [];
  for (let b = 0; b < nb; b++) if (Number.isFinite(ref[b])) { okIdx.push(b); okVal.push(ref[b]); }
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) { const b = Math.floor(i / block); out[i] = okIdx.length ? interp(b, okIdx, okVal) : 0.2; }
  return out;
}
function lastPeak(x, a0, a1, thr) {                // last local maximum >= thr in x[a0:a1]
  let last = null;
  for (let i = Math.max(a0 + 1, 1); i < a1 - 1; i++) if (x[i] >= thr && x[i] > x[i - 1] && x[i] >= x[i + 1]) last = i;
  return last;
}

// ------------------------------------------------------------------ streams
export function buildStreams(records) {
  const recs = records.filter(r => r.dev && r.dev.tmr != null && r.dev.meta != null).map(r => ({ ...r, tmr: r.dev.tmr, meta: unpackMeta(r.dev.meta) }));
  recs.sort((a, b) => a.tmr - b.tmr);
  const uniq = [];
  let nDup = 0;
  for (const r of recs) { if (uniq.length && uniq[uniq.length - 1].tmr === r.tmr) nDup++; else uniq.push(r); }
  if (!uniq.length) throw new Error('No KiteLog developer fields in this file (is it a KiteLog recording?)');
  const tBatch = uniq.map(r => r.tmr / 1000);
  const tsOffset = median(uniq.map((r, i) => r.ts - tBatch[i]));
  const st = [], sf = [];
  let nGap = 0;
  uniq.forEach((r, i) => {
    const codes = unpackBatch(ACC_NAMES.map(k => r.dev[k]));
    const n = codes.length;
    if (!n) return;
    for (let k = 0; k < n; k++) { st.push(tBatch[i] - (n - 1 - k) / FS); sf.push([decodeCode(codes[k][0]), decodeCode(codes[k][1]), decodeCode(codes[k][2])]); }
    if (i > 0 && tBatch[i] - tBatch[i - 1] > 1.5) nGap++;
  });
  const t0 = st[0], t1 = st[st.length - 1];
  const len = Math.round((t1 - t0) * FS) + 1;
  const t = new Float64Array(len);
  for (let i = 0; i < len; i++) t[i] = t0 + i / FS;
  const fx = new Float64Array(len).fill(NaN), fy = new Float64Array(len).fill(NaN), fz = new Float64Array(len).fill(NaN);
  const valid = new Uint8Array(len);
  for (let k = 0; k < st.length; k++) {
    const idx = Math.round((st[k] - t0) * FS);
    if (idx >= 0 && idx < len) { fx[idx] = sf[k][0]; fy[idx] = sf[k][1]; fz[idx] = sf[k][2]; valid[idx] = 1; }
  }
  return {
    t, fx, fy, fz, valid,
    pressT: Float64Array.from(tBatch), press: Float64Array.from(uniq.map(r => r.dev.press != null ? r.dev.press : NaN)),
    gpsT: Float64Array.from(uniq.map(r => r.ts - tsOffset - 0.5)),
    lat: Float64Array.from(uniq.map(r => r.lat != null ? r.lat : NaN)), lon: Float64Array.from(uniq.map(r => r.lon != null ? r.lon : NaN)),
    speed: Float64Array.from(uniq.map(r => r.speed != null ? r.speed : NaN)),
    tsOffset, nDup, nGap, nBatches: uniq.length,
    baroValidFrac: uniq.filter(r => r.dev.press != null).length / uniq.length,
    gpsFixFrac: uniq.filter(r => r.lat != null).length / uniq.length,
  };
}

// ------------------------------------------------------------------ detection
export const DEFAULT_DETECT = {
  lpTransientHz: 4.0, lpLevelHz: 1.0, hpSplitHz: 3.0, roughWinS: 0.32, roughRefWinS: 60,
  roughRatio: 0.30, roughFloorG: 0.035, levelMinG: 0.25, levelMaxG: 1.45, minSmoothS: 0.32, mergeGapS: 0.6,
  takeoffSearchS: 1.2, takeoffMinPeakG: 1.35, landingSearchS: 0.9, landingMinPeakG: 1.6, landingRiseG: 0.5,
  minSpeedMps: 3.0, minFlightSpeedMps: 2.5, minFlightSpeedRatio: 0.4, minValidFrac: 0.6, maxDataGapS: 1.5,
  maxAirtimeS: 14.0, takeoffPoint: 'peak',
};

export function computeFeatures(s, p = DEFAULT_DETECT) {
  const n = s.t.length, mag = new Float64Array(n);
  for (let i = 0; i < n; i++) mag[i] = s.valid[i] ? Math.hypot(s.fx[i], s.fy[i], s.fz[i]) : NaN;
  const magF = Float64Array.from(mag);
  const vi = [], vv = [];
  for (let i = 0; i < n; i++) if (s.valid[i]) { vi.push(i); vv.push(mag[i]); }
  for (let i = 0; i < n; i++) if (!s.valid[i]) magF[i] = vi.length >= 2 ? interp(i, vi, vv) : 1.0;
  const m4 = lowpass(magF, p.lpTransientHz), m1 = lowpass(magF, p.lpLevelHz), l3 = lowpass(magF, p.hpSplitHz);
  const hp = new Float64Array(n);
  for (let i = 0; i < n; i++) hp[i] = magF[i] - l3[i];
  const rough = movingRms(hp, Math.max(3, Math.round(p.roughWinS * FS)));
  for (let i = 0; i < n; i++) if (!s.valid[i]) rough[i] = NaN;
  const roughRef = blockRollingMedian(rough, FS, Math.round(p.roughRefWinS));
  for (let i = 0; i < n; i++) if (rough[i] !== rough[i]) rough[i] = Infinity;
  const gi = [], gv = [];
  for (let i = 0; i < s.gpsT.length; i++) if (Number.isFinite(s.speed[i])) { gi.push(s.gpsT[i]); gv.push(s.speed[i]); }
  const speed = new Float64Array(n);
  for (let i = 0; i < n; i++) speed[i] = gi.length >= 2 ? interp(s.t[i], gi, gv) : NaN;
  return { t: s.t, mag, magF, m4, m1, rough, roughRef, speed };
}

export function detectJumps(s, p = DEFAULT_DETECT, F = null) {
  F = F || computeFeatures(s, p);
  const n = F.t.length;
  const smooth = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const thr = Math.max(p.roughRatio * F.roughRef[i], p.roughFloorG);
    smooth[i] = (F.rough[i] < thr && F.m1[i] > p.levelMinG && F.m1[i] < p.levelMaxG && s.valid[i]) ? 1 : 0;
  }
  const segs = [];
  for (let i = 0; i < n; i++) { if (smooth[i] && (i === 0 || !smooth[i - 1])) segs.push([i, i + 1]); else if (smooth[i]) segs[segs.length - 1][1] = i + 1; }
  const merged = [];
  for (const [a, b] of segs) {
    if (merged.length) {
      const pb = merged[merged.length - 1][1], gap = a - pb;
      const quietBlip = gap <= p.mergeGapS * FS && nanmax(F.m4.subarray(pb, a)) < p.landingMinPeakG;
      let inv = 0; for (let i = pb; i < a; i++) if (!s.valid[i]) inv++;
      const dataGap = gap <= p.maxDataGapS * FS && inv / Math.max(1, gap) > 0.8;
      if (quietBlip || dataGap) { merged[merged.length - 1][1] = b; continue; }
    }
    merged.push([a, b]);
  }
  const cands = [];
  for (const [a, b] of merged) {
    if (b - a < p.minSmoothS * FS) continue;
    if (a < 2 * FS || b > n - 2 * FS) continue;
    const a0 = Math.max(0, a - Math.round(p.takeoffSearchS * FS)), a1 = Math.min(n, a + Math.round(0.15 * FS));
    const flightLevel = median(F.m1.subarray(a, b));
    let iPk = lastPeak(F.m4, a0, a1, p.takeoffMinPeakG);
    if (iPk === null) { let m = -Infinity; iPk = a0; for (let i = a0; i < a1; i++) if (F.m4[i] > m) { m = F.m4[i]; iPk = i; } }
    let pkTo = F.m4[iPk], takeoffInGap = false;
    const invalidBefore = []; for (let i = a0; i < a1; i++) if (!s.valid[i]) invalidBefore.push(i);
    if (pkTo < p.takeoffMinPeakG && invalidBefore.length) { takeoffInGap = true; iPk = invalidBefore[invalidBefore.length - 1]; pkTo = NaN; }
    let tTo;
    if (p.takeoffPoint === 'mid' && !takeoffInGap) {
      const mid = 0.5 * (pkTo + flightLevel); let j = iPk; while (j < b && F.m4[j] > mid) j++; tTo = F.t[j];
    } else tTo = F.t[iPk];
    const b1 = Math.min(n, b + Math.round(p.landingSearchS * FS));
    let iLp = b, m = -Infinity; for (let i = b; i < b1; i++) if (F.m4[i] > m) { m = F.m4[i]; iLp = i; }
    let pkLand = F.m4[iLp], landingInGap = false, k;
    const invalidAfter = []; for (let i = b; i < b1; i++) if (!s.valid[i]) invalidAfter.push(i);
    if (pkLand < p.landingMinPeakG && invalidAfter.length) { landingInGap = true; k = invalidAfter[0]; pkLand = NaN; }
    else {
      let iRaw = b, mm = -Infinity; for (let i = b; i < b1; i++) if (F.magF[i] > mm) { mm = F.magF[i]; iRaw = i; }
      k = iRaw; while (k > a && F.magF[k] > flightLevel + p.landingRiseG) k--;
      k = Math.min(k + 1, iRaw);
    }
    const tLand = F.t[k], airtime = tLand - tTo;
    const spd = Number.isFinite(F.speed[a]) ? F.speed[a] : NaN;
    const spdMin = nanmin(F.speed.subarray(a, b));
    const reasons = [], flags = [];
    if (takeoffInGap) flags.push('takeoff_in_gap'); else if (pkTo < p.takeoffMinPeakG) reasons.push('no_takeoff_peak');
    if (landingInGap) flags.push('landing_in_gap'); else if (pkLand < p.landingMinPeakG) reasons.push('no_landing_peak');
    if (takeoffInGap && landingInGap) reasons.push('both_transients_in_gap');
    if (Number.isFinite(spd) && spd < p.minSpeedMps) reasons.push('slow');
    if (Number.isFinite(spdMin) && spdMin < Math.max(p.minFlightSpeedMps, p.minFlightSpeedRatio * spd)) reasons.push('speed_collapse');
    if (airtime <= 0.3 || airtime > p.maxAirtimeS) reasons.push('airtime_range');
    let vf = 0; for (let i = a; i < b; i++) vf += s.valid[i]; vf /= (b - a);
    if (vf < p.minValidFrac) reasons.push('gap_inside'); else if (vf < 1) flags.push('gap_in_flight');
    const roughIn = nanmean(Array.from(F.rough.subarray(a, b)).map(v => Number.isFinite(v) ? v : NaN));
    cands.push({ tSmoothStart: F.t[a], tSmoothEnd: F.t[b - 1], tTo, tLand, airtime, takeoffPeakG: pkTo, landingPeakG: pkLand,
      flightLevelG: flightLevel, roughIn, roughRef: median(F.roughRef.subarray(a, b)), speedTo: spd,
      accepted: reasons.length === 0, rejectReason: reasons.join(','), flags: flags.join(',') });
  }
  return { cands, F };
}

// ------------------------------------------------------------------ height
export const DEFAULT_HEIGHT = { preWin: [-14, -1.5], postWin: [3, 16], tempC: 15, aEff: 3.2, lagRange: [-0.2, 1.2], aGrid: [1.0, 9.8, 36],
  minFitSamples: 2, splashJumpPa: 60, fitRmseAbsM: 0.6, fitRmseRel: 0.12, quadRmseMaxM: 1.5 };

export function heightFromAirtime(T, aEff = 3.2) { return aEff * T * T / 8; }
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
  const r = { hMaxSample: NaN, hQuad: NaN, hFit: NaN, hAirtime: heightFromAirtime(T, prm.aEff), hBallistic: ballisticHeight(T), best: NaN, method: '',
    nFlightSamples: 0, fitRmseM: NaN, fitAUp: NaN, fitADn: NaN, fitLagS: NaN, baselinePrePa: NaN, baselinePostPa: NaN, baselineOk: false, baroOk: false, flag: '', samples: [] };
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
  if (r.baroOk) { r.best = r.hFit; r.method = 'baro_fit'; }
  else if (r.baselineOk && Number.isFinite(r.hQuad) && Number.isFinite(r.fitRmseM) && r.fitRmseM < prm.quadRmseMaxM && r.hQuad <= 1.3 * r.hBallistic + 0.5) { r.best = r.hQuad; r.method = 'baro_quad'; r.flag = 'fit_rejected'; }
  else { r.best = r.hAirtime; r.method = 'airtime'; r.flag = 'baro_unreliable'; }
  return r;
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
export function baroConsistent(c, h, aFloor = 1.0) {
  if (!h.baselineOk || !Number.isFinite(h.hFit) || c.airtime < 2.0) return true;
  return h.hFit >= 0.5 * aFloor * c.airtime * c.airtime / 8;
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

export function analyze(records, detectParams = DEFAULT_DETECT, heightParams = DEFAULT_HEIGHT) {
  const s = buildStreams(records);
  const { cands, F } = detectJumps(s, detectParams);
  const jumps = cands.map((c, k) => {
    const h = estimateHeight(c.tTo, c.tLand, s.pressT, s.press, heightParams);
    const d = jumpDistance(s, c.tTo, c.tLand);
    let accepted = c.accepted, reason = c.rejectReason;
    if (accepted && !baroConsistent(c, h)) { accepted = false; reason = 'baro_inconsistent'; }
    return { jump: k + 1, accepted, rejectReason: reason, flags: c.flags, tTo: c.tTo, tLand: c.tLand, tsTo: c.tTo + s.tsOffset,
      airtime: c.airtime, height: h.best, heightMethod: h.method, heightFlag: h.flag, hFit: h.hFit, hQuad: h.hQuad, hMaxSample: h.hMaxSample,
      hAirtime: h.hAirtime, fitRmse: h.fitRmseM, fitAUp: h.fitAUp, fitADn: h.fitADn, fitLag: h.fitLagS, nBaro: h.nFlightSamples, baroSamples: h.samples,
      distance: Number.isFinite(d.distSpeed) ? d.distSpeed : d.distPos, distSpeed: d.distSpeed, distPos: d.distPos,
      takeoffSpeed: d.vTo, landingSpeed: d.vLand, maxAirSpeed: d.vMaxAir,
      takeoffPeakG: c.takeoffPeakG, landingPeakG: c.landingPeakG, flightLevelG: c.flightLevelG, roughIn: c.roughIn, roughRef: c.roughRef, cand: c, h };
  });
  // session stats
  const ti = [], tv = [];
  for (let i = 0; i < s.gpsT.length; i++) if (Number.isFinite(s.speed[i])) { ti.push(s.gpsT[i]); tv.push(s.speed[i]); }
  let dist = 0, riding = 0, vmax = 0;
  for (let i = 0; i < ti.length; i++) { const dt = Math.min(5, i ? ti[i] - ti[i - 1] : 1); dist += tv[i] * dt; if (tv[i] >= 3) riding += dt; vmax = Math.max(vmax, tv[i]); }
  let v10 = 0; for (let i = 0; i + 10 <= tv.length; i++) { let a = 0; for (let k = 0; k < 10; k++) a += tv[i + k]; v10 = Math.max(v10, a / 10); }
  const ridingSpeeds = tv.filter(v => v >= 3);
  const acc = jumps.filter(j => j.accepted);
  const stats = {
    durationS: s.t[s.t.length - 1] - s.t[0], ridingTimeS: riding, distanceM: dist, maxSpeedMps: vmax, maxSpeed10sMps: v10,
    avgRidingSpeedMps: ridingSpeeds.length ? ridingSpeeds.reduce((a, b) => a + b, 0) / ridingSpeeds.length : NaN,
    tacks: countTacks(s), jumps: acc.length,
    maxHeightM: acc.length ? Math.max(...acc.map(j => j.height)) : 0, maxAirtimeS: acc.length ? Math.max(...acc.map(j => j.airtime)) : 0,
    totalAirtimeS: acc.reduce((a, j) => a + j.airtime, 0), longestJumpM: acc.length ? Math.max(...acc.map(j => j.distance)) : 0,
    nBatches: s.nBatches, nDupBatches: s.nDup, nGaps: s.nGap, baroValidFrac: s.baroValidFrac, gpsFixFrac: s.gpsFixFrac,
  };
  return { streams: s, features: F, jumps, stats };
}
