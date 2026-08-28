/* ================================================================== view ==
 * Every section renders from DATA + FILTER and nothing else, so a filter
 * change is a full re-render rather than a set of patches that can drift out
 * of step with each other. At this data size that costs a few milliseconds.
 */

function card(title, sub, extra) {
  const box = el('section', 'card' + (extra ? ' ' + extra : ''));
  const head = el('div', 'card-head');
  head.appendChild(el('h3', null, title));
  if (sub) head.appendChild(el('p', 'sub', sub));
  box.appendChild(head);
  const body = el('div', 'card-body');
  box.appendChild(body);
  return { box, head, body };
}

/** The same figures the chart draws, as a table.
 *
 *  `hover()` binds mousemove and nothing else, so a tooltip is unreachable
 *  by keyboard and invisible on touch. Every chart therefore carries the
 *  numbers it is drawn from behind a toggle — which is also the relief the
 *  palette validator requires for the light-mode series that sit below 3:1.
 */
let tableSeq = 0;
function withNumbers(c, headers, rows) {
  if (!rows.length) return c;
  const id = 'numbers-' + (++tableSeq);
  const btn = el('button', 'numbers', 'Numbers');
  btn.type = 'button';
  btn.setAttribute('aria-expanded', 'false');
  btn.setAttribute('aria-controls', id);
  const wrap = table(headers, rows);
  wrap.id = id;
  wrap.hidden = true;
  btn.addEventListener('click', () => {
    const opening = wrap.hidden;
    wrap.hidden = !opening;
    btn.setAttribute('aria-expanded', String(opening));
    btn.textContent = opening ? 'Hide numbers' : 'Numbers';
  });
  c.head.appendChild(btn);
  c.body.appendChild(wrap);
  return c;
}

/** Stage rows shared by the funnels — each stage as a share of the one above. */
const funnelRows = stages => stages.map((st, i) => [
  st.label, fmtInt(st.value),
  i ? fmtPct(rate(st.value, stages[i - 1].value)) : '—',
]);

/** What a section shows when its source has not been dropped in yet. Not a
 *  blank space and not an error — the columns it is waiting for. */
function awaiting(src) {
  const box = el('div', 'await');
  const head = el('p', 'await-h');
  head.append(dot('blocked'), document.createTextNode(`Awaiting ${src.file}`));
  box.appendChild(head);
  box.appendChild(el('p', 'await-c', src.cols));
  return box;
}

function dot(state) {
  const s = el('span', 'dot dot-' + state);
  s.setAttribute('aria-hidden', 'true');
  s.textContent = state === 'ok' ? '●' : state === 'partial' ? '▲' : '○';
  return s;
}

const groupBy = (rows, key) => {
  const m = new Map();
  rows.forEach(r => {
    const k = r[key] == null ? '—' : String(r[key]);
    (m.get(k) || m.set(k, []).get(k)).push(r);
  });
  return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
};

/* ------------------------------------------------------------- intake ---- */

function renderSources() {
  const list = $('#sources');
  list.textContent = '';
  SOURCES.forEach(src => {
    const rows = (DATA[src.id] || []).length;
    const li = el('li', rows ? 'src on' : 'src');
    li.append(dot(rows ? 'ok' : 'blocked'));
    const body = el('div');
    body.appendChild(el('strong', null, src.label));
    body.appendChild(el('span', 'src-file', rows
      ? `${fmtInt(rows)} row${rows === 1 ? '' : 's'} loaded`
      : `expects ${src.cols.split(' · ').slice(0, 4).join(', ')}…`));
    li.appendChild(body);
    list.appendChild(li);
  });
  const n = SOURCES.filter(s => (DATA[s.id] || []).length).length;
  $('#intake').classList.toggle('loaded', n > 0);
  $('#count').textContent = n ? `${n} of ${SOURCES.length} reports loaded` : '';
}

/* ------------------------------------------------------------ filters ---- */

