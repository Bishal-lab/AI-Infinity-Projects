/* ================================================================= wiring ==
 * Nothing here touches the network or storage. The parsed rows live in the
 * DATA object for as long as the tab is open and go no further — no fetch, no
 * localStorage, no cookie. That claim is on the page because anyone handling
 * employee records will ask about it before anything else.
 */

function note(results) {
  const log = $('#log');
  log.textContent = '';
  results.forEach(r => {
    const line = el('p', r.error ? 'log-err' : 'log-ok');
    line.append(dot(r.error ? 'partial' : 'ok'), document.createTextNode(
      r.error ? `${r.file} — ${r.error}` : `${r.file} → ${r.src.label}, ${fmtInt(r.n)} rows`));
    log.appendChild(line);
  });
  log.hidden = !results.length;
}

async function load(files) {
  const list = [...files].filter(f => /\.xlsx$/i.test(f.name));
  if (!list.length) {
    note([{ file:'No .xlsx among the files chosen', error:'expects Excel exports' }]);
    return;
  }
  $('#drop').classList.add('busy');
  const results = await ingest(list);
  $('#drop').classList.remove('busy');
  note(results);
  FILTER = { channel:'', location:'', path:'', from:null, to:null };
  render();
}

/* ------------------------------------------------------------- export ----
 * The ten tiles as CSV. Every chart carries its own numbers view, so this one
 * export is the thing people actually paste into a deck — and it carries the
 * period and the active filters with it, for the same reason the printed sheet
 * does: a column of figures that does not say what it was filtered by can be
 * read as the whole picture.
 */

const csvCell = v => {
  const t = v == null ? '' : String(v);
  return /[",\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
};
const csvRow = cells => cells.map(csvCell).join(',');

function kpiCsv() {
  const K = kpis();
  const active = filterSummary();
  const out = [
    csvRow(['Flight to Success — key performance indicators']),
    csvRow([$('#period').textContent]),
    csvRow([active.length ? 'Filtered by ' + active.join(' · ') : 'No filters applied']),
    csvRow(['Exported', new Date().toISOString().slice(0, 10)]),
    '',
    csvRow(['#', 'KPI', 'Value', 'Basis', 'Status', 'Reading applied']),
  ];
  K.list.forEach(k => out.push(csvRow([
    k.n, k.label,
    k.value == null ? '' : k.kind === 'pct' ? (k.value * 100).toFixed(2) + '%'
      : k.kind === 'index' ? k.value.toFixed(1) + '/100' : Math.round(k.value),
    k.den, k.state, k.note,
  ])));
  return out.join('\r\n');
}

/** The viewer's file-save capability, or null when there isn't one.
 *
 *  A published artifact's sandbox makes an <a download> inert — it would appear
 *  to work and silently do nothing — so inside the viewer the save has to go
 *  through the capability. Saved to disk and opened directly there is no
 *  `window.claude` at all, and an ordinary Blob download works unaided. */
async function saver() {
  if (typeof window.claude === 'undefined' || typeof claude.use !== 'function') return null;
  try { return await claude.use('downloads'); } catch (err) { return null; }
}

function exportNote(text) {
  const note = $('#export-note');
  note.textContent = text || '';
  note.hidden = !text;
}

async function exportKpis() {
  // A BOM, so Excel reads the ÷ and — in these cells as UTF-8 rather than
  // mojibake. Every other consumer ignores it.
  const csv = '\ufeff' + kpiCsv();
  const filename = 'flight-to-success-kpis.csv';
  exportNote('');

  const downloads = await saver();
  if (downloads) {
    try {
      await downloads.save({ filename, data:csv });
    } catch (err) {
      exportNote(err && err.code === 'declined' ? 'Save cancelled.'
        : `Could not save the file${err && err.code ? ` (${err.code})` : ''}.`);
    }
    return;
  }

  const url = URL.createObjectURL(new Blob([csv], { type:'text/csv' }));
  const a = el('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function wire() {
  const input = $('#file');
  $('#pick').addEventListener('click', () => input.click());
  input.addEventListener('change', () => { load(input.files); input.value = ''; });

  const drop = $('#drop');
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', e => load(e.dataTransfer.files));

  const set = (key, value) => { FILTER[key] = value; render(); };
  $('#f-channel').addEventListener('change', e => set('channel', e.target.value));
  $('#f-location').addEventListener('change', e => set('location', e.target.value));
  $('#f-path').addEventListener('change', e => set('path', e.target.value));

  /* A date input hands back 'YYYY-MM-DD'. Parse it as UTC, because both sides of
   * the comparison in campaignRows() already sit at UTC midnight — fromSerial()
   * returns epoch-ms midnight, and a date written as text parses the same way.
   * Reading it as local time would shift the boundary by the viewer's offset and
   * quietly drop the first or last campaign of a range. The upper bound takes
   * end-of-day so it stays inclusive if an export ever carries a time. */
  const bound = (value, endOfDay) => {
    if (!value) return null;
    const d = new Date(value + (endOfDay ? 'T23:59:59.999Z' : 'T00:00:00.000Z'));
    return isNaN(d) ? null : d;
  };
  $('#f-from').addEventListener('change', e => set('from', bound(e.target.value, false)));
  $('#f-to').addEventListener('change', e => set('to', bound(e.target.value, true)));

  // renderFilters() reflects FILTER back into every control, so clearing the
  // object is all a reset needs — the inputs follow.
  $('#f-reset').addEventListener('click', () => {
    FILTER = { channel:'', location:'', path:'', from:null, to:null };
    render();
  });

  $('#f-print').addEventListener('click', () => window.print());
  $('#f-export').addEventListener('click', exportKpis);

  // Inside the viewer, offer the download only if the capability answers.
  // Showing a button that cannot save is worse than showing none.
  if (typeof window.claude !== 'undefined') {
    saver().then(d => { if (!d) $('#f-export').hidden = true; });
  }
}

/* ------------------------------------------------------------ preload ----
 * A page written by the on-prem importer carries its rows inline, so it opens
 * already populated instead of waiting for someone to drag five files in.
 *
 * The payload is in the shape `toRecords()` already returns, and it goes
 * through the very same `claim()` a dropped file goes through. That is the
 * point: there is one ingestion path, not two, so the importer needs no
 * knowledge of which file is which and cannot disagree with the page about it.
 * Drag-and-drop still works on top — a populated page can take an extra file.
 */
function preload() {
  const payload = window.__PRELOADED__;
  if (!Array.isArray(payload) || !payload.length) return;

  const results = [];
  for (const sheet of payload) {
    const records = sheet && sheet.records;
    if (!Array.isArray(records) || !records.length) continue;
    const src = claim(sheet.headers || [], records);
    results.push(src
      ? { file:sheet.file, src, n:records.length }
      : { file:sheet.file, error:'no sheet matched a known report' });
  }
  if (!results.length) return;

  note(results);
  if (window.__IMPORTED_AT__) {
    const line = el('p', 'log-ok', `Imported ${window.__IMPORTED_AT__}`);
    $('#log').prepend(line);
  }
}

wire();
preload();
render();
