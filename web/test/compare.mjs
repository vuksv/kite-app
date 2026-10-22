// Node test: decode the synthetic FIT with fit.js, run kitelog.js, compare with the Python pipeline's jumps.csv.
// usage: node web/test/compare.mjs <file.fit> [python_jumps.csv]
import { readFileSync } from 'node:fs';
import { decodeFit, unwrapFit } from '../fit.js';
import { analyze } from '../kitelog.js';

const [fitPath, csvPath] = process.argv.slice(2);
const buf = readFileSync(fitPath);
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
const raw = await unwrapFit(ab, fitPath);
const t0 = Date.now();
const fit = decodeFit(raw);
console.log(`decoded ${fit.records.length} records, ${fit.laps.length} laps, ${fit.sessions.length} sessions, ${fit.fieldDescriptions.length} dev fields, errors=${JSON.stringify(fit.errors)} in ${Date.now() - t0} ms`);
const t1 = Date.now();
const res = analyze(fit.records);
console.log(`analysed in ${Date.now() - t1} ms: ${res.jumps.filter(j => j.accepted).length} jumps / ${res.jumps.length} candidates; max h ${res.stats.maxHeightM.toFixed(2)} m, tacks ${res.stats.tacks}, dist ${(res.stats.distanceM / 1000).toFixed(2)} km, dup ${res.stats.nDupBatches}, gaps ${res.stats.nGaps}`);
for (const j of res.jumps) console.log(`  #${j.jump} ${j.accepted ? 'OK ' : 'REJ'} air=${j.airtime.toFixed(2)} h=${j.height.toFixed(2)} (${j.heightMethod}) d=${j.distance.toFixed(1)} vto=${j.takeoffSpeed.toFixed(2)} pk=${j.takeoffPeakG.toFixed(2)}/${j.landingPeakG.toFixed(2)} ${j.rejectReason} ${j.flags}`);
if (csvPath) {
  const splitCsv = (l) => (l.match(/("([^"]|"")*"|[^,]*)(,|$)/g) || []).slice(0, -1).map(x => x.replace(/,$/, '').replace(/^"|"$/g, ''));
  const lines = readFileSync(csvPath, 'utf8').trim().split('\n');
  const head = splitCsv(lines[0]);
  const col = (n) => head.indexOf(n);
  const py = lines.slice(1).map(splitCsv).map(r => ({ jump: +r[col('jump')], accepted: r[col('accepted')] === 'True', t_to: +r[col('t_to')], airtime: +r[col('airtime_s')], height: +r[col('height_m')], distance: +r[col('distance_m')], method: r[col('height_method')] }));
  let worstAir = 0, worstH = 0, worstD = 0, matched = 0;
  for (const p of py) {
    const j = res.jumps.find(x => Math.abs(x.tTo - p.t_to) < 0.5);
    if (!j) { console.log(`  python jump ${p.jump} (t_to=${p.t_to.toFixed(1)}) not found in JS`); continue; }
    matched++;
    worstAir = Math.max(worstAir, Math.abs(j.airtime - p.airtime));
    worstH = Math.max(worstH, Math.abs(j.height - p.height));
    worstD = Math.max(worstD, Math.abs(j.distance - p.distance));
    if (j.accepted !== p.accepted || j.heightMethod !== p.method) console.log(`  mismatch jump ${p.jump}: accepted ${j.accepted}/${p.accepted} method ${j.heightMethod}/${p.method}`);
  }
  console.log(`python comparison: ${matched}/${py.length} matched; worst |d airtime| ${worstAir.toFixed(3)} s, |d height| ${worstH.toFixed(3)} m, |d distance| ${worstD.toFixed(2)} m`);
  if (matched !== py.length || worstAir > 0.05 || worstH > 0.15) { console.log('FAIL'); process.exit(1); }
  console.log('PASS');
}
