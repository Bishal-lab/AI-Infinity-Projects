/* Minimal .xlsx reader — no library, no network.
 *
 * An .xlsx is a ZIP of XML. The browser can open both natively:
 * DecompressionStream('deflate-raw') inflates the entries, and the worksheet
 * XML is regular enough to scan with a tokeniser. (DOMParser would also work,
 * but building a DOM for a 7,000-row sheet costs far more than reading the
 * handful of tag shapes that actually occur.)
 *
 * Identical code runs in Node 18+ and in the browser, which is how it gets
 * tested against real generated files before it ever reaches a page.
 */

const td = new TextDecoder();

/* ------------------------------------------------------------------ ZIP --- */

async function inflateRaw(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(
    new DecompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/** Entries of a ZIP, read from the central directory backwards from the EOCD. */
async function unzip(buffer) {
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);

  // The end-of-central-directory record sits in the last 64KB, after a
  // variable-length comment, so scan back for its signature.
  let eocd = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65558); i--) {
    if (view.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('not a zip file (no end-of-central-directory record)');

  let count = view.getUint16(eocd + 10, true);
  let offset = view.getUint32(eocd + 16, true);

  // ZIP64: the 32-bit fields saturate and the real values live in a separate
  // record. Large exports genuinely reach this.
  if (offset === 0xffffffff || count === 0xffff) {
    for (let i = eocd - 20; i >= 0; i--) {
      if (view.getUint32(i, true) === 0x07064b50) {
        const z64 = Number(view.getBigUint64(i + 8, true));
        count = Number(view.getBigUint64(z64 + 32, true));
        offset = Number(view.getBigUint64(z64 + 48, true));
        break;
      }
    }
  }

  const entries = new Map();
  let p = offset;
  for (let i = 0; i < count; i++) {
    if (view.getUint32(p, true) !== 0x02014b50) break;
    const method = view.getUint16(p + 10, true);
    const compressedSize = view.getUint32(p + 20, true);
    const nameLen = view.getUint16(p + 28, true);
    const extraLen = view.getUint16(p + 30, true);
    const commentLen = view.getUint16(p + 32, true);
    const localOffset = view.getUint32(p + 42, true);
    const name = td.decode(bytes.subarray(p + 46, p + 46 + nameLen));
    entries.set(name, { method, compressedSize, localOffset });
    p += 46 + nameLen + extraLen + commentLen;
  }

  const files = new Map();
  for (const [name, meta] of entries) {
    // The local header repeats the name and extra fields, at its own lengths.
    const lh = meta.localOffset;
    if (view.getUint32(lh, true) !== 0x04034b50) continue;
    const start = lh + 30 + view.getUint16(lh + 26, true) + view.getUint16(lh + 28, true);
    const raw = bytes.subarray(start, start + meta.compressedSize);
    files.set(name, meta.method === 0 ? raw : await inflateRaw(raw));
  }
  return files;
}

/* ------------------------------------------------------------------ XML --- */

const ENTITIES = { lt: '<', gt: '>', amp: '&', quot: '"', apos: "'" };

function decodeXml(text) {
  if (text.indexOf('&') === -1) return text;
  return text.replace(/&(#x?[0-9a-fA-F]+|[a-z]+);/g, (whole, code) => {
    if (code[0] === '#') {
      const n = code[1] === 'x' ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10);
      return Number.isFinite(n) ? String.fromCodePoint(n) : whole;
    }
    return ENTITIES[code] ?? whole;
  });
}

/** All <t> text inside a chunk, concatenated — a shared string may be split
 *  across several runs when part of it is styled differently. */
function textOf(chunk) {
  let out = '';
  const re = /<t(?:\s[^>]*)?>([\s\S]*?)<\/t>|<t\s[^>]*\/>/g;
  let m;
  while ((m = re.exec(chunk))) out += decodeXml(m[1] ?? '');
  return out;
}

function sharedStrings(files) {
  const raw = files.get('xl/sharedStrings.xml');
  if (!raw) return [];
  const xml = td.decode(raw);
  const out = [];
  const re = /<si(?:\s[^>]*)?>([\s\S]*?)<\/si>|<si\s*\/>/g;
  let m;
  while ((m = re.exec(xml))) out.push(m[1] === undefined ? '' : textOf(m[1]));
  return out;
}

/* Which style indexes are dates — needed because a date is just a number
 * wearing a date number-format. */
function dateStyles(files) {
  const raw = files.get('xl/styles.xml');
  const isDate = new Set();
  if (!raw) return isDate;
  const xml = td.decode(raw);

  // Built-in numFmtIds that mean a date or time.
  const BUILTIN = new Set([14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47]);
  const custom = new Set();
  const fmtRe = /<numFmt[^>]*numFmtId="(\d+)"[^>]*formatCode="([^"]*)"/g;
  let m;
  while ((m = fmtRe.exec(xml))) {
    const code = decodeXml(m[2]).replace(/\[[^\]]*\]/g, '').replace(/"[^"]*"/g, '');
    if (/[dmyhs]/i.test(code) && /[dmy]/i.test(code)) custom.add(Number(m[1]));
  }

  const cellXfs = xml.match(/<cellXfs[\s\S]*?<\/cellXfs>/);
  if (!cellXfs) return isDate;
  const xfRe = /<xf[^>]*numFmtId="(\d+)"[^>]*>|<xf[^>]*numFmtId="(\d+)"[^>]*\/>/g;
  let index = 0;
  while ((m = xfRe.exec(cellXfs[0]))) {
    const id = Number(m[1] ?? m[2]);
    if (BUILTIN.has(id) || custom.has(id)) isDate.add(index);
    index++;
  }
  return isDate;
}