function options(sel, values, label) {
  sel.textContent = '';
  sel.appendChild(el('option', null, label));
  sel.firstChild.value = '';
  values.forEach(v => { const o = el('option', null, v); o.value = v; sel.appendChild(o); });
}

/** Every campaign date loaded, unfiltered — the bounds the date picker offers,
 *  so it can never propose a range with no data in it. */
function campaignDates() {
  return SOURCES.filter(src => src.grain === 'campaign')
    .flatMap(src => DATA[src.id] || [])
    .map(r => asDate(r.Campaign_Date))
    .filter(d => d && !isNaN(d))
    .sort((a, b) => a - b);
}

const isoDay = d => d instanceof Date && !isNaN(d) ? d.toISOString().slice(0, 10) : '';

/** The active filters in words. Shown on screen and always printed: a sheet of
 *  filtered numbers that does not say it was filtered is the one way this
 *  document can actively mislead. */
function filterSummary() {
  const out = [];
  if (FILTER.channel) out.push('Channel: ' + FILTER.channel);
  if (FILTER.location) out.push('Location: ' + FILTER.location);
  if (FILTER.path) out.push('Learning path: ' + FILTER.path);
  if (FILTER.from || FILTER.to) {
    out.push('Dates: ' + (FILTER.from ? fmtDay(FILTER.from) : 'earliest')
      + ' to ' + (FILTER.to ? fmtDay(FILTER.to) : 'latest'));
  }
  return out;
}

function renderFilters() {
  const people = [...(DATA.webinar || []), ...(DATA.lms || [])];
  const uniq = key => [...new Set(people.map(r => r[key]).filter(Boolean))].sort();
  options($('#f-channel'), uniq('Channel'), 'All channels');
  options($('#f-location'), uniq('Location'), 'All locations');
  options($('#f-path'), [...new Set((DATA.lms || []).map(r => r.Learning_Path).filter(Boolean))].sort(),
    'All learning paths');
  $('#f-channel').value = FILTER.channel;
  $('#f-location').value = FILTER.location;
  $('#f-path').value = FILTER.path;
  const dates = campaignDates();
  const from = $('#f-from'), to = $('#f-to');
  for (const input of [from, to]) {
    input.min = dates.length ? isoDay(dates[0]) : '';
    input.max = dates.length ? isoDay(dates[dates.length - 1]) : '';
    input.disabled = !dates.length;
  }
  from.value = isoDay(FILTER.from);
  to.value = isoDay(FILTER.to);

  $('#filters').hidden = !people.length && !dates.length;
  $('#seg-note').hidden = segmentable('campaign');

  const active = filterSummary();
  $('#applied').textContent = active.length ? 'Filtered by ' + active.join(' · ') : '';
  $('#applied').hidden = !active.length;
}

/* --------------------------------------------------------------- band 1 -- */

function renderKpis(K) {
  const grid = $('#kpi-grid');
  grid.textContent = '';
  let open = null;
  K.list.forEach(k => {
    const tile = el('button', 'tile');
    tile.type = 'button';
    tile.setAttribute('aria-expanded', 'false');
    tile.appendChild(el('span', 'tile-n', String(k.n).padStart(2, '0')));
    tile.appendChild(el('span', 'tile-label', k.label));
    const v = k.value == null ? '—'
      : k.kind === 'pct' ? fmtPct(k.value)
      : k.kind === 'index' ? fmtNum(k.value, 0)
      : fmtInt(k.value);
    const value = el('span', 'tile-value', v);
    if (k.kind === 'index' && k.value != null) value.appendChild(el('small', null, ' / 100'));
    tile.appendChild(value);
    tile.appendChild(el('span', 'tile-den', k.den));
    if (k.state !== 'ok') {
      const chip = el('span', 'chip chip-' + k.state);
      chip.append(dot(k.state), document.createTextNode(
        k.state === 'blocked' ? 'Awaiting file' : 'Partial'));
      tile.appendChild(chip);
    }
    tile.addEventListener('click', () => {
      const same = open === tile;
      grid.querySelectorAll('.tile').forEach(t => {
        t.classList.remove('open'); t.setAttribute('aria-expanded', 'false');
      });
      const panel = $('#kpi-working');
      if (same) { open = null; panel.hidden = true; return; }
      open = tile;
      tile.classList.add('open');
      tile.setAttribute('aria-expanded', 'true');
      panel.textContent = '';
      panel.appendChild(el('h4', null, `${String(k.n).padStart(2, '0')} · ${k.label}`));
      panel.appendChild(el('p', 'working-den', k.den));
      panel.appendChild(el('p', null, k.note));
      if (k.seg) panel.appendChild(el('p', 'working-seg', k.seg));
      panel.hidden = false;
    });
    grid.appendChild(tile);
  });
  $('#kpi-working').hidden = true;
}

