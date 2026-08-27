/* ================================================================== kpis ==
 * Every KPI returns { value, num, den, state, note } rather than a bare
 * number. The denominator travels with the figure because most arguments
 * about a dashboard are arguments about a denominator, and a tile that can
 * show its own working ends them. `state` drives the chip on the tile:
 *   ok        — computed from data that is present
 *   partial   — computed, but a contributing source is missing
 *   blocked   — cannot be computed at all
 */

const OK = 'ok', PARTIAL = 'partial', BLOCKED = 'blocked';

const yesRows = (rows, key) => rows.filter(r => yes(r[key]));

/** Distinct employees across the two people-level reports, after filtering.
 *  This is the population every learning rate is measured against, and the
 *  page says so — 60 employees is what was exported, not necessarily the
 *  whole target audience. */
function population() {
  const ids = new Set();
  for (const id of ['webinar', 'lms']) employeeRows(id).forEach(r => ids.add(r.Employee_ID));
  return ids.size;
}

function kpis() {
  const email = campaignRows('email');
  const wa = campaignRows('whatsapp');
  const viva = campaignRows('viva');
  const web = employeeRows('webinar');
  const lms = employeeRows('lms');

  const waMissing = !has('whatsapp');
  const segNote = segmentable('campaign') ? null
    : 'Channel and location are only on the employee reports — the campaign '
    + 'exports are pre-aggregated, so this figure is unfiltered.';

  /* 1 — campaign count. Distinct names, because the same campaign appears in
   *     both the email and the Viva export and must not be counted twice. */
  const names = new Set();
  [...email, ...wa, ...viva].forEach(r => r.Campaign && names.add(String(r.Campaign).trim()));

  /* 2 — deliveries, deliberately not called reach: Delivered counts messages,
   *     and five campaigns to the same list is five deliveries per person. */
  const emailDelivered = sum(email, 'Delivered');
  const waDelivered = sum(wa, 'Delivered');

  const emailClicked = sum(email, 'Clicked');
  const waClicked = sum(wa, 'Clicked');

  /* 5 — one rate, one unit. Viva's likes and comments are interactions but
   *     they are not clicks and have no Delivered to sit over, so they stay
   *     in KPI 9. The mixed reading is reported beside it, not instead. */
  const vivaEngagements = sum(viva, 'Total_Engagements');
  const mixed = rate(emailClicked + waClicked + vivaEngagements, emailDelivered + waDelivered);

  const registered = yesRows(web, 'Registered');
  const attended = yesRows(web, 'Attended');
  const certified = yesRows(web, 'Certificate');
  const pop = population();

  const assigned = sum(lms, 'Modules_Assigned');
  const completed = sum(lms, 'Modules_Completed');

  const scored = lms.filter(r => isFinite(N(r['Assessment_Score_%'])) && r['Assessment_Score_%'] != null);
  const assessment = scored.length ? sum(scored, 'Assessment_Score_%') / scored.length / 100 : null;

  const members = sum(viva, 'Community_Members');
  const active = sum(viva, 'Active_Users');

  const lmsRate = rate(completed, assigned);
  const attendRate = rate(attended.length, registered.length);
  const certRate = rate(certified.length, attended.length);

  /* 10 — the composite. Components that are absent drop out and the weights
   *      renormalise over what remains, so the index never silently treats a
   *      missing source as a zero. */
  const parts = [
    { id:'lms', label:'Learning completion', weight:RULES.indexWeights.lms, value:lmsRate },
    { id:'attendance', label:'Webinar attendance', weight:RULES.indexWeights.attendance, value:attendRate },
    { id:'assessment', label:'Assessment score', weight:RULES.indexWeights.assessment, value:assessment },
    { id:'certification', label:'Certification', weight:RULES.indexWeights.certification, value:certRate },
  ];
  const live = parts.filter(p => p.value != null && isFinite(p.value));
  const weightSum = live.reduce((a, p) => a + p.weight, 0);
  const index = weightSum
    ? live.reduce((a, p) => a + p.weight * p.value, 0) / weightSum * 100 : null;

  return {
    parts, live, weightSum,
    list: [
      { n:1, label:'Campaigns Running', value:names.size, kind:'int',
        den:`across ${[has('email') && 'email', has('whatsapp') && 'WhatsApp', has('viva') && 'Viva']
          .filter(Boolean).join(' + ') || 'no source'}`,
        state: waMissing ? PARTIAL : OK,
        note:'Distinct campaign names. The same campaign appears in more than one export and is counted once.' },

      { n:2, label: RULES.reachLabel, value:emailDelivered + waDelivered, kind:'int',
        den: waMissing ? 'email only — WhatsApp not loaded' : 'email + WhatsApp',
        state: waMissing ? PARTIAL : OK,
        note:'Messages delivered, not people reached. Neither export carries a recipient list, so unique reach cannot be derived — someone who received all five campaigns is counted five times.' },

      { n:3, label:'Email Engagement Rate', value:rate(emailClicked, emailDelivered), kind:'pct',
        den:`${fmtInt(emailClicked)} clicked ÷ ${fmtInt(emailDelivered)} delivered`,
        state: has('email') ? OK : BLOCKED,
        note:'Clicks over deliveries, computed from the raw counts rather than averaging the per-campaign CTR column — a small campaign and a large one must not weigh the same.' },

      { n:4, label:'WhatsApp Engagement Rate', value:rate(waClicked, waDelivered), kind:'pct',
        den: waMissing ? 'awaiting 02_WhatsApp_Campaign_KPI' : `${fmtInt(waClicked)} clicked ÷ ${fmtInt(waDelivered)} delivered`,
        state: waMissing ? BLOCKED : OK,
        note:'Blocked until the WhatsApp export is dropped in. Nothing else needs to change — the tile fills itself.' },

      { n:5, label:'Overall Digital Engagement', value:rate(emailClicked + waClicked, emailDelivered + waDelivered), kind:'pct',
        den:`${fmtInt(emailClicked + waClicked)} clicks ÷ ${fmtInt(emailDelivered + waDelivered)} delivered`,
        state: waMissing ? PARTIAL : OK,
        note:`Clicks only, across email and WhatsApp — one rate, one unit. Counting Viva's ${fmtInt(vivaEngagements)} likes, comments and shares in the numerator would read ${fmtPct(mixed)}, but it would divide community activity by email deliveries. Viva keeps its own tile instead.` },

      { n:6, label:'Webinar Registration Rate', value:rate(registered.length, pop), kind:'pct',
        den:`${fmtInt(registered.length)} registered ÷ ${fmtInt(pop)} employees`,
        state: has('webinar') ? OK : BLOCKED,
        note:'The denominator is the employees present in the exports, which is the invited population — not necessarily the whole target audience.' },

      { n:7, label:'Webinar Attendance Rate', value:attendRate, kind:'pct',
        den:`${fmtInt(attended.length)} attended ÷ ${fmtInt(registered.length)} registered`,
        state: has('webinar') ? OK : BLOCKED,
        note:'Show-up rate among those who registered. Registration against the whole population is KPI 6.' },

      { n:8, label:'Learning Completion Rate', value:lmsRate, kind:'pct',
        den:`${fmtInt(completed)} ÷ ${fmtInt(assigned)} modules`,
        state: has('lms') ? OK : BLOCKED,
        note:'Modules, not people: an employee who finished four of eight counts as half. The share of employees who finished everything is lower, and is shown below.' },

      { n:9, label:'Viva Engagement Rate', value:rate(active, members), kind:'pct',
        den:`${fmtInt(active)} active ÷ ${fmtInt(members)} members`,
        state: has('viva') ? OK : BLOCKED,
        note:'Community_Members swings by thousands between consecutive days, so it behaves like members targeted that day rather than community size. Read this as daily activation of the targeted set.' },

      { n:10, label:'Learning Engagement Index', value:index, kind:'index',
        den: live.length === parts.length ? 'all four components' : `${live.length} of 4 components`,
        state: live.length === parts.length ? OK : PARTIAL,
        note:`Weighted composite: ${parts.map(p => `${p.label} ${Math.round(p.weight * 100)}%`).join(', ')}. Weights are a management choice — these are the defaults proposed in the plan.` },
    ].map(k => segNote && (k.n <= 5 || k.n === 9) ? { ...k, seg:segNote } : k),
  };
}
