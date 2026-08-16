# Search queries

## Google Alerts (primary source — set up once, covers LinkedIn, Naukri, iimjobs,
## Shine, company career pages, and anything else Google indexes)

Google Alerts is the primary source because it isn't scraping any single job
board (no ToS risk, nothing to break when a site redesigns), and it indexes far
more sources than any single job board's own search.

Go to **[google.com/alerts](https://www.google.com/alerts)**, sign in, and create
the two alerts below. For each one:

1. Paste the query into the search box.
2. Click **Show options** and set:
   - **How often**: At most once a day (Google Alerts doesn't offer more
     frequent delivery than daily even via RSS — the n8n schedule polls more
     often than that in case of updates, but new results still generally only
     land ~daily).
   - **Sources**: Automatic (or narrow to "News" + "Web" if you get noise).
   - **Region**: Any region.
   - **Deliver to**: **RSS feed** (not "Mail").
3. Click **Create Alert**. It now appears in your alerts list with a small RSS
   icon — click it, then copy the URL of the page it opens
   (`https://www.google.com/alerts/feeds/<your-id>/<feed-id>`). That's the URL
   to paste into `GOOGLE_ALERTS_LIFE_INSURANCE_VP` /
   `GOOGLE_ALERTS_TRAVEL_BUSINESS_HEAD` in
   [`n8n/prompts/01_build_feed_list.js`](../n8n/prompts/01_build_feed_list.js)
   (and the matching `Build Feed List` node in the imported workflow).

**Alert 1 — Life Insurance VP (Account Management / Transformation):**
```
("VP" OR "AVP" OR "Vice President") ("Account Management" OR "Transformation" OR "Business Transformation" OR "Digital Transformation") ("Life Insurance" OR "Insurance")
```

**Alert 2 — Travel & Hospitality Business Head:**
```
("Business Head" OR "Head of Business") (Travel OR Hospitality OR Aviation OR "Corporate Travel")
```

Keep both broad and let the n8n workflow's LLM classifier (see
[`n8n/prompts/04_relevance_classifier.md`](../n8n/prompts/04_relevance_classifier.md))
do the fine-grained relevant/not-relevant and location-tier decision — narrower
Google queries tend to under-return rather than over-return.

## Indeed regional RSS (optional secondary source)

Indeed has, at various points, disabled and re-enabled RSS on different country
domains, so these are wired up but optional — treat them as a bonus, not a
dependency. They're already in
[`n8n/prompts/01_build_feed_list.js`](../n8n/prompts/01_build_feed_list.js):

| Region | Feed URL pattern |
|---|---|
| India | `https://www.indeed.co.in/rss?q=<query>&l=<location>` |
| UAE | `https://www.indeed.ae/rss?q=<query>` |
| Australia | `https://au.indeed.com/rss?q=<query>` |

Test each URL directly in a browser after setup (see
[`troubleshooting.md`](troubleshooting.md)) — if one returns an error page or an
empty `<channel>` instead of `<item>` entries, delete that line from
`01_build_feed_list.js` and the corresponding node input; the workflow degrades
gracefully either way (a broken feed is caught and skipped, not fatal — see
`02_fetch_and_parse_feeds.js`).

## Location priority (handled by the LLM classifier, not the search queries)

| Tier | Location |
|---|---|
| 1 | Delhi NCR (Delhi, Gurgaon/Gurugram, Noida, Faridabad) |
| 2 | Rest of India |
| 3 | UAE |
| 4 | Australia |
| 5 | Any other country that commonly accepts Indian nationals at this seniority (Singapore, Qatar, Saudi Arabia, UK, Canada, etc.) |

Adjust the tier list or the "other countries" examples in
[`04_relevance_classifier.md`](../n8n/prompts/04_relevance_classifier.md) any
time your preferences change — no code changes needed elsewhere.
