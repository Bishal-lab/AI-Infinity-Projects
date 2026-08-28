/* The generated page must agree with the dragged one, tile for tile.
 *
 * The importer embeds rows and nothing else — every KPI is computed by the page
 * either way. This is the test that proves it: if a number here ever differs
 * from test/gold.mjs, the two ingestion paths have stopped being one path.
 */
import { chromium } from 'playwright';
import path from 'path';
import fs from 'fs';
const ROOT = new URL('../', import.meta.url).pathname;

const outDir = path.resolve(ROOT, 'importer', 'out');
const generated = fs.readdirSync(outDir).filter(f => f.endsWith('.html')).sort().pop();
if (!generated) { console.error('no generated page — run importer/run.sh first'); process.exit(1); }

const b = await chromium.launch({ executablePath:'/opt/pw-browsers/chromium' });
const errors = [], reqs = [];
const page = await b.newPage({ viewport:{ width:1360, height:900 } });
page.on('pageerror', e => errors.push(e.message));
page.on('console', m => { if (m.type() === 'error' && !/fonts\.g|ERR_/.test(m.text())) errors.push(m.text()); });
page.on('request', r => { if (!r.url().startsWith('file:') && !r.url().includes('fonts.g')) reqs.push(r.url()); });

await page.goto('file://' + path.join(outDir, generated));
await page.waitForTimeout(700);

console.log('opened', generated, '— no dragging');
console.log('dashboard visible without any file input:', await page.locator('#dash').isVisible());
console.log('period:', (await page.locator('#period').textContent()).trim());

const tiles = await page.evaluate(() => [...document.querySelectorAll('.tile')].map(t => ({
  n: t.querySelector('.tile-n').textContent,
  label: t.querySelector('.tile-label').textContent,
  v: t.querySelector('.tile-value').textContent,
  den: t.querySelector('.tile-den').textContent })));
tiles.forEach(t => console.log('  ' + t.n + ' ' + t.label.padEnd(30) + t.v.padStart(11) + '  ' + t.den));

// The gold figures, hand-reconciled against the source workbooks.
const EXPECT = ['5','71,823','11.1%','23.4%','14.5%','88.3%','79.2%','48.4%','30.0%','64 / 100'];
const got = tiles.map(t => t.v);
const same = EXPECT.every((v, i) => got[i] === v);
console.log('\nmatches the dragged path exactly:', same);
if (!same) console.log('  expected', JSON.stringify(EXPECT), '\n  got     ', JSON.stringify(got));

// Dates must survive the JSON round trip — they are ISO text, not Date objects.
await page.fill('#f-from', '2026-08-02');
await page.fill('#f-to', '2026-08-03');
await page.waitForTimeout(400);
const narrowed = await page.locator('.tile').nth(1).locator('.tile-value').textContent();
console.log('date filter 02–03 Aug →', narrowed, '(drag path gives 30,473):', narrowed === '30,473');

console.log('non-font requests:', reqs.length ? reqs : 'none');
console.log('errors:', errors.length ? errors : 'none');
await b.close();
process.exit(same && narrowed === '30,473' && !errors.length && !reqs.length ? 0 : 1);
