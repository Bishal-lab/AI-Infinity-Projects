import { launch } from './browser.mjs';
const ROOT = new URL('../', import.meta.url).pathname;
import path from 'path'; import fs from 'fs';
const R = f => path.resolve(ROOT, 'samples', f);
const ALL = fs.readdirSync(path.resolve(ROOT, 'samples')).sort().map(R);

const browser = await launch();
const errors = [], reqs = [];
async function open(theme) {
  const page = await browser.newPage({ viewport:{width:1360,height:1000},
    colorScheme: theme === 'dark' ? 'dark' : 'light' });
  page.on('pageerror', e => errors.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/fonts\.g|ERR_/.test(m.text())) errors.push('CONSOLE ' + m.text()); });
  page.on('request', r => { if (!r.url().startsWith('file:') && !r.url().includes('fonts.g')) reqs.push(r.url()); });
  await page.goto('file://' + path.resolve(ROOT, 'dashboard.html'));
  return page;
}

// 1 — empty state
let page = await open('light');
console.log('1. empty state — source slots listed:', await page.locator('.src').count(),
  '| dashboard hidden:', await page.locator('#dash').isHidden());
await page.screenshot({ path:ROOT + 'test/shot-empty.png', fullPage:true });

// 2 — one file only
await page.setInputFiles('#file', [R('04_LMS_Employee_Wise_Report.xlsx')]);
await page.waitForTimeout(500);
const cards = await page.evaluate(() => [...document.querySelectorAll('.card')]
  .map(c => ({ t:c.querySelector('h3').textContent, await:!!c.querySelector('.await') })));
console.log('2. LMS only —', cards.filter(c => !c.await).length, 'live,',
  cards.filter(c => c.await).length, 'awaiting:', cards.filter(c => c.await).map(c => c.t).join(' · '));
await page.screenshot({ path:ROOT + 'test/shot-partial.png', fullPage:true });

// 3 — renamed file
fs.copyFileSync(R('05_Viva_Engage_Campaign_KPI.xlsx'), '/tmp/export (3).xlsx');
await page.reload();
await page.setInputFiles('#file', ['/tmp/export (3).xlsx']);
await page.waitForTimeout(400);
console.log('3. renamed →', (await page.locator('#log p').first().textContent()).trim());

// 4 — full set, every tile read back
await page.reload();
await page.setInputFiles('#file', ALL);
await page.waitForTimeout(700);
const tiles = await page.evaluate(() => [...document.querySelectorAll('.tile')].map(t => ({
  n:t.querySelector('.tile-n').textContent, label:t.querySelector('.tile-label').textContent,
  v:t.querySelector('.tile-value').textContent, den:t.querySelector('.tile-den').textContent,
  chip:t.querySelector('.chip')?.textContent.trim() || '' })));
console.log('4. all four files:');
tiles.forEach(t => console.log('   ' + t.n + ' ' + t.label.padEnd(30) + t.v.padStart(11) +
  '  ' + t.den + (t.chip ? '  [' + t.chip + ']' : '')));
console.log('   period:', await page.locator('#period').textContent());
await page.screenshot({ path:ROOT + 'test/shot-full.png', fullPage:true });

// 5 — working panel
await page.locator('.tile').nth(1).click();
await page.waitForTimeout(200);
console.log('5. working panel open:', !(await page.locator('#kpi-working').isHidden()),
  '|', (await page.locator('#kpi-working h4').textContent()));

// 6 — filter
await page.selectOption('#f-channel', 'Axis');
await page.waitForTimeout(400);
const f = await page.evaluate(() => [...document.querySelectorAll('.tile')]
  .map(t => t.querySelector('.tile-value').textContent));
console.log('6. channel=Axis →', f.join(' | '));
console.log('   seg-note visible:', !(await page.locator('#seg-note').isHidden()));
await page.screenshot({ path:ROOT + 'test/shot-filter.png', fullPage:true });

