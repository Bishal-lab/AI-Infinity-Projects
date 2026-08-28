/* ================================================================= charts ==
 * Hand-rolled SVG. Marks are thin, data-ends are rounded and anchored to the
 * baseline, fills are separated by a 2px surface gap, grid is recessive, and
 * every mark carries a hover tooltip. Series colours come from the tokens, in
 * fixed slot order — never cycled, never reassigned when a filter changes the
 * series count.
 */
const SERIES = ['var(--s1)','var(--s2)','var(--s3)','var(--s4)','var(--s5)','var(--s6)'];
const ORDINAL = ['var(--o1)','var(--o2)','var(--o3)','var(--o4)','var(--o5)'];
const SVGNS = 'http://www.w3.org/2000/svg';

const tip = () => $('#tip');
function showTip(evt, html) {
  const t = tip();
  t.innerHTML = html;
  t.style.opacity = '1';
  const pad = 14, w = t.offsetWidth, h = t.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY - h - 8;
  if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
  if (y < 8) y = evt.clientY + pad;
  t.style.left = x + 'px'; t.style.top = y + 'px';
}
const hideTip = () => { tip().style.opacity = '0'; };

function svg(w, h) {
  const s = document.createElementNS(SVGNS, 'svg');
  s.setAttribute('viewBox', `0 0 ${w} ${h}`);
  s.setAttribute('role', 'img');
  return s;
}
function node(parent, name, attrs, text) {
  const n = document.createElementNS(SVGNS, name);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (text != null) n.textContent = text;
  parent.appendChild(n);
  return n;
}
/** Relative luminance of a resolved CSS colour, for picking readable label ink. */
function luminance(colour) {
  const m = /rgba?\(([^)]+)\)/.exec(colour);
  if (!m) return 1;
  const [r, g, b] = m[1].split(',').map(v => parseFloat(v) / 255);
  const lin = c => c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

const ratio = (a, b) => {
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
};

/** Put a label on a mark in whichever ink actually survives against it.
 *
 *  Measured, not guessed: a luminance threshold picked dark ink for a mid-tone
 *  blue in one theme and light ink for the same hex in the other. Comparing the
 *  real contrast ratio against both candidates gets it right for any step of
 *  any ramp, in any theme. Must run after both nodes are in the document — the
 *  fill is a var() that only resolves once the element inherits the theme.
 */
function contrastLabel(text, mark) {
  const probe = getComputedStyle(document.body);
  const markL = luminance(getComputedStyle(mark).fill);
  const inkL = luminance(probe.getPropertyValue('color'));
  const surfaceL = luminance(probe.getPropertyValue('background-color'));
  text.style.fill = ratio(markL, inkL) >= ratio(markL, surfaceL)
    ? 'var(--ink)' : 'var(--surface)';
}

function hover(mark, html) {
  mark.style.cursor = 'default';
  mark.addEventListener('mousemove', e => showTip(e, html));
  mark.addEventListener('mouseleave', hideTip);
}

/** Horizontal bars — one series, category labels at left, value at the end.
 *  The form for magnitude across named categories: length is the easiest
 *  channel to compare, and long names stay readable. */
function hBars(items, { fmt = fmtInt, colour = 'var(--s1)', labelW = 152, note } = {}) {
  const rowH = 26, gap = 7, w = 640;
  const h = items.length * (rowH + gap) + 8;
  const max = Math.max(...items.map(i => i.value), 1);
  const barW = w - labelW - 66;
  const s = svg(w, h);
  items.forEach((it, i) => {
    const y = i * (rowH + gap);
    node(s, 'text', { x:labelW - 10, y:y + rowH / 2 + 4, 'text-anchor':'end', class:'cat' },
      it.label.length > 30 ? it.label.slice(0, 29) + '…' : it.label);
    node(s, 'rect', { x:labelW, y:y + 5, width:barW, height:rowH - 10,
      rx:3, fill:'var(--grid)' });
    const len = Math.max(2, (it.value / max) * barW);
    const bar = node(s, 'rect', { x:labelW, y:y + 5, width:len, height:rowH - 10,
      rx:3, fill:it.colour || colour });
    hover(bar, `${it.label}<br><b>${fmt(it.value)}</b>${note ? '<br>' + note(it) : ''}`);
    node(s, 'text', { x:labelW + len + 8, y:y + rowH / 2 + 4, class:'lbl' }, fmt(it.value));
  });
  return s;
}

/** Grouped bars — two or three series across a shared category axis. Used
 *  where the comparison *between* series inside each category is the point. */
