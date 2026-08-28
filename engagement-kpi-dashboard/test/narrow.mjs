import { launch } from './browser.mjs';
const ROOT = new URL('../', import.meta.url).pathname;
import path from 'path'; import fs from 'fs';
const ALL = fs.readdirSync(path.resolve(ROOT, 'samples')).sort().map(f => path.resolve(ROOT, 'samples', f));
const b = await launch();
for (const w of [390, 820]) {
  const p = await b.newPage({ viewport:{width:w,height:900} });
  await p.goto('file://' + path.resolve(ROOT, 'dashboard.html'));
  await p.setInputFiles('#file', ALL);
  await p.waitForTimeout(700);
  const g = await p.evaluate(() => ({
    hscroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    body: document.body.scrollWidth, client: document.documentElement.clientWidth,
    tableScrolls: [...document.querySelectorAll('.scroll')].map(s => s.scrollWidth > s.clientWidth),
  }));
  console.log(w + 'px:', JSON.stringify(g));
  if (w === 390) await p.screenshot({ path:ROOT + 'test/shot-narrow.png', fullPage:true });
}
await b.close();
