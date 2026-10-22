// fit.js - minimal, dependency-free FIT decoder for KiteLog files (browser + Node ES module).
// Decodes file_id, field_description, record, lap, session and event messages,
// including developer fields (which carry the packed accelerometer stream).
// Also unwraps Garmin Connect "Export Original" ZIPs and .fit.gz files.

export const FIT_EPOCH = 631065600;          // 1989-12-31T00:00:00Z
const SEMI = 180 / 2 ** 31;

const BASE = {                               // base type id -> [size, reader, invalid]
  0x00: [1, 'u8'], 0x01: [1, 'i8'], 0x02: [1, 'u8'], 0x83: [2, 'i16'], 0x84: [2, 'u16'],
  0x85: [4, 'i32'], 0x86: [4, 'u32'], 0x07: [1, 'str'], 0x88: [4, 'f32'], 0x89: [8, 'f64'],
  0x0A: [1, 'u8z'], 0x8B: [2, 'u16z'], 0x8C: [4, 'u32z'], 0x0D: [1, 'u8'], 0x8E: [8, 'i64'],
  0x8F: [8, 'u64'], 0x90: [8, 'u64z'],
};

function readScalar(dv, pos, kind, little) {
  switch (kind) {
    case 'u8': { const v = dv.getUint8(pos); return v === 0xFF ? null : v; }
    case 'u8z': { const v = dv.getUint8(pos); return v === 0 ? null : v; }
    case 'i8': { const v = dv.getInt8(pos); return v === 0x7F ? null : v; }
    case 'u16': { const v = dv.getUint16(pos, little); return v === 0xFFFF ? null : v; }
    case 'u16z': { const v = dv.getUint16(pos, little); return v === 0 ? null : v; }
    case 'i16': { const v = dv.getInt16(pos, little); return v === 0x7FFF ? null : v; }
    case 'u32': { const v = dv.getUint32(pos, little); return v === 0xFFFFFFFF ? null : v; }
    case 'u32z': { const v = dv.getUint32(pos, little); return v === 0 ? null : v; }
    case 'i32': { const v = dv.getInt32(pos, little); return v === 0x7FFFFFFF ? null : v; }
    case 'f32': { const v = dv.getFloat32(pos, little); return Number.isFinite(v) ? v : null; }
    case 'f64': { const v = dv.getFloat64(pos, little); return Number.isFinite(v) ? v : null; }
    case 'i64': { const v = dv.getBigInt64(pos, little); return v === 0x7FFFFFFFFFFFFFFFn ? null : Number(v); }
    case 'u64': { const v = dv.getBigUint64(pos, little); return v === 0xFFFFFFFFFFFFFFFFn ? null : Number(v); }
    case 'u64z': { const v = dv.getBigUint64(pos, little); return v === 0n ? null : Number(v); }
    default: return null;
  }
}

function readField(dv, pos, size, baseType, little) {
  const spec = BASE[baseType & 0xFF] || BASE[baseType & 0x9F] || [1, 'u8'];
  const [bsize, kind] = spec;
  if (kind === 'str') {
    let s = '';
    for (let i = 0; i < size; i++) { const c = dv.getUint8(pos + i); if (c === 0) break; s += String.fromCharCode(c); }
    return s;
  }
  const n = Math.max(1, Math.floor(size / bsize));
  if (n === 1) return readScalar(dv, pos, kind, little);
  const arr = [];
  for (let i = 0; i < n; i++) arr.push(readScalar(dv, pos + i * bsize, kind, little));
  return arr;
}

