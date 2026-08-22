# BFSI & Life Insurance Daily Brief

A small, self-contained bot that reads the BFSI press every morning, keeps the
stories that matter to a life-insurance reader, and delivers one brief to
**Telegram** and **Gmail** at **08:00 IST**.

No API keys for news, no scraping, no paid tiers: it reads public RSS/Atom feeds
from specialist BFSI desks, the RBI, the general business press, and targeted
news queries that pick up IRDAI actions. It runs free on GitHub Actions, or on
any machine with cron and Python 3.10+.

```
🗞 BFSI & Life Insurance Brief
Friday, 22 August 2026 · 08:00 IST

🛡️ LIFE INSURANCE
1. IRDAI eases surrender value norms for life insurers
   Mint · 06:40 · +2 more
   The regulator has relaxed the surrender value floor on non-linked savings
   products, effective 1 October.
2. HDFC Life posts 15% rise in VNB margin
   ET BFSI · 05:12
   Annualised premium equivalent grew 12% on the agency channel.

⚖️ REGULATION & POLICY
1. RBI issues master direction on co-lending arrangements
   RBI Press Releases · 04:00

🏦 BANKING & NBFC
1. Bank credit growth slows to 11% as deposit growth lags
   BusinessLine · 07:05

Window 21 Aug 06:00 – 22 Aug 08:00 (IST) · 17/17 sources responded
```

---

## Contents