/** Excel serial -> Date. Epoch is 1899-12-30, which absorbs the 1900
 *  leap-year bug Excel deliberately carries for Lotus compatibility. */
function fromSerial(serial) {
  return new Date(Math.round((serial - 25569) * 86400000));
}

const COL_RE = /^([A-Z]+)/;
function colIndex(ref) {
  const letters = COL_RE.exec(ref)?.[1] ?? 'A';
  let n = 0;
  for (let i = 0; i < letters.length; i++) n = n * 26 + (letters.charCodeAt(i) - 64);
  return n - 1;
}

/** One worksheet as an array of arrays. */
function readSheet(xml, strings, isDateStyle) {
  const rows = [];
  const rowRe = /<row[^>]*\br="(\d+)"[^>]*>([\s\S]*?)<\/row>|<row[^>]*\/>/g;
  const cellRe = /<c\s([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g;
  let rowMatch;
  while ((rowMatch = rowRe.exec(xml))) {
    if (rowMatch[2] === undefined) continue;
    const cells = [];
    let cellMatch;
    cellRe.lastIndex = 0;
    while ((cellMatch = cellRe.exec(rowMatch[2]))) {
      const attrs = cellMatch[1];
      const body = cellMatch[2] ?? '';
      const ref = /\br="([A-Z]+\d+)"/.exec(attrs)?.[1] ?? '';
      const type = /\bt="([^"]+)"/.exec(attrs)?.[1] ?? 'n';
      const style = Number(/\bs="(\d+)"/.exec(attrs)?.[1] ?? -1);
      const at = ref ? colIndex(ref) : cells.length;

      let value = null;
      if (type === 's') {
        const i = Number(/<v>([^<]*)<\/v>/.exec(body)?.[1]);
        value = strings[i] ?? '';
      } else if (type === 'inlineStr') {
        value = textOf(body);
      } else if (type === 'str') {
        value = decodeXml(/<v>([\s\S]*?)<\/v>/.exec(body)?.[1] ?? '');
      } else if (type === 'b') {
        value = /<v>1<\/v>/.test(body);
      } else if (type === 'e') {
        value = null;                       // #REF!, #DIV/0! — treat as empty
      } else {
        const raw = /<v>([^<]*)<\/v>/.exec(body)?.[1];
        if (raw === undefined || raw === '') {
          // A formula cell openpyxl wrote has no cached <v> at all.
          value = null;
        } else {
          const n = Number(raw);
          value = Number.isFinite(n) ? (isDateStyle.has(style) ? fromSerial(n) : n) : raw;
        }
      }
      while (cells.length < at) cells.push(null);
      cells[at] = value;
    }
    rows.push(cells);
  }
  return rows;
}

/** Every sheet of a workbook: [{ name, rows }] */
export async function readWorkbook(buffer) {
  const files = await unzip(buffer);
  const strings = sharedStrings(files);
  const isDateStyle = dateStyles(files);

  // workbook.xml gives sheet names and r:id; the rels file maps r:id -> path.
  const wbXml = td.decode(files.get('xl/workbook.xml') ?? new Uint8Array());
  const relsXml = td.decode(files.get('xl/_rels/workbook.xml.rels') ?? new Uint8Array());
  const rels = new Map();
  const relRe = /<Relationship[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"/g;
  let m;
  while ((m = relRe.exec(relsXml))) {
    let target = m[2].replace(/^\/?xl\//, '').replace(/^\.\//, '');
    rels.set(m[1], `xl/${target}`);
  }

  const sheets = [];
  const sheetRe = /<sheet[^>]*name="([^"]*)"[^>]*r:id="([^"]+)"|<sheet[^>]*r:id="([^"]+)"[^>]*name="([^"]*)"/g;
  let order = 0;
  while ((m = sheetRe.exec(wbXml))) {
    const name = decodeXml(m[1] ?? m[4]);
    const rid = m[2] ?? m[3];
    const path = rels.get(rid) ?? `xl/worksheets/sheet${++order}.xml`;
    const raw = files.get(path);
    if (!raw) continue;
    sheets.push({ name, rows: readSheet(td.decode(raw), strings, isDateStyle) });
  }
  return sheets;
}

/** Rows -> objects keyed by the header row, with blank rows dropped. */
export function toRecords(rows) {
  if (!rows.length) return { headers: [], records: [] };
  const headers = (rows[0] ?? []).map(h => (h == null ? '' : String(h).trim()));
  const records = [];
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    if (!row || row.every(v => v === null || v === '')) continue;
    const record = {};
    for (let c = 0; c < headers.length; c++) if (headers[c]) record[headers[c]] = row[c] ?? null;
    records.push(record);
  }
  return { headers, records };
}
