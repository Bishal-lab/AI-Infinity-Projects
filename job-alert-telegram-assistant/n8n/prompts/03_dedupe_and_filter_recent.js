// Drops feed-fetch error markers, keeps only postings published within the last
// LOOKBACK_HOURS, and skips anything already alerted in a previous run (tracked
// in this workflow's static data, keyed by link).
//
// Static data lives with the workflow on this n8n instance and survives between
// scheduled runs, but resets if you delete and re-import the workflow fresh.

const LOOKBACK_HOURS = 72;
const MAX_SEEN_LINKS = 1000;

const staticData = $getWorkflowStaticData('global');
if (!Array.isArray(staticData.seenLinks)) staticData.seenLinks = [];
const seen = new Set(staticData.seenLinks);

const cutoff = Date.now() - LOOKBACK_HOURS * 60 * 60 * 1000;
const fresh = [];

for (const item of $input.all()) {
  const j = item.json;
  if (j.fetchError) continue;
  if (!j.link || seen.has(j.link)) continue;

  const published = j.pubDate ? Date.parse(j.pubDate) : NaN;
  if (!Number.isNaN(published) && published < cutoff) continue;

  fresh.push(item);
  seen.add(j.link);
}

staticData.seenLinks = Array.from(seen).slice(-MAX_SEEN_LINKS);

return fresh;
