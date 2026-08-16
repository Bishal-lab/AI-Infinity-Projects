// Fetches every feed produced by "Build Feed List" and parses RSS 2.0 / Atom
// entries into a flat, normalized list.
//
// This runs as ONE Code node (rather than one RSS Feed Read node per feed, with
// downstream nodes reading back $('Build Feed List').item) because that node
// expands a single input item into many output items, and n8n can't reliably
// resolve a paired-item expression across that 1-to-many boundary. Fetching
// and parsing here keeps every entry attached to the feed it came from.

function decodeEntities(str) {
  return (str || '')
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function extractTag(block, tag) {
  const m = block.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i'));
  return m ? decodeEntities(m[1]) : '';
}

function extractLink(block) {
  // RSS: <link>url</link>. Atom: <link href="url"/>.
  const rss = block.match(/<link[^>]*>([\s\S]*?)<\/link>/i);
  if (rss && rss[1].trim()) return decodeEntities(rss[1]);
  const atom = block.match(/<link[^>]*href=["']([^"']+)["']/i);
  return atom ? atom[1] : '';
}

function parseFeed(xml) {
  const isAtom = /<feed[\s>]/i.test(xml) && !/<rss[\s>]/i.test(xml);
  const tag = isAtom ? 'entry' : 'item';
  const blocks = xml.match(new RegExp(`<${tag}[\\s\\S]*?<\\/${tag}>`, 'gi')) || [];
  return blocks.map(block => ({
    title: extractTag(block, 'title'),
    link: extractLink(block),
    pubDate: extractTag(block, isAtom ? 'published' : 'pubDate') || extractTag(block, 'updated'),
    snippet: (extractTag(block, isAtom ? 'summary' : 'description') || extractTag(block, 'content')).slice(0, 500),
  }));
}

const feeds = $input.all();
const results = [];

for (const feed of feeds) {
  const { label, feedUrl, priorityTier } = feed.json;
  try {
    const xml = await this.helpers.httpRequest({
      url: feedUrl,
      method: 'GET',
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; job-alert-bot/1.0)' },
      timeout: 15000,
    });
    const entries = parseFeed(typeof xml === 'string' ? xml : String(xml));
    for (const entry of entries) {
      if (!entry.title || !entry.link) continue;
      results.push({ json: { ...entry, sourceLabel: label, priorityTier } });
    }
  } catch (err) {
    // One broken feed (e.g. Indeed RSS disabled in a region) must not fail the run.
    results.push({ json: { fetchError: true, feed: label, error: err.message } });
  }
}

return results;
