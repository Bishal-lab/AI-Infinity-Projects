import fs from 'fs';
import vm from 'vm';
import path from 'path';
const ROOT = new URL('../', import.meta.url).pathname;

const strip = s => s.replace(/^export\s+/gm, '');
const code = ['01_xlsx.js','02_sources.js','03_kpis.js'].map(f => ROOT + 'src/' + f).map(f => strip(fs.readFileSync(f,'utf8'))).join('\n');
const ctx = { console, Blob, Response, DecompressionStream, TextDecoder, Date, Math, Number, Set, Map, JSON, isFinite, parseFloat, String, Object, Array,
  document: { createElement: () => ({ }), querySelector: () => null } };
vm.createContext(ctx);
vm.runInContext(code + '\nglobalThis.__api = { DATA, kpis, readWorkbook, toRecords, employeeRows, campaignRows, sum, N, yes, fmtPct, fmtInt, claim };', ctx);
const api = ctx.__api;
// Detection is by header signature, exactly as the page does it — so this
// harness also proves a file lands in the right slot without being named.
for (const f of fs.readdirSync(path.resolve(ROOT, 'samples')).sort()) {
  const b = fs.readFileSync(path.resolve(ROOT, 'samples', f));
  const wb = await api.readWorkbook(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
  for (const sheet of wb) {
    const { headers, records } = api.toRecords(sheet.rows);
    if (records.length) api.claim(headers, records);
  }
}

const k = api.kpis();
for (const t of k.list) {
  const v = t.value == null ? '—'
    : t.kind === 'pct' ? api.fmtPct(t.value)
    : t.kind === 'index' ? t.value.toFixed(1) + '/100'
    : api.fmtInt(t.value);
  console.log(String(t.n).padStart(2)+'. '+t.label.padEnd(30)+v.padStart(10)+'   ['+t.state+']  '+t.den);
}
console.log('\nindex parts:');
k.parts.forEach(p => console.log('   '+p.label.padEnd(22)+(p.value==null?'—':api.fmtPct(p.value))+'  w='+p.weight));
