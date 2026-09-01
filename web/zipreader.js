// Copyright (C) 2026 William Wang
// Licensed under the GNU AGPL v3 or later. See LICENSE.
//
// A minimal ZIP reader, so the page has no dependencies and makes no network
// requests. Entries are read through Blob.slice and inflated with the built-in
// DecompressionStream, so a 2 GB export never enters memory — only the few
// small files we actually want.

const U = (b, o) => new DataView(b).getUint32(o, true);
const S = (b, o) => new DataView(b).getUint16(o, true);
const U64 = (b, o) => Number(new DataView(b).getBigUint64(o, true));

async function slice(file, start, end) {
  return await file.slice(start, Math.min(end, file.size)).arrayBuffer();
}

// The end-of-central-directory record sits at the tail, after a comment of
// unknown length, so it has to be found by scanning backwards for its
// signature rather than read from a fixed offset.
async function findEOCD(file) {
  const want = Math.min(file.size, 66560);          // 64 KB + max comment
  const buf = await slice(file, file.size - want, file.size);
  const dv = new DataView(buf);
  for (let i = buf.byteLength - 22; i >= 0; i--) {
    if (dv.getUint32(i, true) === 0x06054b50) {
      const base = file.size - want;
      let entries = dv.getUint16(i + 10, true);
      let cdSize = dv.getUint32(i + 12, true);
      let cdOff = dv.getUint32(i + 16, true);
      // ZIP64: the 32-bit fields saturate, and the real values live in a
      // separate record pointed at by the locator just before the EOCD.
      if (cdOff === 0xffffffff || entries === 0xffff) {
        for (let j = i - 20; j >= 0; j--) {
          if (dv.getUint32(j, true) === 0x07064b50) {
            const z64 = U64(buf, j + 8);
            const zb = await slice(file, z64, z64 + 56);
            entries = U64(zb, 32); cdSize = U64(zb, 40); cdOff = U64(zb, 48);
            break;
          }
        }
      }
      return { entries, cdSize, cdOff, base };
    }
  }
  throw new Error("Not a ZIP file (no end-of-central-directory record found).");
}

export async function readCentralDirectory(file) {
  const { entries, cdSize, cdOff } = await findEOCD(file);
  const buf = await slice(file, cdOff, cdOff + cdSize);
  const dv = new DataView(buf);
  const dec = new TextDecoder();
  const out = new Map();
  let p = 0;
  for (let n = 0; n < entries && p + 46 <= buf.byteLength; n++) {
    if (dv.getUint32(p, true) !== 0x02014b50) break;
    const method = dv.getUint16(p + 10, true);
    let comp = dv.getUint32(p + 20, true);
    let uncomp = dv.getUint32(p + 24, true);
    const nameLen = dv.getUint16(p + 28, true);
    const extraLen = dv.getUint16(p + 30, true);
    const commentLen = dv.getUint16(p + 32, true);
    let local = dv.getUint32(p + 42, true);
    const name = dec.decode(new Uint8Array(buf, p + 46, nameLen));
    if (local === 0xffffffff || uncomp === 0xffffffff || comp === 0xffffffff) {
      let e = p + 46 + nameLen;
      const endExtra = e + extraLen;
      while (e + 4 <= endExtra) {
        const id = dv.getUint16(e, true), sz = dv.getUint16(e + 2, true);
        if (id === 0x0001) {
          let q = e + 4;
          if (uncomp === 0xffffffff) { uncomp = U64(buf, q); q += 8; }
          if (comp === 0xffffffff) { comp = U64(buf, q); q += 8; }
          if (local === 0xffffffff) { local = U64(buf, q); }
          break;
        }
        e += 4 + sz;
      }
    }
    out.set(name, { name, method, comp, uncomp, local });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return out;
}

export async function readEntry(file, entry) {
  // The local header repeats the name and extra field, with its own lengths.
  const head = await slice(file, entry.local, entry.local + 30);
  const nameLen = S(head, 26), extraLen = S(head, 28);
  const start = entry.local + 30 + nameLen + extraLen;
  const blob = file.slice(start, start + entry.comp);
  if (entry.method === 0) return new Uint8Array(await blob.arrayBuffer());
  if (entry.method !== 8) throw new Error(`Unsupported compression (${entry.method}) for ${entry.name}`);
  const ds = new DecompressionStream("deflate-raw");
  const buf = await new Response(blob.stream().pipeThrough(ds)).arrayBuffer();
  return new Uint8Array(buf);
}

export async function readText(file, entry) {
  return new TextDecoder("utf-8").decode(await readEntry(file, entry));
}