export function decodeFit(buffer) {
  const dv = new DataView(buffer);
  const headerSize = dv.getUint8(0);
  const dataSize = dv.getUint32(4, true);
  const sig = String.fromCharCode(dv.getUint8(8), dv.getUint8(9), dv.getUint8(10), dv.getUint8(11));
  if (sig !== '.FIT') throw new Error('Not a FIT file (missing .FIT signature)');
  const end = Math.min(buffer.byteLength, headerSize + dataSize);
  let pos = headerSize;
  const defs = {};
  const devDefs = {};                                  // "devIdx:fieldNum" -> {name, units, baseType}
  const out = { fileId: null, records: [], laps: [], sessions: [], events: [], fieldDescriptions: [], errors: [], nMessages: 0 };
  let lastTs = null;

  const readData = (def) => {
    const fields = {};
    const dev = {};
    for (const f of def.fields) { fields[f.num] = readField(dv, pos, f.size, f.baseType, def.little); pos += f.size; }
    for (const f of def.devFields) {
      const key = f.devIdx + ':' + f.num;
      const d = devDefs[key];
      const bt = d ? d.baseType : 0x0D;
      dev[d ? d.name : 'dev_' + key] = readField(dv, pos, f.size, bt, def.little);
      pos += f.size;
    }
    return { fields, dev };
  };

  const dispatch = (globalNum, m) => {
    out.nMessages++;
    const F = m.fields;
    const ts = F[253] != null ? F[253] + FIT_EPOCH : null;
    switch (globalNum) {
      case 0: out.fileId = { type: F[0], manufacturer: F[1], product: F[2], serial: F[3], timeCreated: F[4] != null ? F[4] + FIT_EPOCH : null }; break;
      case 206: {
        const d = { devIdx: F[0], num: F[1], baseType: F[2], name: F[3] || ('dev_' + F[0] + ':' + F[1]), units: F[8] || '', nativeMesgNum: F[14] };
        devDefs[d.devIdx + ':' + d.num] = d; out.fieldDescriptions.push(d); break;
      }
      case 20: {
        const r = {
          ts, lat: F[0] != null ? F[0] * SEMI : null, lon: F[1] != null ? F[1] * SEMI : null,
          speed: F[73] != null ? F[73] / 1000 : (F[6] != null ? F[6] / 1000 : null),
          alt: F[78] != null ? F[78] / 5 - 500 : (F[2] != null ? F[2] / 5 - 500 : null),
          hr: F[3] ?? null, distance: F[5] != null ? F[5] / 100 : null, dev: m.dev,
        };
        out.records.push(r); break;
      }
      case 19: out.laps.push({ ts, startTime: F[2] != null ? F[2] + FIT_EPOCH : null, elapsed: F[7] != null ? F[7] / 1000 : null, dev: m.dev }); break;
      case 18: out.sessions.push({ ts, startTime: F[2] != null ? F[2] + FIT_EPOCH : null, sport: F[5], subSport: F[6],
        elapsed: F[7] != null ? F[7] / 1000 : null, timer: F[8] != null ? F[8] / 1000 : null, distance: F[9] != null ? F[9] / 100 : null,
        maxSpeed: F[125] != null ? F[125] / 1000 : (F[15] != null ? F[15] / 1000 : null), dev: m.dev }); break;
      case 21: out.events.push({ ts, event: F[0], eventType: F[1] }); break;
      default: break;
    }
  };

  try {
    while (pos < end) {
      const hdr = dv.getUint8(pos++);
      if (hdr & 0x80) {                                  // compressed timestamp header
        const def = defs[(hdr >> 5) & 0x3];
        if (!def) throw new Error('data message before definition (compressed header)');
        const m = readData(def);
        if (lastTs != null) {
          let t = (lastTs & ~0x1F) + (hdr & 0x1F);
          if (t < lastTs) t += 0x20;
          lastTs = t; m.fields[253] = t;
        }
        dispatch(def.globalNum, m);
      } else if (hdr & 0x40) {                           // definition message
        const local = hdr & 0x0F, hasDev = !!(hdr & 0x20);
        pos += 1;
        const little = dv.getUint8(pos++) === 0;
        const globalNum = dv.getUint16(pos, little); pos += 2;
        const nf = dv.getUint8(pos++);
        const fields = [];
        for (let i = 0; i < nf; i++) { fields.push({ num: dv.getUint8(pos), size: dv.getUint8(pos + 1), baseType: dv.getUint8(pos + 2) }); pos += 3; }
        const devFields = [];
        if (hasDev) {
          const nd = dv.getUint8(pos++);
          for (let i = 0; i < nd; i++) { devFields.push({ num: dv.getUint8(pos), size: dv.getUint8(pos + 1), devIdx: dv.getUint8(pos + 2) }); pos += 3; }
        }
        defs[local] = { globalNum, little, fields, devFields };
      } else {                                           // normal data message
        const def = defs[hdr & 0x0F];
        if (!def) throw new Error('data message before definition (local type ' + (hdr & 0x0F) + ')');
        const m = readData(def);
        if (m.fields[253] != null) lastTs = m.fields[253];
        dispatch(def.globalNum, m);
      }
    }
  } catch (e) {
    out.errors.push(String(e && e.message || e));
  }
  return out;
}

// ---------------------------------------------------------------- containers
function findFitInZip(buffer) {
  const dv = new DataView(buffer);
  const n = buffer.byteLength;
  let eocd = -1;
  for (let i = n - 22; i >= Math.max(0, n - 70000); i--) { if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; } }
  if (eocd < 0) throw new Error('ZIP: end of central directory not found');
  const count = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  const entries = [];
  for (let k = 0; k < count; k++) {
    if (dv.getUint32(p, true) !== 0x02014b50) break;
    const method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true), usize = dv.getUint32(p + 24, true);
    const nlen = dv.getUint16(p + 28, true), elen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true);
    const off = dv.getUint32(p + 42, true);
    let name = ''; for (let i = 0; i < nlen; i++) name += String.fromCharCode(dv.getUint8(p + 46 + i));
    entries.push({ name, method, csize, usize, off });
    p += 46 + nlen + elen + clen;
  }
  const fit = entries.find(e => /\.fit$/i.test(e.name)) || entries[0];
  if (!fit) throw new Error('ZIP: no .fit entry');
  const lnlen = dv.getUint16(fit.off + 26, true), lelen = dv.getUint16(fit.off + 28, true);
  const start = fit.off + 30 + lnlen + lelen;
  return { ...fit, data: buffer.slice(start, start + fit.csize) };
}

async function inflate(data, format) {
  if (typeof DecompressionStream === 'undefined') throw new Error('This browser cannot unzip; extract the .fit file first');
  const ds = new DecompressionStream(format);
  const stream = new Blob([data]).stream().pipeThrough(ds);
  return await new Response(stream).arrayBuffer();
}

/** Accepts a .fit, a Garmin Connect "Export Original" .zip, or a .fit.gz and returns the raw FIT ArrayBuffer. */
export async function unwrapFit(buffer, name = '') {
  const dv = new DataView(buffer);
  if (buffer.byteLength >= 12) {
    const sig = String.fromCharCode(dv.getUint8(8), dv.getUint8(9), dv.getUint8(10), dv.getUint8(11));
    if (sig === '.FIT') return buffer;
  }
  if (buffer.byteLength >= 4 && dv.getUint32(0, true) === 0x04034b50) {
    const e = findFitInZip(buffer);
    if (e.method === 0) return e.data;
    if (e.method === 8) return await inflate(e.data, 'deflate-raw');
    throw new Error('ZIP: unsupported compression method ' + e.method);
  }
  if (buffer.byteLength >= 2 && dv.getUint8(0) === 0x1f && dv.getUint8(1) === 0x8b) return await inflate(buffer, 'gzip');
  throw new Error('Unrecognised file ' + name + ' (expected .fit, .zip or .fit.gz)');
}
