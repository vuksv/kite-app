// gpmf.js - read GoPro telemetry (GPMF, HERO5 and later) straight from an MP4 File/Blob in the
// browser (or Node).  Only the 'moov' index and the small telemetry payloads are read, never the
// video, so multi-GB clips work on a phone.  Format: github.com/gopro/gpmf-parser
//
// result = { model, firmware, durationS, startUtc (unix s of video time 0, from the GPS clock, or null),
//            cameraClock (unix s read from the MP4 header: camera clock, usually LOCAL time, or null),
//            gpsFixFrac, streams: { ACCL:{t,v,rateHz,units}, GYRO:{...}, GPS5:{...}, ... } }
//   t = seconds of video time;  v = array of sample arrays (scaled to physical units)

const td = new TextDecoder('latin1');
const str4 = (dv, o) => td.decode(new Uint8Array(dv.buffer, dv.byteOffset + o, 4));

async function readBytes(file, start, len) {
  const ab = await file.slice(start, start + len).arrayBuffer();
  return new DataView(ab);
}

// ---------------------------------------------------------------- MP4 boxes
async function findTopBox(file, type) {
  let pos = 0;
  const size = file.size;
  while (pos + 8 <= size) {
    const h = await readBytes(file, pos, 16);
    let len = h.getUint32(0), hdr = 8;
    const t = str4(h, 4);
    if (len === 1) { len = Number(h.getBigUint64(8)); hdr = 16; } else if (len === 0) len = size - pos;
    if (len < 8) break;
    if (t === type) return { start: pos, len, hdr };
    pos += len;
  }
  return null;
}

function children(dv, start, end) {
  const out = [];
  let p = start;
  while (p + 8 <= end) {
    let len = dv.getUint32(p), hdr = 8;
    const t = str4(dv, p + 4);
    if (len === 1) { len = Number(dv.getBigUint64(p + 8)); hdr = 16; } else if (len === 0) len = end - p;
    if (len < 8 || p + len > end) break;
    out.push({ type: t, start: p, body: p + hdr, end: p + len });
    p += len;
  }
  return out;
}
const child = (dv, box, type) => children(dv, box.body, box.end).find(b => b.type === type);

function telemetryTrack(moov) {
  for (const trak of children(moov, 8, moov.byteLength).filter(b => b.type === 'trak')) {
    const mdia = child(moov, trak, 'mdia'); if (!mdia) continue;
    const hdlr = child(moov, mdia, 'hdlr'); const minf = child(moov, mdia, 'minf'); const mdhd = child(moov, mdia, 'mdhd');
    if (!hdlr || !minf || !mdhd) continue;
    if (str4(moov, hdlr.body + 8) !== 'meta') continue;
    const stbl = child(moov, minf, 'stbl'); if (!stbl) continue;
    const stsd = child(moov, stbl, 'stsd'); if (!stsd || str4(moov, stsd.body + 12) !== 'gpmd') continue;
    const v = moov.getUint8(mdhd.body);
    const timescale = v === 1 ? moov.getUint32(mdhd.body + 20) : moov.getUint32(mdhd.body + 12);
    // sample durations (stts), sizes (stsz), chunk offsets (stco/co64) and sample-to-chunk (stsc)
    const stts = child(moov, stbl, 'stts'), stsz = child(moov, stbl, 'stsz'), stsc = child(moov, stbl, 'stsc');
    const stco = child(moov, stbl, 'stco') || child(moov, stbl, 'co64');
    const durs = [];
    for (let i = 0, n = moov.getUint32(stts.body + 4); i < n; i++) {
      const cnt = moov.getUint32(stts.body + 8 + i * 8), d = moov.getUint32(stts.body + 12 + i * 8);
      for (let k = 0; k < cnt; k++) durs.push(d / timescale);
    }
    const fixed = moov.getUint32(stsz.body + 4), nSamp = moov.getUint32(stsz.body + 8);
    const sizes = [];
    for (let i = 0; i < nSamp; i++) sizes.push(fixed || moov.getUint32(stsz.body + 12 + i * 4));
    const co64 = stco.type === 'co64', nChunk = moov.getUint32(stco.body + 4), chunkOff = [];
    for (let i = 0; i < nChunk; i++) chunkOff.push(co64 ? Number(moov.getBigUint64(stco.body + 8 + i * 8)) : moov.getUint32(stco.body + 8 + i * 4));
    const s2c = [];
    for (let i = 0, n = moov.getUint32(stsc.body + 4); i < n; i++) s2c.push([moov.getUint32(stsc.body + 8 + i * 12), moov.getUint32(stsc.body + 12 + i * 12)]);
    const offsets = [];
    let s = 0;
    for (let c = 0; c < nChunk && s < nSamp; c++) {
      let per = 1; for (const [first, spc] of s2c) if (c + 1 >= first) per = spc;
      let off = chunkOff[c];
      for (let k = 0; k < per && s < nSamp; k++, s++) { offsets.push(off); off += sizes[s]; }
    }
    return { timescale, durs, sizes, offsets };
  }
  return null;
}

// ---------------------------------------------------------------- GPMF KLV
const TYPES = { b: [1, 'getInt8'], B: [1, 'getUint8'], s: [2, 'getInt16'], S: [2, 'getUint16'], l: [4, 'getInt32'], L: [4, 'getUint32'],
  f: [4, 'getFloat32'], d: [8, 'getFloat64'], j: [8, 'getBigInt64'], J: [8, 'getBigUint64'], q: [4, 'q1616'] };