/* --------------------------------------------------------------- band 2 -- */

function renderComms(K) {
  const wrap = $('#band-comms');
  wrap.textContent = '';
  const email = campaignRows('email');
  const viva = campaignRows('viva');

  const c1 = card('Email funnel',
    'Sent to clicked, summed across every campaign in range.');
  if (email.length) {
    const stages = [
      { label:'Sent', value:sum(email, 'Sent') },
      { label:'Delivered', value:sum(email, 'Delivered') },
      { label:'Opened', value:sum(email, 'Opened') },
      { label:'Clicked', value:sum(email, 'Clicked') },
    ];
    c1.body.appendChild(funnel(stages));
    c1.body.appendChild(el('p', 'foot',
      `Each percentage is of the stage above it. ${fmtPct(rate(sum(email, 'Clicked'), sum(email, 'Opened')))} of the people who opened went on to click.`));
    withNumbers(c1, ['Stage', 'Messages', 'Of previous'], funnelRows(stages));
  } else c1.body.appendChild(awaiting(SOURCES[0]));
  wrap.appendChild(c1.box);

  const c2 = card('Email response by campaign',
    'Open and click rates computed per campaign from raw counts.');
  if (email.length) {
    const xs = email.map(r => ({ label:fmtDay(asDate(r.Campaign_Date)) }));
    c2.body.appendChild(lines(xs, [
      { name:'Open rate', values:email.map(r => rate(N(r.Opened), N(r.Delivered))) },
      { name:'Click rate', values:email.map(r => rate(N(r.Clicked), N(r.Delivered))) },
    ]));
    c2.body.appendChild(legend(['Open rate', 'Click rate']));
    withNumbers(c2, ['Campaign', 'Delivered', 'Opened', 'Open %', 'Clicked', 'Click %'],
      email.map(r => [r.Campaign, fmtInt(N(r.Delivered)), fmtInt(N(r.Opened)),
        fmtPct(rate(N(r.Opened), N(r.Delivered))), fmtInt(N(r.Clicked)),
        fmtPct(rate(N(r.Clicked), N(r.Delivered)))]));
  } else c2.body.appendChild(awaiting(SOURCES[0]));
  wrap.appendChild(c2.box);

  const c3 = card('WhatsApp', 'The second communication channel.');
  if (has('whatsapp')) {
    const wa = campaignRows('whatsapp');
    const stages = [
      { label:'Sent', value:sum(wa, 'Sent') },
      { label:'Delivered', value:sum(wa, 'Delivered') },
      { label:'Read', value:sum(wa, 'Read') },
      { label:'Clicked', value:sum(wa, 'Clicked') },
    ];
    c3.body.appendChild(funnel(stages));
    c3.body.appendChild(el('p', 'foot',
      `Read rates on WhatsApp run far above e-mail open rates, so the two channels are reported separately rather than blended into one "engagement" figure.`));
    withNumbers(c3, ['Stage', 'Messages', 'Of previous'], funnelRows(stages));
  } else {
    c3.body.appendChild(awaiting(SOURCES[1]));
    c3.body.appendChild(el('p', 'foot',
      'Two KPIs are held open for it. Drop the file in and they fill — the column names above are what the page looks for, and it will accept the file under any name.'));
  }
  wrap.appendChild(c3.box);

  const c4 = card('Viva Engage activation',
    'Active users as a share of the members targeted that day.');
  if (viva.length) {
    const xs = viva.map(r => ({ label:fmtDay(asDate(r.Campaign_Date)) }));
    c4.body.appendChild(lines(xs, [
      { name:'Active', values:viva.map(r => rate(N(r.Active_Users), N(r.Community_Members))) },
    ]));
    c4.body.appendChild(el('p', 'foot',
      `Community_Members moves between ${fmtInt(Math.min(...viva.map(r => N(r.Community_Members))))} and ${fmtInt(Math.max(...viva.map(r => N(r.Community_Members))))} across consecutive days, which a community size does not do. Read the rate as daily activation of the set targeted that day.`));
    withNumbers(c4, ['Date', 'Members targeted', 'Active users', 'Active %'],
      viva.map(r => [fmtDay(asDate(r.Campaign_Date)), fmtInt(N(r.Community_Members)),
        fmtInt(N(r.Active_Users)), fmtPct(rate(N(r.Active_Users), N(r.Community_Members)))]));
  } else c4.body.appendChild(awaiting(SOURCES[4]));
  wrap.appendChild(c4.box);

  const c5 = card('What Viva engagement is made of',
    'Every interaction recorded across the campaigns in range.', 'wide');
  if (viva.length) {
    const kinds = ['Likes', 'Comments', 'Shares', 'Posts', 'Polls'];
    const parts = kinds.map(k => ({ label:k, value:sum(viva, k) }));
    c5.body.appendChild(stackedBar(parts));
    c5.body.appendChild(legend(kinds, ORDINAL));
    const total = parts.reduce((a, p) => a + p.value, 0);
    c5.body.appendChild(el('p', 'foot',
      `${fmtInt(total)} interactions in total. ${fmtPct(rate(parts[0].value, total), 0)} are likes — the lowest-effort act available, which is why KPI 5 counts clicks rather than mixing these in.`));
    withNumbers(c5, ['Interaction', 'Count', 'Share'],
      parts.map(p => [p.label, fmtInt(p.value), fmtPct(rate(p.value, total))]));
  } else c5.body.appendChild(awaiting(SOURCES[4]));
  wrap.appendChild(c5.box);
}