function groupedBars(cats, series, { fmt = fmtInt, axisFmt } = {}) {
  const w = 640, h = 235, padL = 46, padB = 40, padT = 10;
  const plotW = w - padL - 12, plotH = h - padB - padT;
  const max = Math.max(1, ...series.flatMap(s => s.values));
  const s = svg(w, h);
  for (let t = 0; t <= 4; t++) {
    const y = padT + plotH - (t / 4) * plotH;
    node(s, 'line', { x1:padL, y1:y, x2:padL + plotW, y2:y, class:'gridline' });
    node(s, 'text', { x:padL - 8, y:y + 3.5, 'text-anchor':'end', class:'ax' },
      (axisFmt || fmtInt)(max * t / 4));
  }
  const slot = plotW / cats.length;
  const barW = Math.min(30, (slot - 12) / series.length - 2);
  cats.forEach((cat, i) => {
    const x0 = padL + i * slot + (slot - (barW + 2) * series.length) / 2;
    series.forEach((ser, j) => {
      const v = ser.values[i] || 0;
      const bh = Math.max(1.5, (v / max) * plotH);
      // 2px gap between adjacent fills so the pair never reads as one mark.
      const bar = node(s, 'rect', { x:x0 + j * (barW + 2), y:padT + plotH - bh,
        width:barW, height:bh, rx:3, fill:SERIES[j] });
      hover(bar, `${cat}<br>${ser.name} <b>${fmt(v)}</b>`);
    });
    const text = node(s, 'text', { x:padL + i * slot + slot / 2, y:h - padB + 15,
      'text-anchor':'middle', class:'ax' });
    const words = String(cat).split(' ');
    let line = '', lines = [];
    for (const word of words) {
      if ((line + ' ' + word).trim().length > 14) { lines.push(line.trim()); line = word; }
      else line += ' ' + word;
    }
    lines.push(line.trim());
    lines.slice(0, 2).forEach((l, k) => node(text, 'tspan',
      { x:padL + i * slot + slot / 2, dy:k ? 11 : 0 }, l));
  });
  return s;
}

/** One 100% stacked bar — the shape of a split, where the parts sum to a
 *  meaningful whole and the reader wants proportion, not magnitude. */
function stackedBar(parts, { palette = ORDINAL, fmt = fmtInt } = {}) {
  const w = 640, h = 46, total = parts.reduce((a, p) => a + p.value, 0) || 1;
  const s = svg(w, h);
  const pending = [];
  let x = 0;
  parts.forEach((p, i) => {
    const seg = (p.value / total) * w;
    const bar = node(s, 'rect', { x, y:6, width:Math.max(0, seg - 2), height:26,
      rx:3, fill:p.colour || palette[i % palette.length] });
    hover(bar, `${p.label}<br><b>${fmt(p.value)}</b> · ${fmtPct(p.value / total)}`);
    if (seg > 54) {
      const label = node(s, 'text', { x:x + seg / 2 - 1, y:23,
        'text-anchor':'middle', class:'lbl' }, fmtPct(p.value / total, 0));
      pending.push([label, bar]);
    }
    x += seg;
  });
  // The ordinal ramp reverses between themes, so which segment needs light ink
  // is not fixed — measure the resolved fill once the SVG is in the document.
  requestAnimationFrame(() => pending.forEach(([t, r]) => contrastLabel(t, r)));
  return s;
}

/** Multi-series line over time, with a shared crosshair and end labels so
 *  identity never rests on colour alone. */
function lines(xs, series, { fmt = fmtPct } = {}) {
  const w = 640, h = 250, padL = 46, padB = 34, padT = 10;
  const padR = series.length <= 4 ? 96 : 14;
  const plotW = w - padL - padR, plotH = h - padB - padT;
  const max = Math.max(0.0001, ...series.flatMap(s => s.values.filter(v => v != null)));
  const s = svg(w, h);
  for (let t = 0; t <= 4; t++) {
    const y = padT + plotH - (t / 4) * plotH;
    node(s, 'line', { x1:padL, y1:y, x2:padL + plotW, y2:y, class:'gridline' });
    node(s, 'text', { x:padL - 8, y:y + 3.5, 'text-anchor':'end', class:'ax' },
      fmt(max * t / 4, 0));
  }
  const X = i => padL + (xs.length === 1 ? plotW / 2 : (i / (xs.length - 1)) * plotW);
  const Y = v => padT + plotH - (v / max) * plotH;

  xs.forEach((x, i) => {
    if (i % Math.ceil(xs.length / 6) && i !== xs.length - 1) return;
    node(s, 'text', { x:X(i), y:h - padB + 15, 'text-anchor':'middle', class:'ax' }, x.label);
  });

  series.forEach((ser, j) => {
    const pts = ser.values.map((v, i) => v == null ? null : [X(i), Y(v)]).filter(Boolean);
    if (!pts.length) return;
    node(s, 'path', { d:'M' + pts.map(p => p.join(' ')).join(' L '),
      fill:'none', stroke:SERIES[j], 'stroke-width':2,
      'stroke-linejoin':'round', 'stroke-linecap':'round' });
    const last = pts[pts.length - 1];
    // 2px surface ring so crossing endpoints stay separable.
    node(s, 'circle', { cx:last[0], cy:last[1], r:3.5, fill:SERIES[j],
      stroke:'var(--surface)', 'stroke-width':2 });
    // Direct-label only up to four series; beyond that the end labels stack on
    // top of each other and the legend carries identity instead.
    if (series.length <= 4) {
      const endLabel = node(s, 'text', { x:last[0] + 8, y:last[1] + 3.5, class:'cat' },
        ser.name.length > 15 ? ser.name.slice(0, 14) + '…' : ser.name);
      endLabel.style.fill = SERIES[j];
    }
  });

  // One crosshair band per x, rather than a hit target per point.
  const band = plotW / Math.max(1, xs.length - 1);
  xs.forEach((x, i) => {
    const r = node(s, 'rect', { x:X(i) - band / 2, y:padT, width:band, height:plotH,
      fill:'transparent' });
    const line = node(s, 'line', { x1:X(i), y1:padT, x2:X(i), y2:padT + plotH,
      stroke:'var(--rule-2)', 'stroke-width':1, opacity:0 });
    r.addEventListener('mousemove', e => {
      line.setAttribute('opacity', 1);
      showTip(e, `<b>${x.label}</b><br>` + series.map((ser, j) =>
        `<span style="color:${SERIES[j]}">■</span> ${ser.name} <b>${fmt(ser.values[i])}</b>`)
        .join('<br>'));
    });
    r.addEventListener('mouseleave', () => { line.setAttribute('opacity', 0); hideTip(); });
  });
  return s;
}

