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
  $('#f-reset').addEventListener('click', () => {
    FILTER = { channel:'', location:'', path:'', from:null, to:null };
    render();
  });
}

wire();
render();