/* --------------------------------------------------------------- band 3 -- */

const BANDS = [
  { label:'<50', lo:-Infinity, hi:50 }, { label:'50–59', lo:50, hi:60 },
  { label:'60–69', lo:60, hi:70 }, { label:'70–79', lo:70, hi:80 },
  { label:'80–89', lo:80, hi:90 }, { label:'90+', lo:90, hi:Infinity },
];

function renderLearning(K) {
  const wrap = $('#band-learn');
  wrap.textContent = '';
  const web = employeeRows('webinar');
  const lms = employeeRows('lms');

  const c1 = card('Webinar funnel',
    'Employees in the export, through to a certificate issued.');
  if (web.length) {
    const stages = [
      { label:'Employees', value:population() },
      { label:'Registered', value:web.filter(r => yes(r.Registered)).length },
      { label:'Attended', value:web.filter(r => yes(r.Attended)).length },
      { label:'Certified', value:web.filter(r => yes(r.Certificate)).length },
    ];
    c1.body.appendChild(funnel(stages));
    const mins = web.filter(r => yes(r.Attended)).map(r => N(r.Duration_Min));
    c1.body.appendChild(el('p', 'foot',
      `Attendees stayed ${fmtNum(mins.reduce((a, b) => a + b, 0) / (mins.length || 1), 0)} minutes on average.`));
    withNumbers(c1, ['Stage', 'People', 'Of previous'], funnelRows(stages));
  } else c1.body.appendChild(awaiting(SOURCES[2]));
  wrap.appendChild(c1.box);

  const c2 = card('Registered and attended, by channel',
    'Where the drop-off between signing up and showing up actually happens.');
  if (web.length) {
    const groups = groupBy(web, 'Channel');
    const reg = groups.map(g => g[1].filter(r => yes(r.Registered)).length);
    const att = groups.map(g => g[1].filter(r => yes(r.Attended)).length);
    c2.body.appendChild(groupedBars(groups.map(g => g[0]), [
      { name:'Registered', values:reg }, { name:'Attended', values:att },
    ]));
    c2.body.appendChild(legend(['Registered', 'Attended']));
    withNumbers(c2, ['Channel', 'Registered', 'Attended', 'Show-up %'],
      groups.map((g, i) => [g[0], fmtInt(reg[i]), fmtInt(att[i]), fmtPct(rate(att[i], reg[i]))]));
  } else c2.body.appendChild(awaiting(SOURCES[2]));
  wrap.appendChild(c2.box);

  const c3 = card('Learning completion by path',
    'Modules completed as a share of modules assigned.');
  if (lms.length) {
    const groups = groupBy(lms, 'Learning_Path');
    const items = groups.map(([name, rows]) => ({
      label:name,
      value:rate(sum(rows, 'Modules_Completed'), sum(rows, 'Modules_Assigned')) || 0,
      rows,
    })).sort((a, b) => b.value - a.value);
    c3.body.appendChild(hBars(items, {
      fmt:v => fmtPct(v, 0),
      note:it => `${fmtInt(sum(it.rows, 'Modules_Completed'))} of ${fmtInt(sum(it.rows, 'Modules_Assigned'))} modules · ${it.rows.length} employees`,
    }));
    const done = lms.filter(r => N(r.Modules_Completed) >= N(r.Modules_Assigned) && N(r.Modules_Assigned) > 0).length;
    const none = lms.filter(r => N(r.Modules_Completed) === 0).length;
    c3.body.appendChild(el('p', 'foot',
      `Measured in modules, not people: ${fmtInt(done)} of ${fmtInt(lms.length)} employees have finished everything assigned to them, and ${fmtInt(none)} have started nothing.`));
    withNumbers(c3, ['Learning path', 'Employees', 'Assigned', 'Completed', 'Completion %'],
      items.map(it => [it.label, fmtInt(it.rows.length), fmtInt(sum(it.rows, 'Modules_Assigned')),
        fmtInt(sum(it.rows, 'Modules_Completed')), fmtPct(it.value)]));
  } else c3.body.appendChild(awaiting(SOURCES[3]));
  wrap.appendChild(c3.box);

  const c4 = card('Assessment scores',
    'Employees by score band — the shape behind the average.');
  if (lms.length) {
    const scores = lms.map(r => N(r['Assessment_Score_%'])).filter(v => v > 0);
    const items = BANDS.map(b => ({
      label:b.label, value:scores.filter(s => s >= b.lo && s < b.hi).length,
    }));
    c4.body.appendChild(bandBars(items, { note:() => 'employees' }));
    c4.body.appendChild(el('p', 'foot',
      `${fmtInt(scores.length)} employees have a recorded score, averaging ${fmtNum(scores.reduce((a, b) => a + b, 0) / (scores.length || 1))}%.`));
    withNumbers(c4, ['Score band', 'Employees', 'Share'],
      items.map(it => [it.label, fmtInt(it.value), fmtPct(rate(it.value, scores.length))]));
  } else c4.body.appendChild(awaiting(SOURCES[3]));
  wrap.appendChild(c4.box);
}