/** Funnel — an ordinal ramp, because the stages are ordered and each is a
 *  subset of the one before. */
function funnel(stages) {
  const w = 640, rowH = 38, s = svg(w, stages.length * rowH + 4);
  const top = stages[0].value || 1;
  stages.forEach((st, i) => {
    const y = i * rowH;
    const len = Math.max(3, (st.value / top) * (w - 190));
    const bar = node(s, 'rect', { x:150, y:y + 6, width:len, height:rowH - 14,
      rx:3, fill:ORDINAL[Math.min(i, ORDINAL.length - 1)] });
    hover(bar, `${st.label}<br><b>${fmtInt(st.value)}</b>` +
      (i ? `<br>${fmtPct(st.value / stages[i - 1].value)} of ${stages[i - 1].label.toLowerCase()}` : ''));
    node(s, 'text', { x:140, y:y + rowH / 2 + 3, 'text-anchor':'end', class:'cat' }, st.label);
    node(s, 'text', { x:150 + len + 8, y:y + rowH / 2 + 3, class:'lbl' },
      fmtInt(st.value) + (i ? `  ${fmtPct(st.value / stages[i - 1].value, 0)}` : ''));
  });
  return s;
}

function legend(names, palette = SERIES) {
  const box = el('div', 'legend');
  names.forEach((n, i) => {
    const item = el('span');
    const swatch = el('i');
    swatch.style.background = palette[i % palette.length];
    item.append(swatch, document.createTextNode(n));
    box.appendChild(item);
  });
  return box;
}

function table(headers, rowsIn) {
  const wrap = el('div', 'scroll');
  const t = el('table', 'tbl');
  const thead = el('thead'), tr = el('tr');
  headers.forEach(h => tr.appendChild(el('th', null, h)));
  thead.appendChild(tr); t.appendChild(thead);
  const tb = el('tbody');
  rowsIn.forEach(r => {
    const row = el('tr');
    r.forEach((c, i) => row.appendChild(el('td', i ? 'n' : null, c)));
    tb.appendChild(row);
  });
  t.appendChild(tb); wrap.appendChild(t);
  return wrap;
}

/** Vertical bars over an ordered band axis — a distribution, where the order
 *  of the categories is itself information and reversing it would be wrong. */
function bandBars(items, { colour = 'var(--s1)', fmt = fmtInt, note } = {}) {
  const w = 640, h = 200, padL = 34, padB = 34, padT = 12;
  const plotW = w - padL - 10, plotH = h - padB - padT;
  const max = Math.max(1, ...items.map(i => i.value));
  const s = svg(w, h);
  for (let t = 0; t <= 4; t++) {
    const y = padT + plotH - (t / 4) * plotH;
    node(s, 'line', { x1:padL, y1:y, x2:padL + plotW, y2:y, class:'gridline' });
    node(s, 'text', { x:padL - 8, y:y + 3.5, 'text-anchor':'end', class:'ax' }, fmtInt(max * t / 4));
  }
  const slot = plotW / items.length, barW = Math.min(56, slot - 8);
  items.forEach((it, i) => {
    const x = padL + i * slot + (slot - barW) / 2;
    const bh = Math.max(1.5, (it.value / max) * plotH);
    const bar = node(s, 'rect', { x, y:padT + plotH - bh, width:barW, height:bh,
      rx:3, fill:it.colour || colour });
    hover(bar, `${it.label}<br><b>${fmt(it.value)}</b>${note ? '<br>' + note(it) : ''}`);
    node(s, 'text', { x:x + barW / 2, y:padT + plotH - bh - 6, 'text-anchor':'middle',
      class:'lbl' }, fmt(it.value));
    node(s, 'text', { x:x + barW / 2, y:h - padB + 15, 'text-anchor':'middle',
      class:'ax' }, it.label);
  });
  return s;
}
