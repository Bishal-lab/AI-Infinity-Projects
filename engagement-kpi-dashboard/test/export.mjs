/* The CSV export, on the local-file path.
 *
 * Saved to disk there is no `window.claude`, so the page takes the ordinary
 * Blob download. Inside a published artifact that route is inert and the save
 * goes through the `downloads` capability instead — which is why the button
 * hides itself when the capability does not answer. That branch is asserted
 * here by faking the viewer object.
 */
import { launch } from './browser.mjs';
import path from 'path'; import fs from 'fs';
const ROOT = new URL('../', import.meta.url).pathname;
const ALL = fs.readdirSync(path.resolve(ROOT, 'samples')).sort()
  .map(f => path.resolve(ROOT, 'samples', f));

const b = await launch();

// --- local file: the Blob path actually produces a file
const p = await b.newPage({ viewport:{ width:1280, height:900 } });
await p.goto('file://' + path.resolve(ROOT, 'dashboard.html'));
await p.setInputFiles('#file', ALL);
await p.waitForTimeout(700);
await p.selectOption('#f-location', 'Mumbai');
await p.waitForTimeout(300);
const [download] = await Promise.all([
  p.waitForEvent('download'),
  p.click('#f-export'),
]);
const to = path.resolve(ROOT, 'test', 'exported.csv');
await download.saveAs(to);
const csv = fs.readFileSync(to, 'utf8');
console.log('suggested filename:', download.suggestedFilename());
console.log('BOM present:', csv.charCodeAt(0) === 0xfeff);
console.log(csv.split('\r\n').slice(0, 9).join('\n'));
console.log('… rows:', csv.trim().split('\r\n').length);
fs.unlinkSync(to);
await p.close();

// --- artifact with no downloads capability: the button hides itself
const q = await b.newPage({ viewport:{ width:1280, height:900 } });
await q.addInitScript(() => { window.claude = { use: async () => null }; });
await q.goto('file://' + path.resolve(ROOT, 'dashboard.html'));
await q.setInputFiles('#file', ALL);
await q.waitForTimeout(700);
console.log('\nno capability → export button hidden:', await q.locator('#f-export').isHidden());

// --- artifact with the capability: save() is called with the right shape
const r = await b.newPage({ viewport:{ width:1280, height:900 } });
await r.addInitScript(() => {
  window.__saved = null;
  window.claude = { use: async n => n === 'downloads'
    ? { save: async req => { window.__saved = { filename:req.filename, bytes:req.data.length }; return { status:'saved' }; } }
    : null };
});
await r.goto('file://' + path.resolve(ROOT, 'dashboard.html'));
await r.setInputFiles('#file', ALL);
await r.waitForTimeout(700);
await r.click('#f-export');
await r.waitForTimeout(300);
console.log('capability present → button shown:', await r.locator('#f-export').isVisible(),
  '| save() got:', JSON.stringify(await r.evaluate(() => window.__saved)));
console.log('no error surfaced:', await r.locator('#export-note').isHidden());

await b.close();
