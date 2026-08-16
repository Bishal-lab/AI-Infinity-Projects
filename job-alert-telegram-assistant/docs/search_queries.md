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
     frequent delivery than daily even via RSS — polling more often in Make
     just means you see a new day's batch sooner after it lands, not that
     Google refreshes faster).
   - **Sources**: Automatic (or narrow to "News" + "Web" if you get noise).
   - **Region**: Any region.
   - **Deliver to**: **RSS feed** (not "Mail").
3. Click **Create Alert**. It now appears in your alerts list with a small RSS
   icon — click it, then copy the URL of the page it opens
   (`https://www.google.com/alerts/feeds/<your-id>/<feed-id>`). That's the
   **Feed URL** for that alert's "Watch RSS feed items" module — see
   [`../make/feed_urls.md`](../make/feed_urls.md) and
   [`../make/build_guide.md`](../make/build_guide.md).

**Alert 1 — Life Insurance VP (Account Management / Transformation):**
```
("VP" OR "AVP" OR "Vice President") ("Account Management" OR "Transformation" OR "Business Transformation" OR "Digital Transformation") ("Life Insurance" OR "Insurance")
```

**Alert 2 — Travel & Hospitality Business Head:**
```
("Business Head" OR "Head of Business") (Travel OR Hospitality OR Aviation OR "Corporate Travel")
```

Keep both broad and let the Make scenario's LLM classifier (see
[`../make/relevance_classifier_prompt.md`](../make/relevance_classifier_prompt.md))
do the fine-grained relevant/not-relevant and location-tier decision — narrower
Google queries tend to under-return rather than over-return.

## Indeed regional RSS (optional secondary source)

Indeed has, at various points, disabled and re-enabled RSS on different country
domains, so these are wired up but optional — treat them as a bonus, not a
dependency. The full list of feed URLs is in
[`../make/feed_urls.md`](../make/feed_urls.md):

| Region | Feed URL pattern |
|---|---|
| India | `https://www.indeed.co.in/rss?q=<query>&l=<location>` |
| UAE | `https://www.indeed.ae/rss?q=<query>` |
| Australia | `https://au.indeed.com/rss?q=<query>` |

Test each URL directly in a browser before wiring up its watcher scenario —
if one returns an error page or an empty `<channel>` instead of `<item>`
entries, skip that scenario. Because each feed is its own independent Make
scenario (see [`../make/build_guide.md`](../make/build_guide.md)), a dead
Indeed feed only means that one scenario finds nothing — it can't break the
Google Alerts scenarios or any other region.

## Location priority (handled by the LLM classifier, not the search queries)

| Tier | Location |
|---|---|
| 1 | Delhi NCR (Delhi, Gurgaon/Gurugram, Noida, Faridabad) |
| 2 | Rest of India |
| 3 | UAE |
| 4 | Australia |
| 5 | Any other country that commonly accepts Indian nationals at this seniority (Singapore, Qatar, Saudi Arabia, UK, Canada, etc.) |

Adjust the tier list or the "other countries" examples in
[`../make/relevance_classifier_prompt.md`](../make/relevance_classifier_prompt.md)
any time your preferences change — no other file needs to change.
