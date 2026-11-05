// all.js - overview of every session listed in sessions/sessions.json.
import { decodeFit, unwrapFit } from './fit.js';
import { analyze, DEFAULT_DETECT, DEFAULT_HEIGHT } from './kitelog.js';

const $ = (id) => document.getElementById(id);
const KMH = 3.6;
const fmt = (v, d = 1) => Number.isFinite(v) ? v.toFixed(d) : '–';
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const hm = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`; };
const day = (unix) => new Date(unix * 1000).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
const clock = (unix) => new Date(unix * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const link = (e) => `./?fit=sessions/${encodeURIComponent(e.file)}`;

function setStatus(msg, err = false) { const el = $('status'); el.textContent = msg; el.className = 'status' + (err ? ' err' : ''); }

// the same analysis settings the single-session page saved on this device
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

async function main() {
  let list;
  try {
    const r = await fetch('sessions/sessions.json', { cache: 'no-cache' });
    if (!r.ok) throw new Error(r.status);
    list = await r.json();
  } catch (e) { setStatus('No session list found (sessions/sessions.json).', true); return; }
  const { d, h } = savedParams();
  const results = [];
  for (let i = 0; i < list.length; i++) {
    const e = list[i];
    setStatus(`Analysing ${i + 1} of ${list.length}: ${e.label} …`);
    try {
      const r = await fetch('sessions/' + encodeURIComponent(e.file));
      if (!r.ok) throw new Error(r.status);
      const fit = decodeFit(await unwrapFit(await r.arrayBuffer(), e.file));
      const res = analyze(fit.records, d, h);
      const start = fit.records.length ? fit.records[0].ts : Date.parse(e.start_utc) / 1000;
      results.push({ e, start, stats: res.stats, jumps: res.jumps.filter(j => j.accepted), gpsOnly: !!res.stats.gpsOnly });
    } catch (err) {
      results.push({ e, start: Date.parse(e.start_utc) / 1000, error: String(err.message || err) });
    }
    await new Promise(res => setTimeout(res, 0));       // keep the page responsive between files
  }
  results.sort((a, b) => b.start - a.start);
  render(results);
  const n = results.filter(x => !x.error).length;
  setStatus(`${n} sessions analysed${n < results.length ? `, ${results.length - n} could not be read` : ''}.`);
}

function render(results) {
  const ok = results.filter(x => !x.error);
  const allJumps = ok.flatMap(x => x.jumps.map(j => ({ ...j, session: x })));
  const sum = (f) => ok.reduce((a, x) => a + (f(x) || 0), 0);
  const max = (arr) => arr.length ? Math.max(...arr) : NaN;
  const tiles = [
    ['Sessions', ok.length, ''], ['On the water', hm(sum(x => x.stats.ridingTimeS)), ''], ['Distance', fmt(sum(x => x.stats.distanceM) / 1000, 1), 'km'],
    ['Jumps', allJumps.length, ''], ['Max height', fmt(max(allJumps.map(j => j.height)), 1), 'm'], ['Max airtime', fmt(max(allJumps.map(j => j.airtime)), 2), 's'],
    ['Longest jump', fmt(max(allJumps.map(j => j.distance)), 0), 'm'], ['Max speed', fmt(max(ok.map(x => x.stats.maxSpeedMps)) * KMH, 1), 'km/h'],
    ['Total airtime', fmt(allJumps.reduce((a, j) => a + j.airtime, 0) / 60, 1), 'min'],
  ];
  $('tiles').innerHTML = tiles.map(([k, v, u]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}<span class="u">${u}</span></div></div>`).join('');

  // sessions table
  const maxJ = Math.max(1, ...ok.map(x => x.jumps.length));
  $('sessions').querySelector('thead').innerHTML = '<tr><th>Session</th><th>Riding</th><th>km</th><th>Max km/h</th><th>Jumps</th><th>Max m</th><th>Max s</th></tr>';
  $('sessions').querySelector('tbody').innerHTML = results.map(x => {
    const name = `<a href="${link(x.e)}">${esc(day(x.start))} ${esc(clock(x.start))}</a><div class="sub">${esc(x.e.label)}${x.e.note ? ' · ' + esc(x.e.note) : ''}</div>`;
    if (x.error) return `<tr><td>${name}</td><td colspan="6" style="text-align:left">could not be read: ${esc(x.error)}</td></tr>`;
    const st = x.stats, nj = x.jumps.length;
    const jumps = x.gpsOnly ? '<span class="sub">GPS only</span>' : `${nj} <span class="bar" style="width:${Math.round(40 * nj / maxJ)}px"></span>`;
    return `<tr><td>${name}</td><td>${hm(st.ridingTimeS)}</td><td>${fmt(st.distanceM / 1000, 1)}</td><td>${fmt(st.maxSpeedMps * KMH, 1)}</td>
      <td>${jumps}</td><td>${x.gpsOnly ? '–' : fmt(max(x.jumps.map(j => j.height)), 1)}</td><td>${x.gpsOnly ? '–' : fmt(max(x.jumps.map(j => j.airtime)), 2)}</td></tr>`;
  }).join('');

  renderScatter(ok.filter(x => !x.gpsOnly).slice().reverse(), allJumps);
  renderBest(allJumps);
  $('bestBy').onchange = () => renderBest(allJumps);
  $('content').classList.remove('hidden');
}

