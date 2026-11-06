// gopro.js - cross-check KiteLog jumps against GoPro clips (telemetry read locally by gpmf.js).
import { readGoPro } from './gpmf.js';
import { decodeFit, unwrapFit } from './fit.js';
import { analyze, DEFAULT_DETECT, DEFAULT_HEIGHT } from './kitelog.js';
import { FS, sessionSignal, goproMagnitude, refineGps, searchByMotion } from './goproalign.js';

const $ = (id) => document.getElementById(id);
const G = 9.80665, KMH = 3.6;
const fmt = (v, d = 1) => Number.isFinite(v) ? v.toFixed(d) : '–';
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const mmss = (s) => { const m = Math.floor(s / 60), r = s - 60 * m; return `${m}:${r.toFixed(1).padStart(4, '0')}`; };
const local = (u) => new Date(u * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
function setStatus(msg, err = false) { const el = $('status'); el.textContent = msg; el.className = 'status' + (err ? ' err' : ''); }

function savedParams() {
  const d = { ...DEFAULT_DETECT }, h = { ...DEFAULT_HEIGHT };
  try {
    const o = JSON.parse(localStorage.getItem('kitelog.params') || 'null');
    if (o) {
      for (const k in o) { const v = parseFloat(o[k]); if (k in d && Number.isFinite(v)) d[k] = v; }
      if (Number.isFinite(parseFloat(o.minSpeedKmh))) d.minSpeedMps = parseFloat(o.minSpeedKmh) / KMH;
      if (Number.isFinite(parseFloat(o.aEff))) h.aEff = parseFloat(o.aEff);
    }
  } catch (e) { /* defaults */ }
  return { d, h };
}

// ---------------------------------------------------------------- KiteLog sessions (cached)
let sessionList = null;
const sessionCache = new Map();
async function sessions() {
  if (!sessionList) {
    const r = await fetch('../sessions/sessions.json', { cache: 'no-cache' });
    sessionList = r.ok ? await r.json() : [];
    for (const e of sessionList) { e.t0 = Date.parse(e.start_utc) / 1000; e.t1 = e.t0 + (e.records || 0) * 1.05 + 60; }
  }
  return sessionList;
}
async function loadSession(e) {
  if (!sessionCache.has(e.file)) {
    const r = await fetch('../sessions/' + encodeURIComponent(e.file));
    const fit = decodeFit(await unwrapFit(await r.arrayBuffer(), e.file));
    const { d, h } = savedParams();
    const res = analyze(fit.records, d, h);
    if (res.gpsOnly) { sessionCache.set(e.file, null); return null; }
    sessionCache.set(e.file, { e, res, ...sessionSignal(res) });
  }
  return sessionCache.get(e.file);
}

// returns { S (session), offset (UTC of video time 0), method, r, ratio }
async function align(gp, mag) {
  const list = await sessions();
  const dur = gp.durationS;
  if (gp.startUtc) {
    for (const e of list.filter(e => e.t0 < gp.startUtc + dur + 120 && e.t1 > gp.startUtc - 120)) {
      const S = await loadSession(e); if (!S) continue;
      const c = refineGps(mag, S, gp.startUtc);
      const ok = c.r > 0.35;
      return { S, offset: gp.startUtc + (ok ? c.shift : 0), r: c.r,
        method: ok ? `camera GPS clock, refined by ${c.shift >= 0 ? '+' : ''}${c.shift.toFixed(2)} s from motion` : 'camera GPS clock' };
    }
    return { S: null, offset: gp.startUtc, method: 'camera GPS clock' };
  }
  const ref = gp.cameraClock;                       // camera clock, time zone unknown: +-14 h
  let best = null;
  for (const e of list.filter(e => !ref || Math.abs(e.t0 - ref) < 14 * 3600 + (e.records || 0))) {
    const S = await loadSession(e); if (!S || S.m4.length < mag.length) continue;
    const m = searchByMotion(mag, S);
    if (!best || m.r > best.r) best = { S, ...m, method: 'motion match (no GPS on the camera)' };
  }
  return best || { S: null, offset: null, method: 'no match' };
}

// GoPro's own airtime for a KiteLog jump: pop and impact peaks in the 200 Hz accelerometer
function goproAirtime(gp, tTo, tLand) {
  const a = gp.streams.ACCL; if (!a) return NaN;
  const seg = (t0, t1) => { const out = []; for (let i = 0; i < a.t.length; i++) if (a.t[i] >= t0 && a.t[i] <= t1) out.push([a.t[i], Math.hypot(...a.v[i].slice(0, 3)) / G]); return out; };
  const pk = (arr) => arr.reduce((m, p) => p[1] > m[1] ? p : m, [NaN, -1]);
  const p = pk(seg(tTo - 0.6, tTo + 0.4)), l = pk(seg(tLand - 0.4, tLand + 0.8));
  if (!(p[1] > 1.8 && l[1] > 1.8)) return NaN;
  return l[0] - p[0];
}

// ---------------------------------------------------------------- chart (one axis, both in g)
function chart(series, vlines, x0, x1) {
  const W = 420, H = 130, L = 30, R = 6, T = 8, B = 20, yMax = 6;
  const xs = (x) => L + (x - x0) / (x1 - x0) * (W - L - R), ys = (y) => T + (1 - Math.min(y, yMax) / yMax) * (H - T - B);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="GoPro and KiteLog acceleration">`;
  for (const v of [0, 2, 4, 6]) s += `<line class="grid" x1="${L}" x2="${W - R}" y1="${ys(v)}" y2="${ys(v)}"/><text x="${L - 4}" y="${ys(v) + 4}" text-anchor="end">${v}</text>`;
  for (let v = Math.ceil(x0); v <= x1; v += 2) s += `<text x="${xs(v).toFixed(1)}" y="${H - 5}" text-anchor="middle">${mmss(v).replace(/\.\d$/, '')}</text>`;
  for (const v of vlines) s += `<line x1="${xs(v).toFixed(1)}" x2="${xs(v).toFixed(1)}" y1="${T}" y2="${H - B}" stroke="var(--text-2)" stroke-width="1"/>`;
  for (const se of series) {
    let d = ''; se.x.forEach((x, i) => { if (x >= x0 && x <= x1 && Number.isFinite(se.y[i])) d += (d ? 'L' : 'M') + xs(x).toFixed(1) + ' ' + ys(se.y[i]).toFixed(1); });
    s += `<path d="${d}" fill="none" stroke="${se.c}" stroke-width="1.5" stroke-linejoin="round"/>`;
  }
  return s + '</svg>';
}

// ---------------------------------------------------------------- export
async function exportTelemetry(gp, name, al) {
  const a = gp.streams.ACCL || { t: [], v: [] }, g = gp.streams.GPS5 || { t: [], v: [] };
  const r3 = (x) => Math.round(x * 1000) / 1000;
  const obj = { format: 'kitelog-gopro-1', clip: name, model: gp.model, firmware: gp.firmware, durationS: gp.durationS, startUtc: gp.startUtc, cameraClock: gp.cameraClock,
    gpsFixFrac: gp.gpsFixFrac, aligned: al && al.offset ? { utcOfVideoZero: al.offset, method: al.method, session: al.S && al.S.e.file } : null,
    accl: { units: 'm/s2', t: a.t.map(r3), xyz: a.v.map(v => v.slice(0, 3).map(r3)) },
    gps5: { fields: ['lat', 'lon', 'alt_m', 'speed2d_mps', 'speed3d_mps'], t: g.t.map(r3), v: g.v.map(v => v.map(x => Math.round(x * 1e7) / 1e7)) } };
  const blob = new Blob([JSON.stringify(obj)]);
  const gz = typeof CompressionStream !== 'undefined' ? await new Response(blob.stream().pipeThrough(new CompressionStream('gzip'))).blob() : blob;
  const el = document.createElement('a'); el.href = URL.createObjectURL(gz); el.download = name.replace(/\.[^.]+$/, '') + (gz === blob ? '.gopro.json' : '.gopro.json.gz'); el.click();
}

// ---------------------------------------------------------------- per clip
async function handle(file, box) {
  box.innerHTML = `<h2 style="margin-top:0">${esc(file.name)}</h2><div class="status">Reading telemetry …</div>`;
  let gp;
  try { gp = await readGoPro(file, f => { box.querySelector('.status').textContent = `Reading telemetry … ${Math.round(f * 100)} %`; }); }
  catch (e) { box.querySelector('.status').textContent = 'No telemetry: ' + (e.message || e); box.querySelector('.status').className = 'status err'; return; }
  const mag = goproMagnitude(gp);
  const st = (k) => gp.streams[k] ? `${k} ${fmt(gp.streams[k].rateHz, 0)} Hz` : '';
  let html = `<h2 style="margin-top:0">${esc(file.name)}</h2>
    <p>${esc(gp.model || 'GoPro')} · ${mmss(gp.durationS)} · ${[st('ACCL'), st('GYRO'), st('GPS5')].filter(Boolean).join(' · ')}</p>
    <p>${gp.startUtc ? `GPS clock: starts ${esc(local(gp.startUtc))} (fix ${Math.round(gp.gpsFixFrac * 100)} %)` : `No GPS fix in this clip; camera clock says ${gp.cameraClock ? esc(new Date(gp.cameraClock * 1000).toISOString().slice(0, 19).replace('T', ' ')) : 'nothing'} (camera time zone)`}</p>`;
  box.innerHTML = html + '<div class="status">Finding the KiteLog session …</div>';
  let al = null;
  try { al = mag ? await align(gp, mag) : null; } catch (e) { console.error(e); }
  if (!al || !al.S) {
    html += `<p class="status err">No KiteLog session overlaps this clip${gp.startUtc ? '' : ' (and no motion match was found)'}.</p>`;
  } else {
    const S = al.S, off = al.offset;
    const conf = al.ratio ? ` · match ${fmt(al.r, 2)}, ${fmt(al.ratio, 1)}× the next best` : (al.r ? ` · motion agreement ${fmt(al.r, 2)}` : '');
    html += `<p><b>KiteLog session:</b> <a href="./?fit=sessions/${encodeURIComponent(S.e.file)}">${esc(S.e.label)}</a> · ${esc(al.method)}${conf}</p>`;
    if (al.ratio && al.ratio < 1.3) html += `<p class="status err">Weak match: check a jump visually before trusting the times below.</p>`;
    const inClip = S.jumps.filter(j => { const v = j.tsTo - off; return v > -1 && v < gp.durationS; });
    if (!inClip.length) html += '<p>No KiteLog jumps happen during this clip.</p>';
    else {
      html += `<div class="tablewrap"><table><thead><tr><th>In video</th><th>Height m</th><th>KiteLog air s</th><th>GoPro air s</th><th>Length m</th></tr></thead><tbody>`;
      const charts = [];
      for (const j of inClip) {
        const v0 = j.tsTo - off, v1 = v0 + j.airtime, ga = goproAirtime(gp, v0, v1);
        html += `<tr><td><b>${mmss(Math.max(0, v0))}</b></td><td>${fmt(j.height)}</td><td>${fmt(j.airtime, 2)}</td><td>${fmt(ga, 2)}</td><td>${fmt(j.distance, 0)}</td></tr>`;
        const w0 = v0 - 5, w1 = v1 + 5, gx = [], gy = [], kx = [], ky = [];
        for (let k = Math.max(0, Math.floor(w0 * FS)); k < Math.min(mag.length, w1 * FS); k++) { gx.push(k / FS); gy.push(mag[k]); }
        const base = (off - S.u0) * FS;
        for (let k = Math.floor(w0 * FS); k < w1 * FS; k++) { const i = Math.round(base + k); if (i >= 0 && i < S.m4.length) { kx.push(k / FS); ky.push(S.m4[i]); } }
        charts.push(`<h3>${mmss(Math.max(0, v0))} · ${fmt(j.height)} m · ${fmt(j.airtime, 2)} s</h3>` + chart([{ x: gx, y: gy, c: 'var(--s3)' }, { x: kx, y: ky, c: 'var(--s1)' }], [v0, v1], w0, w1));
      }
      html += `</tbody></table></div><p class="sub" style="font-size:13px">"In video" is the take-off moment in the clip. GoPro air = pop-to-impact on the camera's 200 Hz accelerometer.</p>
        <div class="legend"><span style="--c:var(--s3)">GoPro |a| (g)</span><span style="--c:var(--s1)">KiteLog wrist |f| (g)</span><span style="--c:var(--text-2)">KiteLog take-off / landing</span></div>` + charts.join('');
    }
  }
  html += `<div class="row noprint" style="margin-top:10px"><button type="button" class="exp">Download telemetry (small file to share)</button></div>`;
  box.innerHTML = html;
  box.querySelector('.exp').onclick = () => exportTelemetry(gp, file.name, al);
}

$('clips').addEventListener('change', async (ev) => {
  const files = Array.from(ev.target.files || []); const res = $('results'); res.innerHTML = '';
  setStatus(`${files.length} clip${files.length === 1 ? '' : 's'}`);
  for (const f of files) { const box = document.createElement('section'); box.className = 'card'; res.appendChild(box); await handle(f, box); }
  setStatus('Done.');
});
