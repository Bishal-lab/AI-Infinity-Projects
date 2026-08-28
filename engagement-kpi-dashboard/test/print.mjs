/* The print layout, checked in BOTH colour schemes.
 *
 * The dark palette is declared under `:root:not([data-theme="light"])`, which
 * outranks a bare `:root`. If the print override ever loses that specificity
 * race the page prints white-on-white or black-on-black, and nobody notices
 * until it comes out of a printer — so this asserts the resolved colours
 * rather than the presence of the stylesheet.
 */
import { chromium } from 'playwright';
import path from 'path';
import fs from 'fs';
const ROOT = new URL('../', import.meta.url).pathname;
const ALL = fs.readdirSync(path.resolve(ROOT, 'samples')).sort()
  .map(f => path.resolve(ROOT, 'samples', f));

const browser = await chromium.launch({ executablePath:'/opt/pw-browsers/chromium' });
let bad = 0;

for (const scheme of ['light', 'dark']) {
  const page = await browser.newPage({ viewport:{ width:1100, height:900 }, colorScheme:scheme });
  await page.goto('file://' + path.resolve(ROOT, 'dashboard.html'));
  await page.setInputFiles('#file', ALL);
  await page.waitForTimeout(700);
  await page.selectOption('#f-channel', 'Axis');
  await page.waitForTimeout(300);
  await page.emulateMedia({ media:'print' });
  await page.waitForTimeout(200);

  const r = await page.evaluate(() => {
    const seen = e => e && getComputedStyle(e).display !== 'none';
    const cs = getComputedStyle(document.body);
    return {
      bg: cs.backgroundColor,
      ink: getComputedStyle(document.querySelector('h1')).color,
      intake: seen(document.querySelector('#intake')),
      filters: seen(document.querySelector('.filters')),
      toggles: seen(document.querySelector('.numbers')),
      appliedShown: seen(document.querySelector('#applied')),
      applied: document.querySelector('#applied').textContent.trim(),
      tiles: document.querySelectorAll('.tile').length,
      cards: document.querySelectorAll('.card').length,
      avoid: [...document.querySelectorAll('.card')]
        .every(c => getComputedStyle(c).breakInside === 'avoid'),
    };
  });

  const white = r.bg === 'rgb(255, 255, 255)';
  const black = r.ink === 'rgb(0, 0, 0)';
  const ok = white && black && !r.intake && !r.filters && !r.toggles
    && r.appliedShown && r.avoid;
  if (!ok) bad++;
  console.log(`${scheme.padEnd(5)} → paper ${r.bg}, ink ${r.ink}`);
  console.log(`        controls hidden: intake ${!r.intake}, filters ${!r.filters}, toggles ${!r.toggles}`);
  console.log(`        filter state printed: ${r.appliedShown} — "${r.applied}"`);
  console.log(`        ${r.tiles} tiles, ${r.cards} cards, break-inside avoid on all: ${r.avoid}`);
  console.log(`        ${ok ? 'PASS' : 'FAIL'}`);

  await page.pdf({ path:ROOT + `test/print-${scheme}.pdf`, format:'A4', printBackground:true });
  await page.close();
}

await browser.close();
console.log(bad ? `\n${bad} scheme(s) FAILED` : '\nboth schemes print correctly');
process.exit(bad ? 1 : 0);