- [What it actually does](#what-it-actually-does)
- [Quick start](#quick-start)
- [Setting up Telegram](#setting-up-telegram)
- [Setting up Gmail](#setting-up-gmail)
- [Scheduling it for 08:00 IST](#scheduling-it-for-0800-ist)
- [Tuning what you receive](#tuning-what-you-receive)
- [Command reference](#command-reference)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Tests](#tests)
- [Known limits](#known-limits)

---

## What it actually does

Each run:

1. **Fetches** every enabled feed in `config/sources.yaml`, concurrently, with
   retries. A dead feed is reported, not fatal.
2. **Cleans** each item: strips markup, parses whichever of the three date
   formats the feed used, removes campaign tracking from URLs, and drops the
   "` - Publisher`" suffix that search feeds append to headlines.
3. **Keeps the last 26 hours.** Wider than a day on purpose, so a story
   published at 07:55 IST is not lost between two runs.
4. **Scores and routes** every story against the taxonomy in
   `config/topics.yaml` — seven sections, several hundred keywords, weighted by
   whether the hit was in the headline and by how specialist the source is.
   Cricket, cinema and horoscopes are excluded outright.
5. **Collapses duplicates.** One wire story reaching you from four feeds under
   four URLs becomes one item, marked `+3 more`. Headlines are compared on
   stemmed words, so "life insurers" and "life insurance companies" match, while
   a Q1 and a Q2 results story stay separate.
6. **Drops anything already sent**, using a small JSON store keyed on both the
   URL and the headline.
7. **Trims** to at most 4 per source, 6 per section, 24 in total — strongest
   first.
8. **Renders and sends**: a Telegram message (split if long) and a multipart
   e-mail with an HTML and a plain-text part.

If nothing qualifies, it still sends a one-line note. A brief that silently
stops arriving is indistinguishable from a broken bot, so it says which it is —
including when the real problem is that no source could be reached.

## Quick start

```bash
cd bfsi-insurance-digest-bot
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then fill in the four credentials
python -m bot check-sources   # are the feeds alive?
python -m bot preview         # what would today's brief say?
python -m bot test-delivery   # are Telegram and Gmail wired up?
python -m bot run             # send it
```

`preview` and `check-sources` need no credentials at all, so you can see what
the brief looks like before setting anything up.

## Setting up Telegram

1. Open Telegram, message **@BotFather**, send `/newbot`, and follow the two
   prompts (a display name, then a username ending in `bot`).
2. BotFather replies with a token like `123456789:AAE...`. That is
   `TELEGRAM_BOT_TOKEN`.
3. **Send your new bot a message.** A bot cannot start a conversation; until you
   write to it first, it has no permission to write to you.
4. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and
   copy `result[0].message.chat.id`. That is `TELEGRAM_CHAT_ID`.

For a group: add the bot to the group, send any message there, and use the
group's chat id from the same URL (it will be negative). Several destinations —
separate the ids with commas.

`python -m bot test-delivery` confirms the token and resolves every chat id;
add `--send` to receive a real test message.

## Setting up Gmail

Gmail rejects ordinary account passwords over SMTP, so the bot needs an **App
Password**:

1. <https://myaccount.google.com> → **Security** → turn on **2-Step
   Verification** (App Passwords do not exist without it).
2. Same page → **App passwords** → create one, name it anything.
3. Google shows 16 characters. That is `GMAIL_APP_PASSWORD` — spaces in it are
   ignored, so keep or drop them.

Set `GMAIL_ADDRESS` to the sending account and `EMAIL_TO` to wherever the brief
should land (defaults to the sender; comma-separate for several).

To send through something other than Gmail, change `delivery.email.smtp_host`
and `smtp_port` in `config/settings.yaml`; set `use_ssl: false` for a server
that expects STARTTLS on port 587.

## Scheduling it for 08:00 IST

### GitHub Actions (nothing to host)

`.github/workflows/bfsi-digest.yml` in the repository root already does this.
Add five repository secrets under **Settings → Secrets and variables →
Actions**:

| Secret | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | from `getUpdates` |
| `GMAIL_ADDRESS` | the sending Gmail account |
| `GMAIL_APP_PASSWORD` | the 16-character App Password |
| `EMAIL_TO` | optional; defaults to `GMAIL_ADDRESS` |

Then run the workflow once by hand (**Actions → BFSI digest → Run workflow**),
choosing `test-delivery` and then `run`, to confirm it works before you rely on
the schedule.

The cron line is `30 2 * * *` — **02:30 UTC, which is 08:00 IST**. Actions cron
is always UTC and has no timezone setting; the half-hour offset also keeps the
job out of the on-the-hour queue, where scheduled runs routinely start ten to
fifteen minutes late.

Two Actions-specific things to know:

- GitHub **disables scheduled workflows after 60 days** with no commits to the
  default branch. Any commit re-arms them.
- The runner is discarded after each job, so the seen-store is carried between
  runs in the Actions cache. If that cache is ever evicted, one morning's brief
  may repeat a story or two — nothing worse.

### cron, on your own machine

```cron
# 02:30 UTC = 08:00 IST — use this if the host clock is UTC
30 2 * * * /path/to/bfsi-insurance-digest-bot/run.sh >> /var/log/bfsi-digest.log 2>&1

# If the host is already on IST:
0 8 * * * /path/to/bfsi-insurance-digest-bot/run.sh >> /var/log/bfsi-digest.log 2>&1
```

`run.sh` uses `.venv/bin/python` if there is one, and passes any arguments
through, so `run.sh check-sources` works too. Credentials come from `.env`.

### systemd, if you want retries on a laptop that sleeps

See [`docs/scheduling.md`](docs/scheduling.md) for a timer unit with
`Persistent=true`, which catches up a missed run after a reboot.

## Tuning what you receive

Everything is in `config/`, and nothing there is a secret.

**`sources.yaml` — where the news comes from.** Each entry has a `weight` (a
relevance bonus for everything that source publishes) and an optional
`section_hint`. Set `enabled: false` to mute a source without deleting it. One
international source ships switched off; turn it on for a global view.

The Google News entries are ordinary search feeds — edit the `q=` parameter to
track a company, a regulation or a theme:

```yaml
- id: gnews-my-topic
  name: News · Bima Sugam
  url: https://news.google.com/rss/search?q=%22Bima+Sugam%22+when:3d&hl=en-IN&gl=IN&ceid=IN:en
  weight: 1.0
  section_hint: regulation
```

**`topics.yaml` — what counts as relevant.** Seven sections, each with `strong`
and `supporting` keyword lists. Add the terms you care about to `strong`;
`multiplier` tilts a whole section up or down. Life Insurance sits at 1.2, which
is what makes a story that is both a bank story and a life story file under
life.

**`settings.yaml` — the shape of the brief.** The knobs you are most likely to
touch:

| Setting | Effect |
| --- | --- |
| `digest.min_score` | the relevance floor. Raise it if the brief feels noisy, lower it if thin |
| `digest.max_items_total` | overall length |
| `digest.max_items_per_source` | stops one prolific desk filling the brief |
| `digest.lookback_hours` | how far back to look |
| `digest.send_when_empty` | send a "nothing today" note, or stay silent |
| `delivery.telegram.include_summaries` | headlines only, or headlines with a gist |
| `delivery.email.subject_template` | supports `{date}`, `{count}`, `{top_section}`, `{top_headline}` |

After any change: `python -m bot preview --explain` shows what got in, what did
not, and the score and reason for each.

## Command reference

| Command | What it does |
| --- | --- |
| `python -m bot run` | build the brief and send it |
| `python -m bot run --dry-run` | build it, write it to `out/`, send nothing |
| `python -m bot run --only telegram` | restrict delivery to one channel |
| `python -m bot run --no-state` | re-send stories already delivered |
| `python -m bot preview` | print the brief; never sends, never touches the store |
| `python -m bot preview --explain` | also list what was passed over, and why |
| `python -m bot preview --html out/x.html` | write the HTML e-mail to a file |
| `python -m bot check-sources` | fetch every feed; report status, item count, freshness |
| `python -m bot test-delivery [--send]` | verify Telegram and Gmail credentials |

Global flags: `--config DIR`, `--env PATH`, `-v/--verbose`.

Exit codes: `0` fine, `1` a delivery or feed failure, `2` a configuration error.

## How it works

```
config/sources.yaml ─┐
                     ├─► feeds.py ──► normalize.py ──► digest.py ──► render.py ──┬─► channels/telegram.py
config/topics.yaml ──┤   fetch &      clean, date,     window,        Telegram    │
                     │   parse RSS/   canonical URL    score, route,  + HTML/text └─► channels/email_smtp.py
config/settings.yaml ┘   Atom/RDF                      dedupe, cap
                                                          ▲
                                                    state.py (seen-store)
```

| Module | Responsibility |
| --- | --- |
| `config.py` | loads and validates the three YAML files into frozen dataclasses |
| `feeds.py` | HTTP with retries; RSS 2.0, RSS 1.0/RDF and Atom parsing |
| `normalize.py` | markup, dates, canonical URLs, headline cleanup |
| `relevance.py` | keyword matching, scoring, section routing, explanations |
| `dedupe.py` | collapses the same story from several sources |
| `state.py` | the seen-store that stops the overlapping window repeating itself |
| `digest.py` | the pipeline, and the `Digest` the renderers consume |
| `render.py` | Telegram messages, HTML e-mail, plain-text e-mail |
| `channels/` | delivery, one module per channel |
| `cli.py` | the commands above |

Two dependencies: `requests` and `PyYAML` (plus `tzdata`, so a slim container
still knows what IST is). Feed parsing, e-mail assembly and SMTP all come from
the standard library — this job has to keep installing cleanly for years with
nobody watching.

## Troubleshooting

**Nothing arrived at all.** Check the workflow run (or the cron log). The bot
exits non-zero on a delivery failure and logs which channel failed and why.

**The brief says no source could be reached.** Almost always a network or proxy
issue on the host rather than the feeds. `python -m bot check-sources` names
each failure.

**One source keeps failing.** Publishers move their feeds. `check-sources`
prints the error per feed; open the URL in a browser, find the publisher's
current RSS link, and update `config/sources.yaml`.

**Telegram: "chat not found".** You have not messaged the bot yet, or the chat
id is wrong. Message the bot, reload `getUpdates`, copy the id again.

**Telegram: "can't parse entities".** A headline contained markup the renderer
did not escape. Please open an issue with the headline — everything is escaped
on the way out, so this would be a bug.

**Gmail: "Username and Password not accepted".** The password is a normal
account password, not an App Password. See [Setting up Gmail](#setting-up-gmail).

**The brief is too noisy / too thin.** Move `digest.min_score` — up for noisy,
down for thin — and check the effect with `preview --explain`.

**Yesterday's stories came back.** The seen-store was lost (a cleared Actions
cache, or a deleted `state/seen.json`). It rebuilds itself from the next run.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

134 tests, all offline: feed parsing runs against fixtures in
`tests/fixtures/`, delivery is faked, and no test opens a socket. The suite
covers the shipped `config/` too, so an edit to the taxonomy that breaks routing
shows up here rather than at 08:00.

## Known limits

- **Feeds are only as reliable as their publishers.** URLs in `sources.yaml`
  were correct when written; run `check-sources` on first use, and occasionally
  after, to catch any that have moved.
- **Relevance is keyword-based**, not a language model. It is transparent,
  free and fast, and `preview --explain` shows its reasoning — but it will
  occasionally keep a story you would not have, or miss one phrased unusually.
  Adding the term to `topics.yaml` fixes the miss permanently.
- **Headlines and summaries only.** The bot links to articles; it does not
  fetch, store or republish their text.
- **A scheduled GitHub run is best-effort.** If one is skipped, the next
  morning's 26-hour window picks up what was missed.
