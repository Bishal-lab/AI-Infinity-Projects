/* ============================================================== sources ==
 * Recognised by column signature, never by filename. `02_WhatsApp` has not
 * been supplied yet — its slot is defined so the moment the file arrives the
 * two blocked KPIs light up with no code change.
 *
 * The list itself lives in config/sources.json, because the on-prem importer
 * needs the same signatures to decide what it can use. build.sh substitutes it
 * for the marker below, so there is one definition rather than two that can
 * disagree about what makes a file the LMS export.
 */
const SOURCES = /*__SOURCES__*/[];

/* The four decisions from the plan, made explicit here so the page can state
 * which reading it took beside every number that depends on one. */
const RULES = {
  reachLabel: 'Total Deliveries',        // A — not "reach": Delivered counts messages
  engagementNumerator: 'clicks',         // B — clicks only, one unit; Viva keeps its own tile
  vivaDenominator: 'Community_Members',  // C — as given, but labelled for what it behaves like
  indexWeights: { lms:0.40, attendance:0.30, assessment:0.20, certification:0.10 },  // D
};

const DATA = {};
let FILTER = { channel:'', location:'', path:'', from:null, to:null };

const $ = s => document.querySelector(s);
const el = (t, cls, txt) => { const n = document.createElement(t);
  if (cls) n.className = cls; if (txt != null) n.textContent = txt; return n; };

const N = v => { const n = typeof v === 'number' ? v : parseFloat(v); return isFinite(n) ? n : 0; };
const fmtInt = v => (v == null || !isFinite(v)) ? '—' : Math.round(v).toLocaleString('en-IN');
const fmtPct = (v, d = 1) => (v == null || !isFinite(v)) ? '—' : (v * 100).toFixed(d) + '%';
const fmtNum = (v, d = 1) => (v == null || !isFinite(v)) ? '—' : v.toFixed(d);
const rate = (a, b) => b ? a / b : null;
const asDate = v => v instanceof Date ? v : (v ? new Date(v) : null);
const fmtDay = d => d instanceof Date && !isNaN(d)
  ? d.toLocaleDateString('en-GB', { day:'2-digit', month:'short' }) : '—';
const yes = v => String(v).trim().toLowerCase() === 'yes';

function claim(headers, records) {
  const have = new Set(headers);
  for (const src of [...SOURCES].sort((a, b) => b.must.length - a.must.length)) {
    if (src.must.every(c => have.has(c))) {
      DATA[src.id] = (DATA[src.id] || []).concat(records);
      return src;
    }
  }
  return null;
}

async function ingest(files) {
  const out = [];
  for (const f of files) {
    try {
      const sheets = await readWorkbook(await f.arrayBuffer());
      let hit = 0;
      for (const sheet of sheets) {
        const { headers, records } = toRecords(sheet.rows);
        if (!records.length) continue;
        const src = claim(headers, records);
        if (src) { hit++; out.push({ file:f.name, src, n:records.length }); }
      }
      if (!hit) out.push({ file:f.name, error:'no sheet matched a known report' });
    } catch (err) { out.push({ file:f.name, error:err.message }); }
  }
  return out;
}

/* --------------------------------------------------------------- filters --
 * Channel, Location and Learning_Path exist only on the two employee reports.
 * The campaign reports are already aggregated and carry no such columns, so a
 * channel filter genuinely cannot narrow them. Rather than let a filter appear
 * to work while silently doing nothing, `segmentable()` reports that, and the
 * communication tiles say so on the page.
 */
const has = id => (DATA[id] || []).length > 0;
const peopleFiltered = () =>
  !!(FILTER.channel || FILTER.location || FILTER.path);

function segmentable(grain) {
  return grain === 'employee' || !peopleFiltered();
}

function employeeRows(id) {
  let rows = DATA[id] || [];
  if (FILTER.channel) rows = rows.filter(r => r.Channel === FILTER.channel);
  if (FILTER.location) rows = rows.filter(r => r.Location === FILTER.location);
  if (FILTER.path) {
    const lms = DATA.lms || [];
    const ok = new Set(lms.filter(r => r.Learning_Path === FILTER.path)
      .map(r => r.Employee_ID));
    rows = rows.filter(r => ok.has(r.Employee_ID));
  }
  return rows;
}

function campaignRows(id) {
  let rows = DATA[id] || [];
  if (FILTER.from || FILTER.to) {
    rows = rows.filter(r => {
      const d = asDate(r.Campaign_Date);
      if (!d || isNaN(d)) return true;
      if (FILTER.from && d < FILTER.from) return false;
      if (FILTER.to && d > FILTER.to) return false;
      return true;
    });
  }
  return rows;
}
const sum = (rows, key) => rows.reduce((a, r) => a + N(r[key]), 0);
