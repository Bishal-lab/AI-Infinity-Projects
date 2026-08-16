# Troubleshooting

## Nothing ever arrives in Telegram

1. Check the workflow is **Active** (toggle top-right in n8n) — a schedule
   trigger only fires on an active workflow.
2. Open the last execution (**Executions** tab). Check each node's output in
   order:
   - `Build Feed List` — 0 items means both `GOOGLE_ALERTS_*` placeholders are
     still unreplaced and no Indeed feeds are enabled. Fix the placeholders in
     the node's code (see `docs/search_queries.md`).
   - `Fetch & Parse Feeds` — items with `fetchError: true` mean that feed
     returned an error or timed out; check the `error` field. A feed 404ing
     doesn't stop the others.
   - `Dedupe & Filter Recent` — 0 items is *expected* most runs once the seen-
     links cache has caught up; it only means "nothing new since last time,"
     not that something's broken. To force a re-test, open the node, run
     `$getWorkflowStaticData('global').seenLinks = []` once via a temporary
     Code node, or just wait for a genuinely new posting.
   - `Relevance Classifier` — if this errors, check the LLM credential is
     attached (Credentials → OpenAI/Gemini) and the Structured Output Parser's
     JSON schema still matches `04_relevance_classifier.md`'s output format.
   - `Relevant Only` (Filter) — 0 items means the LLM classified everything as
     `relevant: false`. Read a few `reason` fields in the classifier's output
     to sanity-check whether the target-role/industry rules in
     `04_relevance_classifier.md` are too strict for what's actually showing up.
3. Check `Send Telegram Alert`'s node for an auth error — see below.

## Telegram node errors ("chat not found" / 400)

- The bot can only message a chat it has been started in. Open Telegram, find
  your bot (search the username you gave BotFather), and send it `/start`
  once — then re-fetch your `chat_id` (see main README's Telegram setup step)
  and update the node's `chatId` parameter.
- Group chats: add the bot to the group, send any message, then read the
  `chat.id` (negative number) from `https://api.telegram.org/bot<TOKEN>/getUpdates`.

## Indeed RSS feed returns empty or an error page

Indeed has changed RSS availability by country/region over time — this is a
known, external limitation, not a bug in this workflow. `02_fetch_and_parse_feeds.js`
already isolates each feed in a `try/catch` so one dead Indeed feed doesn't
break Google Alerts or the other regions. If a specific regional feed
consistently returns nothing:

1. Test the URL directly in a browser — a 404, redirect to a CAPTCHA page, or
   an empty `<channel>` (no `<item>` tags) all mean that region's RSS is
   currently unavailable.
2. Delete that feed's line from `n8n/prompts/01_build_feed_list.js` and from
   the corresponding `Build Feed List` code node in the workflow.
3. Rely on the Google Alerts feeds for that region instead — Google indexes
   Indeed listings too, just with a delay.

## Google Alerts feed is slow to update

Google Alerts RSS typically batches at most once a day even though this
workflow's schedule trigger polls every 6 hours — that's expected; the extra
polling frequency just means you see a new day's batch sooner after it lands,
not that Google refreshes faster.

## Duplicate alerts for the same job

The dedupe key is the posting's `link` field. If a job board changes a
listing's URL (e.g. adds a tracking query string) between your runs, it'll
look like a "new" posting and be re-sent — this is a known tradeoff of
link-based dedupe kept intentionally simple; a fuzzy title+company match would
catch more duplicates but risks false-positive suppression of genuinely
different roles.

## I re-imported the workflow and now I'm getting old alerts again

Static data (the `seenLinks` dedupe cache) is attached to the workflow
instance in your n8n database, not the JSON file — deleting and re-importing
the workflow resets it to empty, so the next run will re-alert on anything
still within the 72-hour lookback window. This is expected; it self-heals
after one run.
