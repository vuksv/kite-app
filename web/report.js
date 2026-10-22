// report.js - KiteLog browser report: load a FIT/ZIP, analyse with kitelog.js, render.
import { decodeFit, unwrapFit } from './fit.js';
import { analyze, DEFAULT_DETECT, DEFAULT_HEIGHT, interp, jumpModel, localXY } from './kitelog.js';

const $ = (id) => document.getElementById(id);
const UNITS = { kmh: [3.6, 'km/h'], kn: [1.94384, 'kn'], mps: [1, 'm/s'] };
const KMH = 3.6;
const PARAM_IDS = ['roughRatio', 'roughFloorG', 'takeoffMinPeakG', 'landingMinPeakG', 'levelMinG', 'levelMaxG', 'minSmoothS', 'takeoffMinJerkG', 'landingMinJerkG'];
const state = { buffer: null, name: '', fit: null, result: null, sortKey: 'jump', sortDir: 1, map: null };

const fmt = (v, d = 1) => Number.isFinite(v) ? v.toFixed(d) : '–';
const speedUnit = () => UNITS[$('unit').value] || UNITS.kmh;
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const hms = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return (h ? h + ':' : '') + String(m).padStart(h ? 2 : 1, '0') + ':' + String(x).padStart(2, '0'); };
const clock = (unix) => new Date(unix * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

function setStatus(msg, err = false) { const el = $('status'); el.textContent = msg; el.className = 'status' + (err ? ' err' : ''); }

function readParams() {
  const d = { ...DEFAULT_DETECT };
  for (const id of PARAM_IDS) { const v = parseFloat($(id).value); if (Number.isFinite(v)) d[id] = v; }
  const vMin = parseFloat($('minSpeedKmh').value);        // entered in km/h, detector works in m/s
  if (Number.isFinite(vMin)) d.minSpeedMps = vMin / KMH;
  const h = { ...DEFAULT_HEIGHT, aEff: parseFloat($('aEff').value) || 3.2 };
  return { d, h };
}
function setDefaults() {
  for (const id of PARAM_IDS) $(id).value = DEFAULT_DETECT[id];
  $('minSpeedKmh').value = +(DEFAULT_DETECT.minSpeedMps * KMH).toFixed(1);
  $('aEff').value = DEFAULT_HEIGHT.aEff;
}

// ------------------------------------------------------------------ loading
async function loadBuffer(buffer, name) {
  try {
    setStatus('Decoding ' + name + ' …');
    const raw = await unwrapFit(buffer, name);
    const fit = decodeFit(raw);
    if (!fit.records.length) throw new Error('No record messages found in this file');
    state.buffer = buffer; state.name = name; state.fit = fit;
    $('rerun').disabled = false;
    run();
  } catch (e) {
    console.error(e);
    setStatus('Could not analyse this file: ' + (e.message || e), true);
  }
}
function run() {
  const { d, h } = readParams();
  try {
    const t0 = performance.now();
    state.result = analyze(state.fit.records, d, h);
    render();
    const acc = state.result.jumps.filter(j => j.accepted).length;
    setStatus(`${state.name}: ${state.fit.records.length} records, ${acc} jumps found (${Math.round(performance.now() - t0)} ms).`);
  } catch (e) {
    console.error(e);
    setStatus('Analysis failed: ' + (e.message || e), true);
  }
}

// ------------------------------------------------------------------ svg helpers
function niceTicks(lo, hi, n = 5) {
  if (!(hi > lo)) { hi = lo + 1; }
  const raw = (hi - lo) / n, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
  const step = (m >= 5 ? 10 : m >= 2 ? 5 : m >= 1 ? 2 : 1) * p;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}
/** cfg: {w,h,xMin,xMax,yMin,yMax,series:[{x,y,color,width,dots,dash,r}],vlines:[{x,label}],hlines:[y],title,yLabel,xLabel,xFmt} */
function svgChart(cfg) {
  const w = cfg.w || 420, h = cfg.h || 150, L = 44, R = 8, T = cfg.title ? 22 : 8, B = cfg.xLabel ? 30 : 18;
  const xs = (x) => L + (x - cfg.xMin) / (cfg.xMax - cfg.xMin || 1) * (w - L - R);
  const ys = (y) => T + (1 - (y - cfg.yMin) / (cfg.yMax - cfg.yMin || 1)) * (h - T - B);
  let s = `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(cfg.title || '')}">`;
  if (cfg.title) s += `<text class="title" x="${L}" y="14">${esc(cfg.title)}</text>`;
  for (const t of niceTicks(cfg.yMin, cfg.yMax, 4)) {
    if (t < cfg.yMin || t > cfg.yMax) continue;
    s += `<line class="grid" x1="${L}" x2="${w - R}" y1="${ys(t).toFixed(1)}" y2="${ys(t).toFixed(1)}"/><text x="${L - 4}" y="${(ys(t) + 4).toFixed(1)}" text-anchor="end">${+t.toFixed(2)}</text>`;
  }
  for (const t of niceTicks(cfg.xMin, cfg.xMax, 6)) {
    if (t < cfg.xMin || t > cfg.xMax) continue;
    s += `<text x="${xs(t).toFixed(1)}" y="${h - B + 13}" text-anchor="middle">${cfg.xFmt ? cfg.xFmt(t) : +t.toFixed(2)}</text>`;
  }
  s += `<line class="axis" x1="${L}" x2="${w - R}" y1="${ys(cfg.yMin)}" y2="${ys(cfg.yMin)}"/>`;
  for (const y of cfg.hlines || []) s += `<line x1="${L}" x2="${w - R}" y1="${ys(y).toFixed(1)}" y2="${ys(y).toFixed(1)}" stroke="var(--muted)" stroke-dasharray="3 3"/>`;
  for (const v of cfg.vlines || []) {
    if (v.x < cfg.xMin || v.x > cfg.xMax) continue;
    s += `<line x1="${xs(v.x).toFixed(1)}" x2="${xs(v.x).toFixed(1)}" y1="${T}" y2="${ys(cfg.yMin)}" stroke="${v.color || 'var(--text-2)'}" stroke-width="${v.width || 1}" opacity="${v.opacity || 1}"/>`;
    if (v.label) s += `<text x="${(xs(v.x) + 3).toFixed(1)}" y="${T + 11}">${esc(v.label)}</text>`;
  }
  for (const se of cfg.series || []) {
    const col = se.color || 'var(--s1)';
    if (se.dots) {
      for (let i = 0; i < se.x.length; i++) if (Number.isFinite(se.y[i]) && se.x[i] >= cfg.xMin && se.x[i] <= cfg.xMax)
        s += `<circle cx="${xs(se.x[i]).toFixed(1)}" cy="${ys(se.y[i]).toFixed(1)}" r="${se.r || 3.5}" fill="${col}" stroke="var(--surface-2)" stroke-width="1"/>`;
      continue;
    }
    let d = '', pen = false;
    for (let i = 0; i < se.x.length; i++) {
      const y = se.y[i];
      if (!Number.isFinite(y) || se.x[i] < cfg.xMin || se.x[i] > cfg.xMax) { pen = false; continue; }
      d += (pen ? 'L' : 'M') + xs(se.x[i]).toFixed(1) + ' ' + ys(Math.min(cfg.yMax, Math.max(cfg.yMin, y))).toFixed(1);
      pen = true;
    }
    s += `<path d="${d}" fill="none" stroke="${col}" stroke-width="${se.width || 1.6}" ${se.dash ? 'stroke-dasharray="4 3"' : ''} stroke-linejoin="round" opacity="${se.opacity || 1}"/>`;
  }
  if (cfg.yLabel) s += `<text transform="translate(11 ${(T + (h - T - B) / 2).toFixed(1)}) rotate(-90)" text-anchor="middle">${esc(cfg.yLabel)}</text>`;
  if (cfg.xLabel) s += `<text x="${(L + (w - L - R) / 2).toFixed(1)}" y="${h - 4}" text-anchor="middle">${esc(cfg.xLabel)}</text>`;
  return s + '</svg>';
}
const legend = (items) => `<div class="legend">${items.map(([c, l]) => `<span style="--c:${c}">${esc(l)}</span>`).join('')}</div>`;

// ------------------------------------------------------------------ rendering
function render() {
  const { streams: s, jumps, stats } = state.result;
  const [k, u] = speedUnit();
  const rec0 = state.fit.records[0];
  const start = new Date(rec0.ts * 1000);
  $('title').textContent = start.toLocaleDateString([], { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' }) + ' · ' + clock(rec0.ts);
  const sport = state.fit.sessions[0] && state.fit.sessions[0].sport === 44 ? 'Kiteboarding' : 'Activity';
  $('subtitle').textContent = `${sport} · ${hms(stats.durationS)} recorded · riding ${hms(stats.ridingTimeS)} · ${state.fit.records.length} records`;
  const pill = (txt, cls = '') => `<span class="pill ${cls}">${esc(txt)}</span>`;
  const gapCls = stats.nGaps > stats.nBatches * 0.01 ? 'warn' : '';
  $('health').innerHTML = pill(`${stats.nBatches} accel batches`) + pill(`${stats.nDupBatches} duplicated`, stats.nDupBatches > 5 ? 'warn' : '') +
    pill(`${stats.nGaps} gaps`, gapCls) + pill(`baro ${Math.round(stats.baroValidFrac * 100)} %`, stats.baroValidFrac < 0.95 ? 'warn' : '') +
    pill(`GPS fix ${Math.round(stats.gpsFixFrac * 100)} %`, stats.gpsFixFrac < 0.9 ? 'warn' : '') +
    (state.fit.errors.length ? pill('decoder: ' + state.fit.errors[0], 'bad') : '');
  const tiles = [
    ['Jumps', stats.jumps, ''], ['Max height', fmt(stats.maxHeightM, 1), 'm'], ['Max airtime', fmt(stats.maxAirtimeS, 2), 's'],
    ['Longest jump', fmt(stats.longestJumpM, 0), 'm'], ['Max speed', fmt(stats.maxSpeedMps * k, 1), u], ['Max 10 s speed', fmt(stats.maxSpeed10sMps * k, 1), u],
    ['Distance', fmt(stats.distanceM / 1000, 1), 'km'], ['Tacks', stats.tacks, ''], ['Total airtime', fmt(stats.totalAirtimeS, 1), 's'],
  ];
  $('tiles').innerHTML = tiles.map(([kk, v, uu]) => `<div class="tile"><div class="k">${kk}</div><div class="v">${v}<span class="u">${uu}</span></div></div>`).join('');
  renderTable();
  renderTrack();
  renderTimeline();
  $('map').classList.add('hidden');
  if (state.map) { state.map.remove(); state.map = null; }
  $('report').classList.remove('hidden');
}

function renderTable() {
  const { jumps } = state.result;
  const [k, u] = speedUnit();
  const show = $('showRejected').checked ? jumps : jumps.filter(j => j.accepted);
  const cols = [['jump', '#'], ['tsTo', 'Time'], ['height', 'Height (m)'], ['airtime', 'Airtime (s)'], ['distance', 'Length (m)'], ['takeoffSpeed', `Take-off (${u})`], ['heightMethod', 'Method']];
  $('jumps').querySelector('thead').innerHTML = '<tr>' + cols.map(([key, label]) => `<th data-key="${key}">${label}${state.sortKey === key ? (state.sortDir > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('') + '</tr>';
  const sorted = [...show].sort((a, b) => { const x = a[state.sortKey], y = b[state.sortKey]; return (typeof x === 'string' ? x.localeCompare(y) : (x - y)) * state.sortDir; });
  $('jumps').querySelector('tbody').innerHTML = sorted.map(j => `<tr class="jump ${j.accepted ? '' : 'rej'}" data-jump="${j.jump}">
      <td>${j.jump}${j.accepted ? '' : ' ✕'}</td><td>${clock(j.tsTo)}</td><td><b>${fmt(j.height, 1)}</b></td><td>${fmt(j.airtime, 2)}</td><td>${fmt(j.distance, 0)}</td><td>${fmt(j.takeoffSpeed * k, 1)}</td>
      <td>${esc(j.heightMethod)}${j.flags ? ' <span class="pill warn">' + esc(j.flags) + '</span>' : ''}${j.accepted ? '' : ' <span class="pill bad">' + esc(j.rejectReason) + '</span>'}</td></tr>`).join('');
  $('jumps').querySelectorAll('th').forEach(th => th.onclick = () => { const key = th.dataset.key; state.sortDir = state.sortKey === key ? -state.sortDir : (key === 'jump' || key === 'tsTo' ? 1 : -1); state.sortKey = key; renderTable(); });
  $('jumps').querySelectorAll('tr.jump').forEach(tr => tr.onclick = () => toggleDetail(tr));
}

function toggleDetail(tr) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains('detail')) { next.remove(); return; }
  const j = state.result.jumps.find(x => x.jump === +tr.dataset.jump);
  const row = document.createElement('tr');
  row.className = 'detail';
  row.innerHTML = `<td colspan="7">${jumpDetailHtml(j)}</td>`;
  tr.after(row);
}

function jumpDetailHtml(j) {
  const { streams: s, features: F } = state.result;
  const [k, u] = speedUnit();
  const t0 = j.tTo, w0 = t0 - 6, w1 = j.tLand + 6;
  let i0 = 0, i1 = F.t.length;
  while (i0 < F.t.length && F.t[i0] < w0) i0++;
  while (i1 > 0 && F.t[i1 - 1] > w1) i1--;
  const tx = Array.from(F.t.subarray(i0, i1), v => v - t0);
  const vl = [{ x: 0, label: 'take-off' }, { x: j.airtime, label: 'landing' }];
  const xr = { xMin: w0 - t0, xMax: w1 - t0 };
  const mag = Array.from(F.mag.subarray(i0, i1)), m4 = Array.from(F.m4.subarray(i0, i1)), sp = Array.from(F.speed.subarray(i0, i1), v => v * k);
  const yMaxA = Math.min(8, Math.max(2.5, Math.max(...mag.filter(Number.isFinite)) * 1.05));
  const a = svgChart({ ...xr, h: 150, yMin: 0, yMax: yMaxA, title: `Accelerometer - load ${fmt(j.takeoffPeakG)} g · flight ${fmt(j.flightLevelG, 2)} g · impact ${fmt(j.landingPeakG)} g`, yLabel: 'g',
    series: [{ x: tx, y: mag, color: 'var(--muted)', width: 0.7, opacity: 0.8 }, { x: tx, y: m4, color: 'var(--s1)', width: 1.8 }], vlines: vl, hlines: [1] });
  const bs = j.baroSamples || [];
  const bx = bs.map(p => p[0] - t0), by = bs.map(p => p[1]);
  let fitX = [], fitY = [];
  if (Number.isFinite(j.hFit)) { const jm = jumpModel(j.airtime, j.fitAUp, j.fitADn); for (let q = 0; q <= 80; q++) { const tau = j.airtime * q / 80; fitX.push(tau + j.fitLag); fitY.push(jm.h(tau)); } }
  const hb = Math.max(1, ...by.filter(Number.isFinite), ...fitY) * 1.15;
  const b = svgChart({ ...xr, h: 150, yMin: Math.min(-0.5, ...by.filter(Number.isFinite)), yMax: hb, title: `Barometer - ${j.heightMethod} ${fmt(j.height)} m · airtime model ${fmt(j.hAirtime)} m`, yLabel: 'm',
    series: [{ x: fitX, y: fitY, color: 'var(--s3)', width: 1.8 }, { x: bx, y: by, color: 'var(--s3)', dots: true }], vlines: vl, hlines: [0] });
  const c = svgChart({ ...xr, h: 130, yMin: 0, yMax: Math.max(5, Math.max(...sp.filter(Number.isFinite)) * 1.1), title: `GPS speed - take-off ${fmt(j.takeoffSpeed * k)} ${u} · landing ${fmt(j.landingSpeed * k)} ${u}`, yLabel: u, xLabel: 'seconds from take-off',
    series: [{ x: tx, y: sp, color: 'var(--s4)', width: 1.8 }], vlines: vl });
  const extra = `<p style="font-size:13px">transients: take-off jerk ${fmt(j.takeoffJerkG, 2)} g/sample · landing jerk ${fmt(j.landingJerkG, 2)} g/sample · roughness in flight ${fmt(j.roughIn, 3)} g vs reference ${fmt(j.roughRef, 3)} g</p><p style="font-size:13px">height estimators: fit ${fmt(j.hFit)} m · parabola ${fmt(j.hQuad)} m · max sample ${fmt(j.hMaxSample)} m · airtime model ${fmt(j.hAirtime)} m · ${j.nBaro} baro samples in flight · fit RMSE ${fmt(j.fitRmse, 2)} m, a_up ${fmt(j.fitAUp)} a_dn ${fmt(j.fitADn)} m/s², lag ${fmt(j.fitLag, 2)} s · length by speed ${fmt(j.distSpeed, 0)} m / by position ${fmt(j.distPos, 0)} m${j.heightFlag ? ' · <b>' + esc(j.heightFlag) + '</b>' : ''}</p>`;
  return legend([['var(--muted)', '|f| 25 Hz'], ['var(--s1)', '|f| low-pass 4 Hz']]) + a + legend([['var(--s3)', 'barometer height / physics fit']]) + b + c + extra;
}

function trackXY() {
  const { streams: s } = state.result;
  const pts = [];
  for (let i = 0; i < s.gpsT.length; i++) if (Number.isFinite(s.lat[i]) && Number.isFinite(s.lon[i])) pts.push([s.gpsT[i], s.lat[i], s.lon[i]]);
  if (pts.length < 2) return null;
  const lat0 = pts.reduce((a, p) => a + p[1], 0) / pts.length, lon0 = pts.reduce((a, p) => a + p[2], 0) / pts.length;
  return { pts, lat0, lon0, xy: pts.map(p => localXY(p[1], p[2], lat0, lon0)) };
}

function renderTrack() {
  const tr = trackXY();
  if (!tr) { $('track').innerHTML = '<p>No GPS positions in this file.</p>'; return; }
  const { jumps } = state.result;
  const xs = tr.xy.map(p => p[0]), ys = tr.xy.map(p => p[1]);
  const xMin = Math.min(...xs), xMax = Math.max(...xs), yMin = Math.min(...ys), yMax = Math.max(...ys);
  const span = Math.max(xMax - xMin, yMax - yMin, 50) * 1.08, cx = (xMin + xMax) / 2, cy = (yMin + yMax) / 2;
  const W = 420, H = Math.min(420, Math.max(220, Math.round(W * (yMax - yMin + 20) / (xMax - xMin + 20))));
  const scale = Math.min(W / span, H / span);
  const px = (x) => W / 2 + (x - cx) * scale, py = (y) => H / 2 - (y - cy) * scale;
  let d = '';
  tr.xy.forEach((p, i) => { d += (i ? 'L' : 'M') + px(p[0]).toFixed(1) + ' ' + py(p[1]).toFixed(1); });
  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="GPS track with jumps"><path d="${d}" fill="none" stroke="var(--s1)" stroke-width="1.4" stroke-linejoin="round"/>`;
  const tt = tr.pts.map(p => p[0]);
  for (const j of jumps.filter(j => j.accepted)) {
    const x = interp(j.tTo, tt, xs), y = interp(j.tTo, tt, ys);
    svg += `<circle cx="${px(x).toFixed(1)}" cy="${py(y).toFixed(1)}" r="${(3 + Math.sqrt(Math.max(0, j.height)) * 1.6).toFixed(1)}" fill="var(--s2)" fill-opacity="0.75" stroke="var(--surface-2)" stroke-width="1"><title>Jump ${j.jump}: ${fmt(j.height)} m, ${fmt(j.airtime, 2)} s</title></circle>`;
  }
  const bar = [5000, 2000, 1000, 500, 200, 100, 50, 20, 10].find(b => b <= span / 4) || 10;
  svg += `<line x1="12" x2="${(12 + bar * scale).toFixed(1)}" y1="${H - 10}" y2="${H - 10}" stroke="var(--text-2)" stroke-width="2"/><text x="12" y="${H - 14}">${bar} m</text></svg>`;
  $('track').innerHTML = legend([['var(--s1)', 'track'], ['var(--s2)', 'jumps (size = height)']]) + svg;
}

function renderTimeline() {
  const { streams: s, jumps } = state.result;
  const [k, u] = speedUnit();
  const t0 = s.t[0];
  const x = [], y = [];
  for (let i = 0; i < s.gpsT.length; i++) if (Number.isFinite(s.speed[i])) { x.push((s.gpsT[i] - t0) / 60); y.push(s.speed[i] * k); }
  const acc = jumps.filter(j => j.accepted);
  const top = [...acc].sort((a, b) => b.height - a.height).slice(0, 5).map(j => j.jump);
  const vlines = acc.map(j => ({ x: (j.tTo - t0) / 60, color: 'var(--s2)', opacity: 0.7, label: top.includes(j.jump) ? fmt(j.height) + ' m' : '' }));
  $('timeline').innerHTML = legend([['var(--s4)', 'speed'], ['var(--s2)', 'jumps (top 5 labelled)']]) +
    svgChart({ w: 420, h: 170, xMin: 0, xMax: Math.max(1, ...x), yMin: 0, yMax: Math.max(5, ...y) * 1.1, yLabel: u, xLabel: 'session time (min)', series: [{ x, y, color: 'var(--s4)', width: 1.1 }], vlines });
}

// ------------------------------------------------------------------ map
function loadScript(src) { return new Promise((res, rej) => { const el = document.createElement('script'); el.src = src; el.onload = res; el.onerror = () => rej(new Error('failed to load ' + src)); document.head.appendChild(el); }); }
function loadCss(href) { const el = document.createElement('link'); el.rel = 'stylesheet'; el.href = href; document.head.appendChild(el); }
async function showMap() {
  const tr = trackXY();
  if (!tr) return;
  try {
    if (!window.L) {
      loadCss('https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css');
      await loadScript('https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js');
    }
  } catch (e) { setStatus('Map library could not be loaded (offline?)', true); return; }
  $('map').classList.remove('hidden');
  if (state.map) state.map.remove();
  const map = L.map('map');
  state.map = map;
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap' }).addTo(map);
  const line = L.polyline(tr.pts.map(p => [p[1], p[2]]), { color: '#2a78d6', weight: 2 }).addTo(map);
  map.fitBounds(line.getBounds());
  const tt = tr.pts.map(p => p[0]), la = tr.pts.map(p => p[1]), lo = tr.pts.map(p => p[2]);
  for (const j of state.result.jumps.filter(j => j.accepted)) {
    L.circleMarker([interp(j.tTo, tt, la), interp(j.tTo, tt, lo)], { radius: 4 + Math.sqrt(Math.max(0, j.height)) * 2, color: '#eb6834', fillColor: '#eb6834', fillOpacity: 0.6, weight: 1 })
      .bindPopup(`<b>Jump ${j.jump}</b><br>height ${fmt(j.height)} m<br>airtime ${fmt(j.airtime, 2)} s<br>length ${fmt(j.distance, 0)} m`).addTo(map);
  }
}

// ------------------------------------------------------------------ exports
function download(name, text, type = 'text/plain') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
function exportCsv() {
  const cols = ['jump', 'accepted', 'rejectReason', 'flags', 'tsTo', 'airtime', 'height', 'heightMethod', 'heightFlag', 'hFit', 'hQuad', 'hMaxSample', 'hAirtime', 'fitRmse', 'fitAUp', 'fitADn', 'fitLag', 'nBaro', 'distance', 'distSpeed', 'distPos', 'takeoffSpeed', 'landingSpeed', 'maxAirSpeed', 'takeoffPeakG', 'landingPeakG', 'takeoffJerkG', 'landingJerkG', 'flightLevelG', 'roughIn', 'roughRef'];
  const speedCols = ['takeoffSpeed', 'landingSpeed', 'maxAirSpeed'];      // m/s internally, km/h in the file
  const rows = state.result.jumps.map(j => cols.map(c => { const v = speedCols.includes(c) ? j[c] * KMH : j[c]; return typeof v === 'number' ? (Number.isFinite(v) ? v.toFixed(4) : '') : '"' + String(v).replace(/"/g, '""') + '"'; }).join(','));
  const head = cols.map(c => speedCols.includes(c) ? c + 'Kmh' : c);
  download(state.name.replace(/\.[^.]+$/, '') + '_jumps.csv', head.join(',') + '\n' + rows.join('\n'), 'text/csv');
}
function exportGpx() {
  const { streams: s } = state.result;
  let g = '<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="kitelog" xmlns="http://www.topografix.com/GPX/1/1"><trk><name>KiteLog session</name><trkseg>\n';
  for (let i = 0; i < s.gpsT.length; i++) if (Number.isFinite(s.lat[i]) && Number.isFinite(s.lon[i]))
    g += `<trkpt lat="${s.lat[i].toFixed(7)}" lon="${s.lon[i].toFixed(7)}"><time>${new Date((s.gpsT[i] + s.tsOffset) * 1000).toISOString()}</time></trkpt>\n`;
  download(state.name.replace(/\.[^.]+$/, '') + '.gpx', g + '</trkseg></trk></gpx>', 'application/gpx+xml');
}

// ------------------------------------------------------------------ cloud archive
function cloudSettings() { return { url: ($('cloudUrl').value || '').replace(/\/+$/, ''), token: $('cloudToken').value || '' }; }
function saveCloudSettings() { try { localStorage.setItem('kitelog.cloud', JSON.stringify(cloudSettings())); } catch (e) { /* private mode */ } }
function loadCloudSettings() {
  try { const c = JSON.parse(localStorage.getItem('kitelog.cloud') || 'null'); if (c) { $('cloudUrl').value = c.url || ''; $('cloudToken').value = c.token || ''; } } catch (e) { /* ignore */ }
  if (!$('cloudUrl').value && location.protocol.startsWith('http') && !/github\.io|localhost|127\.0\.0\.1/.test(location.host)) $('cloudUrl').value = location.origin;
}
async function uploadToCloud() {
  const { url, token } = cloudSettings();
  const el = $('cloudStatus');
  if (!state.buffer) { el.textContent = 'Load a file first.'; return; }
  if (!url) { el.textContent = 'Enter the service URL under Analysis settings › Cloud archive.'; el.className = 'status err'; return; }
  saveCloudSettings();
  el.className = 'status'; el.textContent = 'Uploading …';
  try {
    const fd = new FormData();
    fd.append('file', new Blob([state.buffer]), state.name);
    const r = await fetch(url + '/api/analyze', { method: 'POST', body: fd, headers: token ? { 'X-KiteLog-Token': token } : {} });
    if (!r.ok) throw new Error(r.status + ' ' + (await r.text()).slice(0, 200));
    const j = await r.json();
    el.innerHTML = `Archived as <b>${esc(j.session_id)}</b>: ${j.stats.jumps} jumps, max ${fmt(j.stats.max_height_m)} m · <a href="${url}/api/sessions/${esc(j.session_id)}/jumps.csv${token ? '?token=' + encodeURIComponent(token) : ''}">jumps.csv</a>`;
  } catch (e) { el.className = 'status err'; el.textContent = 'Upload failed: ' + (e.message || e); }
}

// ------------------------------------------------------------------ wiring
$('file').addEventListener('change', async (e) => { const f = e.target.files[0]; if (f) loadBuffer(await f.arrayBuffer(), f.name); });
$('sampleBtn').addEventListener('click', async () => {
  try { setStatus('Loading sample …'); const r = await fetch('sample/synthetic.fit'); if (!r.ok) throw new Error(r.status); loadBuffer(await r.arrayBuffer(), 'synthetic.fit'); }
  catch (e) { setStatus('Sample not reachable from here (open the page over http(s), or pick web/sample/synthetic.fit with the file chooser).', true); }
});
$('rerun').addEventListener('click', run);
$('resetSettings').addEventListener('click', () => { setDefaults(); if (state.fit) run(); });
$('unit').addEventListener('change', () => { if (state.result) render(); });
$('showRejected').addEventListener('change', () => { if (state.result) renderTable(); });
$('csvBtn').addEventListener('click', exportCsv);
$('gpxBtn').addEventListener('click', exportGpx);
$('printBtn').addEventListener('click', () => window.print());
$('mapBtn').addEventListener('click', showMap);
$('cloudBtn').addEventListener('click', uploadToCloud);
$('cloudUrl').addEventListener('change', saveCloudSettings);
$('cloudToken').addEventListener('change', saveCloudSettings);
setDefaults();
loadCloudSettings();
if (location.search.includes('sample')) $('sampleBtn').click();