// one column per KiteLog session (oldest left), every jump a dot at its height
function renderScatter(sessions, allJumps) {
  if (!sessions.length) { $('scatter').innerHTML = '<p>No KiteLog sessions with jumps yet.</p>'; return; }
  const W = 420, H = 220, L = 34, R = 8, T = 10, B = 44;
  const yMax = Math.max(2, Math.ceil(Math.max(...allJumps.map(j => j.height), 1) + 0.5));
  const colW = (W - L - R) / sessions.length;
  const ys = (v) => T + (1 - v / yMax) * (H - T - B);
  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Jump heights per session">`;
  for (let v = 0; v <= yMax; v += yMax > 8 ? 2 : 1) svg += `<line class="grid" x1="${L}" x2="${W - R}" y1="${ys(v).toFixed(1)}" y2="${ys(v).toFixed(1)}"/><text x="${L - 4}" y="${(ys(v) + 4).toFixed(1)}" text-anchor="end">${v}</text>`;
  svg += `<text transform="translate(10 ${((H - B + T) / 2).toFixed(0)}) rotate(-90)" text-anchor="middle">height (m)</text>`;
  sessions.forEach((x, i) => {
    const cx = L + colW * (i + 0.5);
    x.jumps.forEach((j, k) => {
      const jitter = ((k * 37) % 17 - 8) / 8 * Math.min(colW * 0.32, 14);
      svg += `<a href="${link(x.e)}"><circle cx="${(cx + jitter).toFixed(1)}" cy="${ys(j.height).toFixed(1)}" r="3.6" fill="var(--s2)" fill-opacity="0.75" stroke="var(--surface-2)" stroke-width="1"><title>${esc(day(x.start))}: ${fmt(j.height)} m, ${fmt(j.airtime, 2)} s, ${fmt(j.distance, 0)} m</title></circle></a>`;
    });
    const best = Math.max(...x.jumps.map(j => j.height), 0);
    svg += `<line x1="${(cx - colW * 0.35).toFixed(1)}" x2="${(cx + colW * 0.35).toFixed(1)}" y1="${ys(best).toFixed(1)}" y2="${ys(best).toFixed(1)}" stroke="var(--text-2)" stroke-width="1.5"/>`;
    svg += `<text x="${cx.toFixed(1)}" y="${H - B + 16}" text-anchor="middle">${esc(new Date(x.start * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' }))}</text>`;
    svg += `<text x="${cx.toFixed(1)}" y="${H - B + 30}" text-anchor="middle">${x.jumps.length} jumps</text>`;
  });
  $('scatter').innerHTML = `<div class="legend"><span style="--c:var(--s2)">jump</span><span style="--c:var(--text-2)">best of the session</span></div>` + svg + '</svg>';
}

function renderBest(allJumps) {
  const by = $('bestBy').value;
  const top = [...allJumps].sort((a, b) => b[by] - a[by]).slice(0, 15);
  $('best').querySelector('thead').innerHTML = '<tr><th>#</th><th>When</th><th>Height m</th><th>Airtime s</th><th>Length m</th><th>Take-off km/h</th></tr>';
  $('best').querySelector('tbody').innerHTML = top.map((j, i) => `<tr><td>${i + 1}</td>
      <td><a href="${link(j.session.e)}">${esc(day(j.tsTo))} ${esc(clock(j.tsTo))}</a></td>
      <td><b>${fmt(j.height, 1)}</b></td><td>${fmt(j.airtime, 2)}</td><td>${fmt(j.distance, 0)}</td><td>${fmt(j.takeoffSpeed * KMH, 1)}</td></tr>`).join('');
}

main();
