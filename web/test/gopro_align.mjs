// Mechanics test for goproalign.js: fake a GoPro clip from a real KiteLog session (different body part:
// scaled, filtered, noisy, slight clock-rate difference), hide its start, and check it is found again.
// usage: node web/test/gopro_align.mjs sessions/<file>.fit
import { readFileSync } from 'node:fs';
import { decodeFit } from '../fit.js';
import { analyze } from '../kitelog.js';
import { sessionSignal, goproMagnitude, refineGps, searchByMotion, FS } from '../goproalign.js';
const buf = readFileSync(process.argv[2]);
const res = analyze(decodeFit(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)).records);
const sig = sessionSignal(res);
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
let fails = 0;
for (const [startMin, durS] of [[12, 240], [30, 90], [45, 480], [8, 60]]) {
  if ((startMin * 60 + durS) * FS >= sig.m4.length) continue;
  const trueUtc = sig.u0 + startMin * 60 + 0.37;
  // camera: 200 Hz, |a| = 0.8 * wrist + noise, clock 0.05 % fast
  const t = [], v = [];
  for (let k = 0; k < durS * 200; k++) {
    const tt = k / 200, i = (trueUtc - sig.u0 + tt * 1.0005) * FS, i0 = Math.floor(i);
    const wrist = sig.m4[i0] + (sig.m4[i0 + 1] - sig.m4[i0]) * (i - i0);
    const g = (1 + 0.8 * (wrist - 1) + (rnd() - 0.5) * 0.3) * 9.80665;
    t.push(tt); v.push([g * 0.6, g * 0.48, g * 0.64]);
  }
  const gp = { durationS: durS, streams: { ACCL: { t, v } } };
  const mag = goproMagnitude(gp);
  const m = searchByMotion(mag, sig);
  const g = refineGps(mag, sig, trueUtc + 3.2);          // GPS clock 3.2 s off
  const errM = m.offset - trueUtc, errG = 3.2 + g.shift;
  const ok = Math.abs(errM) < 0.3 && Math.abs(errG) < 0.3;
  if (!ok) fails++;
  console.log(`clip @${startMin} min, ${durS} s: motion search error ${errM.toFixed(2)} s (r ${m.r.toFixed(2)}, ${m.ratio.toFixed(1)}x next) | GPS refine error ${errG.toFixed(2)} s (r ${g.r.toFixed(2)}) ${ok ? 'OK' : 'FAIL'}`);
}
if (fails) { console.log('FAIL'); process.exit(1); } else console.log('PASS');