// 6b — date range narrows the campaign KPIs and says so
await page.selectOption('#f-channel', '');
await page.waitForTimeout(300);
const wide = await page.locator('.tile').nth(1).locator('.tile-value').textContent();
await page.fill('#f-from', '2026-08-02');
await page.fill('#f-to', '2026-08-03');
await page.waitForTimeout(400);
const narrowed = await page.locator('.tile').nth(1).locator('.tile-value').textContent();
const employeeKpi = await page.locator('.tile').nth(7).locator('.tile-value').textContent();
console.log('6b. dates 02–03 Aug — deliveries', wide, '→', narrowed,
  '| employee KPI unchanged:', employeeKpi === '48.4%',
  '| applied:', (await page.locator('#applied').textContent()).trim());
await page.screenshot({ path:ROOT + 'test/shot-dates.png', fullPage:true });
await page.click('#f-reset');
await page.waitForTimeout(300);
console.log('   after reset — deliveries', await page.locator('.tile').nth(1).locator('.tile-value').textContent(),
  '| applied hidden:', await page.locator('#applied').isHidden());

// 6c — numbers reachable without a mouse
const toggle = page.locator('.numbers').nth(1);
await toggle.focus();
await page.keyboard.press('Enter');
await page.waitForTimeout(200);
const tblId = await toggle.getAttribute('aria-controls');
const opened = await page.locator('#' + tblId).isVisible();
const firstRow = await page.locator('#' + tblId + ' tbody tr').first().allInnerTexts();
console.log('6c. keyboard toggle —', await toggle.getAttribute('aria-expanded'),
  '| table visible:', opened, '|', JSON.stringify(firstRow));
console.log('   toggles on every chart card:', await page.locator('.numbers').count());

// 7 — geometry / overflow
await page.selectOption('#f-channel', '');
await page.waitForTimeout(400);
const geo = await page.evaluate(() => ({
  hscroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  overflowing: [...document.querySelectorAll('.card')].filter(c =>
    c.scrollWidth > c.clientWidth + 1).map(c => c.querySelector('h3').textContent),
  charts: document.querySelectorAll('.card-body svg').length,
  faces: [...new Set([...document.querySelectorAll('h1,.tile-value,.lbl')]
    .map(e => getComputedStyle(e).fontFamily.split(',')[0]))],
}));
console.log('7. geometry:', JSON.stringify(geo));

// 8 — dark mode
const dark = await open('dark');
await dark.setInputFiles('#file', ALL);
await dark.waitForTimeout(700);
await dark.screenshot({ path:ROOT + 'test/shot-dark.png', fullPage:true });
const inkOnInk = await dark.evaluate(() => {
  const lum = c => { const m = /rgba?\(([^)]+)\)/.exec(c); if (!m) return 1;
    const [r,g,b] = m[1].split(',').map(v=>parseFloat(v)/255);
    const l = x => x<=.03928 ? x/12.92 : Math.pow((x+.055)/1.055,2.4);
    return .2126*l(r)+.7152*l(g)+.0722*l(b); };
  const ratio=(a,b)=>{const[h,o]=a>b?[a,b]:[b,a];return (h+.05)/(o+.05);};
  const bg = lum(getComputedStyle(document.body).backgroundColor);
  return [...document.querySelectorAll('.tile-value,.foot,.card-head h3,.ax,.cat,.lbl')]
    .map(e => ({ t:e.textContent.slice(0,22), r:+ratio(lum(getComputedStyle(e).fill && getComputedStyle(e).fill !== 'none' && e.namespaceURI.includes('svg') ? getComputedStyle(e).fill : getComputedStyle(e).color), bg).toFixed(2) }))
    .filter(x => x.r < 3);
});
console.log('8. dark mode — text below 3:1 vs page:', JSON.stringify(inkOnInk));

console.log('\nnetwork (non-font, non-file):', reqs.length ? reqs : 'none');
console.log('errors:', errors.length ? errors : 'none');
await browser.close();