function parseKlv(dv, start, end, out) {
  let p = start;
  while (p + 8 <= end) {
    const key = str4(dv, p), type = String.fromCharCode(dv.getUint8(p + 4)), ssize = dv.getUint8(p + 5), rep = dv.getUint16(p + 6);
    const len = ssize * rep, body = p + 8, next = body + ((len + 3) & ~3);
    if (key === '\0\0\0\0') break;
    if (type === '\0') { const kids = []; parseKlv(dv, body, Math.min(end, body + len), kids); out.push({ key, kids }); }
    else out.push({ key, type, ssize, rep, body, len, dv });
    p = next;
  }
}

function values(e) {
  if (e.type === 'c' || e.type === 'U') return [td.decode(new Uint8Array(e.dv.buffer, e.dv.byteOffset + e.body, e.len)).replace(/\0+$/, '')];
  const t = TYPES[e.type]; if (!t) return [];
  const [bs, fn] = t, per = Math.max(1, Math.round(e.ssize / bs)), rows = [];
  for (let r = 0; r < e.rep; r++) {
    const row = [];
    for (let k = 0; k < per; k++) {
      const o = e.body + r * e.ssize + k * bs;
      row.push(fn === 'q1616' ? e.dv.getInt32(o) / 65536 : Number(e.dv[fn](o)));
    }
    rows.push(row);
  }
  return rows;
}

const parseGpsu = (s) => {   // "yymmddhhmmss.sss" UTC
  const m = /^(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)(\d\d(?:\.\d+)?)/.exec(s); if (!m) return null;
  return Date.UTC(2000 + +m[1], +m[2] - 1, +m[3], +m[4], +m[5], 0) / 1000 + parseFloat(m[6]);
};

/** Read GoPro telemetry from an MP4 File/Blob.  Throws if the file has no GPMF track. */
export async function readGoPro(file, onProgress = () => {}) {
  const moovBox = await findTopBox(file, 'moov');
  if (!moovBox) throw new Error('not an MP4 file (no moov box)');
  const moov = await readBytes(file, moovBox.start, moovBox.len);
  // camera clock at recording start (mvhd creation time; GoPro writes the camera's local clock, not UTC)
  let cameraClock = null;
  const mvhd = children(moov, 8, moov.byteLength).find(b => b.type === 'mvhd');
  if (mvhd) {
    const v = moov.getUint8(mvhd.body);
    const secs1904 = v === 1 ? Number(moov.getBigUint64(mvhd.body + 4)) : moov.getUint32(mvhd.body + 4);
    if (secs1904 > 0) cameraClock = secs1904 - 2082844800;
  }
  const tr = telemetryTrack(moov);
  if (!tr) throw new Error('no GoPro telemetry track in this file - pick the original GoPro MP4 (copies exported through the photo library are re-encoded without it)');
  const streams = {}, gpsu = [], fix = [];
  let model = '', firmware = '', t = 0;
  for (let i = 0; i < tr.sizes.length; i++) {
    const dv = await readBytes(file, tr.offsets[i], tr.sizes[i]);
    const top = []; parseKlv(dv, 0, dv.byteLength, top);
    const dur = tr.durs[i] || 1;
    for (const devc of top.filter(x => x.key === 'DEVC')) {
      const dvnm = devc.kids.find(x => x.key === 'DVNM'); if (dvnm && !model) model = values(dvnm)[0];
      for (const strm of devc.kids.filter(x => x.key === 'STRM')) {
        let scal = [1], units = '', name = '';
        for (const e of strm.kids) {
          if (e.key === 'SCAL') scal = values(e).flat();
          else if (e.key === 'SIUN' || e.key === 'UNIT') units = values(e)[0] || units;
          else if (e.key === 'STNM') name = values(e)[0];
          else if (e.key === 'GPSU') { const u = parseGpsu(values(e)[0]); if (u) gpsu.push([t, u]); }
          else if (e.key === 'GPSF') fix.push(values(e)[0][0]);
          else if (/^(ACCL|GYRO|GPS5|GRAV|CORI|IORI|MAGN)$/.test(e.key)) {
            const rows = values(e), n = rows.length;
            const st = streams[e.key] || (streams[e.key] = { t: [], v: [], units, name });
            for (let k = 0; k < n; k++) {
              st.t.push(t + (k + 0.5) * dur / n);
              st.v.push(rows[k].map((x, c) => x / (scal.length > 1 ? scal[c] ?? scal[0] : scal[0])));
            }
          }
        }
      }
    }
    const firm = top.length && top[0].kids && top[0].kids.find(x => x.key === 'FMWR'); if (firm) firmware = values(firm)[0];
    t += dur;
    if (i % 20 === 0) onProgress(i / tr.sizes.length);
  }
  for (const st of Object.values(streams)) st.rateHz = st.t.length > 1 ? (st.t.length - 1) / (st.t[st.t.length - 1] - st.t[0]) : NaN;
  // video time 0 in UTC: median of (GPS UTC - video time) over payloads with a GPS fix
  const good = gpsu.filter((_, k) => fix.length <= k || fix[k] >= 2).map(([vt, u]) => u - vt).sort((a, b) => a - b);
  const startUtc = good.length ? good[Math.floor(good.length / 2)] : null;
  onProgress(1);
  return { model, firmware, durationS: t, startUtc, cameraClock, gpsFixFrac: fix.length ? fix.filter(x => x >= 2).length / fix.length : 0, streams };
}
