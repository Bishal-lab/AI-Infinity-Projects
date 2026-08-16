# Troubleshooting

## Nothing ever arrives in Telegram

1. Check that both the relevant **watcher scenario(s)** and the **digest
   sender scenario** are toggled **Active** (top-right in each scenario) — a
   scheduled scenario only runs while active.
2. Open the watcher scenario's **History** tab and inspect the last run:
   - No run at all yet → check the scheduling (clock icon) is set, and that
     enough time has passed since activation for the interval to fire; use
     ▶ **Run once** to test immediately instead of waiting.
   - Module 1 (`Watch RSS feed items`) ran with 0 bundles → expected most
     runs once it's caught up (it only emits genuinely new items since the
     last poll); not a bug. To force a re-test, temporarily point the module
     at a feed URL you know has recent content, or just wait for a real new
     posting.
   - Module 2 (`Create a Chat Completion`) errored → check the OpenAI
     connection is attached and has quota; check the pasted system prompt
     didn't get truncated by a field character limit.
   - Module 3 (`Parse JSON`) errored → the OpenAI reply wasn't valid JSON
     (rare, but possible without JSON-mode support) — open the raw
     completion text in that run's log and check it doesn't have stray
     text/markdown fences around the JSON.
   - The "Relevant only" filter blocked everything → read a few `reason`
     values from Module 3's output across recent runs to sanity-check
     whether the target-role/industry rules in
     `make/relevance_classifier_prompt.md` are too strict for what's
     actually showing up.
3. Open the digest sender's **History** tab:
   - Module 1 (`Search Records`, filter `notified = false`) returned 0
     records → nothing new from any watcher since the last digest run; this
     is expected, not an error.
   - The "Has new jobs" filter blocked the run → same as above, expected when
     there's genuinely nothing new.
   - Module 3 (`Send a Text Message`) errored → see the Telegram section
     below.

## Telegram "chat not found" / 400 errors

- The bot can only message a chat it has been started in. Open Telegram, find
  your bot (search the username you gave BotFather), and send it `/start`
  once — then re-fetch your chat ID (see the main README's Telegram setup
  step) and update the **Chat ID** field on the `Send a Text Message` module.
- Group chats: add the bot to the group, send any message, then read the
  `chat.id` (a negative number) from
  `https://api.telegram.org/bot<TOKEN>/getUpdates`.

## Indeed RSS feed returns empty or an error page

Indeed has changed RSS availability by country/region over time — this is a
known, external limitation, not a bug in this project. Because every feed is
its own watcher scenario, a dead Indeed feed just means that one scenario
never finds anything — it has no effect on the Google Alerts scenarios or any
other region. If a specific regional feed consistently returns nothing:

1. Test the URL directly in a browser — a 404, redirect to a CAPTCHA page, or
   an empty `<channel>` (no `<item>` tags) all mean that region's RSS is
   currently unavailable.
2. Deactivate that one watcher scenario.
3. Rely on the Google Alerts scenarios for that region instead — Google
   indexes Indeed listings too, just with a delay.

## Duplicate alerts for the same job

Dedupe happens two ways: the `Watch RSS feed items` trigger's own built-in
new-item tracking (per feed), and the `Job Alerts` Data Store's
Add/Replace-by-`link`-key step (across feeds — so the same job surfacing via
both a Google Alert and an Indeed feed collapses into one record). If a job
board changes a listing's URL (e.g. adds a tracking query string) between
polls, it'll look like a "new" posting and be re-sent — a known tradeoff of
link-based dedupe kept intentionally simple.

## I rebuilt/reimported a scenario and now I'm getting old alerts again

A watcher scenario's "only new since last poll" state lives with that
specific scenario in your Make account — cloning or rebuilding it resets that
state, so the next run may re-surface postings it already saw. The `Job
Alerts` Data Store is separate and persists independently, so already-sent
jobs (`notified: true`) still won't be re-sent by the digest sender even if a
watcher re-emits them — worst case, one extra Data Store write, not a repeat
Telegram message.

## Digest arrives but records never flip to `notified: true`

Check Module 5 (`Update a Record`) in the digest sender — its **Key** field
must reference the Iterator's per-record key output (click it from the
mapping panel rather than typing a key manually), otherwise it may be
updating a nonexistent or wrong record. Confirm in **Data stores → Job
Alerts** that records shown in a Telegram digest actually flip to
`notified: true` after that scenario run.
