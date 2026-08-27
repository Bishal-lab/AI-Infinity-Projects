import { chromium } from 'playwright';
const ROOT = new URL('../', import.meta.url).pathname;
import path from 'path'; import fs from 'fs';
const ALL = fs.readdirSync(path.resolve(ROOT, 'samples')).sort().map(f => path.resolve(ROOT, 'samples', f));
const b = await chromium.launch({ executablePath:'/opt/pw-browsers/chromium' });
for (const scheme of ['light','dark']) {
  const p = await b.newPage({ viewport:{width:1360,height:900}, colorScheme:scheme });
  await p.goto('file://' + path.resolve(ROOT, 'dashboard.html'));
  await p.setInputFiles('#file', ALL);
  await p.waitForTimeout(800);
  const r = await p.evaluate(() => {
    const lum = c => { const m=/rgba?\(([^)]+)\)/.exec(c); if(!m) return 1;
      const [r,g,b]=m[1].split(',').map(v=>parseFloat(v)/255);
      const l=x=>x<=.03928?x/12.92:Math.pow((x+.055)/1.055,2.4);
      return .2126*l(r)+.7152*l(g)+.0722*l(b); };
    const ratio=(a,b)=>{const[h,o]=a>b?[a,b]:[b,a];return (h+.05)/(o+.05);};
    // labels sitting ON a mark: the stacked-bar percentages
    const svgs=[...document.querySelectorAll('.card-body svg')];
    const out=[];
    svgs.forEach(s=>{
      const rects=[...s.querySelectorAll('rect')], texts=[...s.querySelectorAll('text')];
      texts.forEach(t=>{
        const tb=t.getBoundingClientRect();
        const cx=tb.left+tb.width/2, cy=tb.top+tb.height/2;
        const under=rects.filter(r=>{const rb=r.getBoundingClientRect();
          return cx>rb.left&&cx<rb.right&&cy>rb.top&&cy<rb.bottom;});
        if(!under.length) return;
        const bg=lum(getComputedStyle(under[under.length-1]).fill);
        const fg=lum(getComputedStyle(t).fill);
        out.push({txt:t.textContent, r:+ratio(fg,bg).toFixed(2)});
      });
    });
    return out.filter(o=>o.r<3.5);
  });
  console.log(scheme, 'labels on marks below 3.5:1 —', JSON.stringify(r));
  await p.close();
}
await b.close();
