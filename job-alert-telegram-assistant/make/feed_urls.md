# Watcher scenarios — one per feed

Each row below is one **"Job Watcher: \<name\>"** scenario (see
[`build_guide.md`](build_guide.md) for the module-by-module setup — you build
one scenario fully, then use Make's **Clone** on the scenario to create the
rest, swapping just the Feed URL and the `Source` label in the OpenAI user
message).

Start with the two **required** rows — they're the reliable, no-scraping-risk
source (see [`../docs/search_queries.md`](../docs/search_queries.md) for how
to generate the actual feed URLs). The Indeed rows are optional bonus
coverage; Indeed has changed RSS availability by region over time, so treat
them as "nice to have, not load-bearing" — see
[`../docs/troubleshooting.md`](../docs/troubleshooting.md).

| Scenario name | Feed URL | Source label (for the OpenAI prompt) |
|---|---|---|
| **Required** — Job Watcher: Life Insurance VP | `REPLACE_WITH_GOOGLE_ALERTS_RSS_URL_1` (from Google Alerts, see docs/search_queries.md Alert 1) | `Google Alerts — Life Insurance VP` |
| **Required** — Job Watcher: Travel Business Head | `REPLACE_WITH_GOOGLE_ALERTS_RSS_URL_2` (from Google Alerts, see docs/search_queries.md Alert 2) | `Google Alerts — Travel & Hospitality Business Head` |
| Optional — Job Watcher: Indeed India VP (Delhi NCR) | `https://www.indeed.co.in/rss?q=%22VP%22+%22Life+Insurance%22+Transformation&l=Delhi+NCR` | `Indeed India — VP, Life Insurance (Delhi NCR)` |
| Optional — Job Watcher: Indeed India Business Head (Delhi NCR) | `https://www.indeed.co.in/rss?q=%22Business+Head%22+Travel&l=Delhi+NCR` | `Indeed India — Business Head, Travel (Delhi NCR)` |
| Optional — Job Watcher: Indeed India VP (pan-India) | `https://www.indeed.co.in/rss?q=%22VP%22+%22Life+Insurance%22+Transformation` | `Indeed India — VP, Life Insurance (pan-India)` |
| Optional — Job Watcher: Indeed India Business Head (pan-India) | `https://www.indeed.co.in/rss?q=%22Business+Head%22+%22Travel%22` | `Indeed India — Business Head, Travel (pan-India)` |
| Optional — Job Watcher: Indeed UAE VP | `https://www.indeed.ae/rss?q=%22VP%22+%22Life+Insurance%22` | `Indeed UAE — VP, Life Insurance` |
| Optional — Job Watcher: Indeed UAE Business Head | `https://www.indeed.ae/rss?q=%22Business+Head%22+Travel` | `Indeed UAE — Business Head, Travel/Hospitality` |
| Optional — Job Watcher: Indeed Australia VP | `https://au.indeed.com/rss?q=%22VP%22+%22Life+Insurance%22` | `Indeed Australia — VP, Life Insurance` |
| Optional — Job Watcher: Indeed Australia Business Head | `https://au.indeed.com/rss?q=%22Business+Head%22+Travel` | `Indeed Australia — Business Head, Travel/Hospitality` |

Adding a new search later (e.g. a third role, or a new country) is just
another row: clone an existing watcher scenario, paste in the new feed URL,
update the `Source` label in the OpenAI user message, and activate it — it
writes into the same shared `Job Alerts` Data Store and digest sender, no
other changes needed.
