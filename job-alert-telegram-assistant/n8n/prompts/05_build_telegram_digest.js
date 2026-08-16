// Builds one or more Telegram-ready Markdown digest messages from the relevant,
// classified postings that survived the "Relevant Only" filter. Splits into
// multiple messages if the digest would exceed Telegram's ~4096-character limit.

const TIER_LABELS = {
  1: '🟢 Delhi NCR',
  2: '🟡 India (other)',
  3: '🔵 UAE',
  4: '🟣 Australia',
  5: '⚪ Other (open to Indian profiles)',
};
const MAX_MESSAGE_LENGTH = 3800;

function tierOf(item) {
  const t = item.json.output && item.json.output.location_tier;
  return Number.isInteger(t) ? t : (item.json.priorityTier || 5) || 5;
}

function formatEntry(item) {
  const j = item.json;
  const out = j.output || {};
  const title = j.title || 'Untitled role';
  const loc = out.location_text || 'Location unclear — check listing';
  const reason = out.reason ? `\n_${out.reason}_` : '';
  return `*${title}*\n📍 ${loc} | via ${j.sourceLabel || 'search'}${reason}\n🔗 ${j.link}`;
}

const items = $input.all().slice().sort((a, b) => tierOf(a) - tierOf(b));

const chunks = [];
let current = '🔔 *New role matches found*\n';
let currentTier = null;

for (const item of items) {
  const tier = tierOf(item);
  let addition = '';
  if (tier !== currentTier) {
    addition += `\n\n*${TIER_LABELS[tier] || 'Other'}*\n`;
    currentTier = tier;
  }
  addition += `\n${formatEntry(item)}\n`;

  if ((current + addition).length > MAX_MESSAGE_LENGTH) {
    chunks.push(current);
    current = addition;
  } else {
    current += addition;
  }
}
if (current.trim()) chunks.push(current);

return chunks.map(message => ({ json: { message } }));