/* --------------------------------------------------------------- band 4 -- */

function renderCross(K) {
  const wrap = $('#band-cross');
  wrap.textContent = '';
  const web = employeeRows('webinar');
  const lms = employeeRows('lms');

  const c1 = card('Every channel, side by side',
    'The two employee reports joined on Employee_ID — the reason to consolidate at all.');
  if (web.length || lms.length) {
    const channels = [...new Set([...web, ...lms].map(r => r.Channel).filter(Boolean))].sort();
    const rows = channels.map(ch => {
      const w = web.filter(r => r.Channel === ch);
      const l = lms.filter(r => r.Channel === ch);
      const reg = w.filter(r => yes(r.Registered)).length;
      const att = w.filter(r => yes(r.Attended)).length;
      const scores = l.map(r => N(r['Assessment_Score_%'])).filter(v => v > 0);
      return [ch,
        fmtInt(new Set([...w, ...l].map(r => r.Employee_ID)).size),
        fmtPct(rate(reg, w.length), 0),
        fmtPct(rate(att, reg), 0),
        fmtPct(rate(sum(l, 'Modules_Completed'), sum(l, 'Modules_Assigned')), 0),
        fmtNum(scores.reduce((a, b) => a + b, 0) / (scores.length || 1), 0) + '%',
        fmtInt(w.filter(r => yes(r.Certificate)).length),
      ];
    });
    c1.body.appendChild(table(
      ['Channel', 'People', 'Reg %', 'Att %', 'Modules %', 'Score', 'Certs'],
      rows));
    c1.body.appendChild(el('p', 'foot',
      'Registered is of that channel’s employees; attended is of those who registered. This table is also the accessible reading of every chart above it.'));
  } else c1.body.appendChild(awaiting(SOURCES[2]));
  wrap.appendChild(c1.box);

  const c2 = card('How the index is built',
    'Four components, weighted. Change your mind about the weights and the number moves — which is the point of showing them.');
  const idx = K.list.find(k => k.n === 10);
  const hero = el('div', 'hero');
  hero.appendChild(el('span', 'hero-v', idx.value == null ? '—' : fmtNum(idx.value, 0)));
  hero.appendChild(el('span', 'hero-u', '/ 100'));
  c2.body.appendChild(hero);
  const points = p => p.value == null ? null
    : p.weight * p.value * 100 / (K.weightSum || 1);
  c2.body.appendChild(hBars(K.parts.map(p => ({
    label:`${p.label} · ${Math.round(p.weight * 100)}%`,
    value:p.value == null ? 0 : p.value,
    p,
  })), { fmt:v => fmtPct(v, 0),
    note:it => it.p.value == null ? 'no data — weight redistributed'
      : `contributes ${fmtNum(points(it.p), 1)} points` }));
  c2.body.appendChild(el('p', 'foot',
    K.live.length === K.parts.length
      ? 'Certification is measured against attendees, not the whole population — a certificate cannot be earned by someone who never attended.'
      : `${K.parts.length - K.live.length} component(s) have no data; the remaining weights were renormalised rather than counting the gap as zero.`));
  withNumbers(c2, ['Component', 'Weight', 'Value', 'Points of 100'],
    K.parts.map(p => [p.label, fmtPct(p.weight, 0),
      p.value == null ? '—' : fmtPct(p.value),
      p.value == null ? 'excluded' : fmtNum(points(p), 1)]));
  wrap.appendChild(c2.box);
}

/* ------------------------------------------------------------ assembly --- */

function period() {
  const dates = [...campaignRows('email'), ...campaignRows('viva'), ...campaignRows('whatsapp')]
    .map(r => asDate(r.Campaign_Date)).filter(d => d && !isNaN(d)).sort((a, b) => a - b);
  if (!dates.length) return '';
  const one = dates[0].getTime() === dates[dates.length - 1].getTime();
  return one ? fmtDay(dates[0]) + ' ' + dates[0].getFullYear()
    : `${fmtDay(dates[0])} – ${fmtDay(dates[dates.length - 1])} ${dates[dates.length - 1].getFullYear()}`;
}

function render() {
  renderSources();
  const any = SOURCES.some(s => (DATA[s.id] || []).length);
  $('#dash').hidden = !any;
  if (!any) return;
  renderFilters();
  const K = kpis();
  renderKpis(K);
  renderComms(K);
  renderLearning(K);
  renderCross(K);
  const p = period();
  $('#period').textContent = p ? `Campaign period ${p}` : 'Employee reports only — no campaign dates in range';
}
