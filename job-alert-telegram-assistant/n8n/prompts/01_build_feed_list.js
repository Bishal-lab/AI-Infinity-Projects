// Defines every search feed this workflow polls, each tagged with a rough priority
// tier matching the candidate's location preference: 1 = Delhi NCR, 2 = rest of
// India, 3 = UAE, 4 = Australia. Tier 0 = "let the LLM figure out the location"
// (used for the broad Google Alerts feeds, which aren't location-scoped).
//
// GOOGLE_ALERTS_* values must be replaced with the real RSS feed URLs you get after
// creating the two Google Alerts described in docs/search_queries.md (the Alerts
// "RSS feed" link — not the email digest link).

const GOOGLE_ALERTS_LIFE_INSURANCE_VP = 'REPLACE_WITH_GOOGLE_ALERTS_RSS_URL_1';
const GOOGLE_ALERTS_TRAVEL_BUSINESS_HEAD = 'REPLACE_WITH_GOOGLE_ALERTS_RSS_URL_2';

const feeds = [
  { label: 'Google Alerts — Life Insurance VP (Account Mgmt / Transformation)', feedUrl: GOOGLE_ALERTS_LIFE_INSURANCE_VP, priorityTier: 0 },
  { label: 'Google Alerts — Travel & Hospitality Business Head', feedUrl: GOOGLE_ALERTS_TRAVEL_BUSINESS_HEAD, priorityTier: 0 },

  // Optional Indeed regional RSS feeds — Indeed has changed RSS availability by
  // region over the years, so verify these after import; if one 404s or returns
  // empty, just delete that line (see docs/troubleshooting.md).
  { label: 'Indeed India — VP Account Management, Life Insurance (Delhi NCR)', feedUrl: 'https://www.indeed.co.in/rss?q=%22VP+Account+Management%22+%22Life+Insurance%22&l=Delhi+NCR', priorityTier: 1 },
  { label: 'Indeed India — VP Transformation, Life Insurance (Delhi NCR)', feedUrl: 'https://www.indeed.co.in/rss?q=%22VP+Transformation%22+%22Life+Insurance%22&l=Delhi+NCR', priorityTier: 1 },
  { label: 'Indeed India — Business Head, Travel (Delhi NCR)', feedUrl: 'https://www.indeed.co.in/rss?q=%22Business+Head%22+Travel&l=Delhi+NCR', priorityTier: 1 },
  { label: 'Indeed India — VP, Life Insurance / Transformation (pan-India)', feedUrl: 'https://www.indeed.co.in/rss?q=%22VP%22+%22Life+Insurance%22+Transformation', priorityTier: 2 },
  { label: 'Indeed India — Business Head, Travel/Hospitality (pan-India)', feedUrl: 'https://www.indeed.co.in/rss?q=%22Business+Head%22+%22Travel%22', priorityTier: 2 },
  { label: 'Indeed UAE — VP, Life Insurance', feedUrl: 'https://www.indeed.ae/rss?q=%22VP%22+%22Life+Insurance%22', priorityTier: 3 },
  { label: 'Indeed UAE — Business Head, Travel/Hospitality', feedUrl: 'https://www.indeed.ae/rss?q=%22Business+Head%22+Travel', priorityTier: 3 },
  { label: 'Indeed Australia — VP, Life Insurance', feedUrl: 'https://au.indeed.com/rss?q=%22VP%22+%22Life+Insurance%22', priorityTier: 4 },
  { label: 'Indeed Australia — Business Head, Travel/Hospitality', feedUrl: 'https://au.indeed.com/rss?q=%22Business+Head%22+Travel', priorityTier: 4 },
];

return feeds
  .filter(f => f.feedUrl && !f.feedUrl.startsWith('REPLACE_WITH'))
  .map(f => ({ json: f }));
