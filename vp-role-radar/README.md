# VP Role Radar

A standing watch for **Vice President & equivalent key-account-management
openings in Life Insurance across India, the GCC and Asia** — scored against
one specific candidate profile, and delivered every weekday morning to Gmail
and to Claude chat.

It is not a job board. It is a filter with an opinion: every opening arrives
with a fit score out of 100 and the reasons behind it, so the brief can be
trusted at a glance rather than re-read in full.

```
STRONG FIT (2)
------------------------------------------------------------
1. Vice President - Key Accounts Lead - Credit Life Insurance Business
   Leading Life Insurer — 92/100 · 15-20 yrs
   Mumbai, India · posted 24 Aug · Careerjet · India
   Why it fits: VP & above (vice president) · key accounts · bancassurance ·
   life insurance · India — Mumbai · 15-20 yrs asked — squarely in range ·
   your edge: P&L
   https://…
```

## How it works

```
sources.yaml ─┬─ workday ──┐
              ├─ greenhouse│
              ├─ lever     ├─► normalise ─► score against profile.yaml
              ├─ rss       │      │            (5 dimensions, 3 of them gates)
              └─ careerjet │      ▼
                 jooble    │   dedupe ─► seen-store ─► rank & cap
                 adzuna ───┘                              │
                                                          ├─► Gmail (SMTP)
                                                          └─► radar-state branch
                                                                    │
                                                          Claude Routine reads it
                                                          and posts the brief in chat
```

Delivery is split because capability is split. The GitHub Actions runner can
reach job boards; a Claude session cannot — the sandbox blocks those hosts
outright. So the Action does the scanning and mails the digest, publishes the
same brief to an orphan `radar-state` branch, and a scheduled Claude session
reads that file half an hour later and posts it into chat.
[docs/claude-routine.md](docs/claude-routine.md) covers that lane in full.

## What counts as a match

`config/profile.yaml` holds the whole definition. Five dimensions make up a
score out of 100; three of them are also gates, so a role that fails one is
dropped rather than scored low.

| Dimension | Points | Gate |
|---|---:|---|
| **Seniority** — VP/SVP/EVP/Head/Director at full marks, AVP and Deputy VP as eligible | 30 | ✓ |
| **Function** — key accounts, bancassurance, partnership distribution, alliances, group & institutional business | 30 | ✓ |
| **Domain** — life insurance at full weight, the rest of insurance and BFSI at a discount | 20 | |
| **Geography** — India (Delhi-NCR preferred), GCC, Asia | 10 | ✓ |
| **Edge** — P&L ownership, multi-market/APAC remit, shared services, transformation and GTM | 10 | |

Plus one more gate that is not a score: a posting advertising an experience
band topping out below ten years is dropped whatever its title says.

Tiers: **Strong** ≥ 70, **Possible** ≥ 55, **Stretch** ≥ 45. Everything below
`min_fit` never appears. [docs/tuning.md](docs/tuning.md) explains how to move
any of it.

## Setup

### 1. Gmail

The digest goes out over SMTP with an App Password — Gmail refuses ordinary
passwords.

```
myaccount.google.com → Security → 2-Step Verification (turn on)
                     → App passwords → generate one for "Mail"
```

Add the repository secrets under **Settings → Secrets and variables → Actions**:

| Secret | Required | Notes |
|---|---|---|
| `GMAIL_ADDRESS` | ✓ | the sending account |
| `GMAIL_APP_PASSWORD` | ✓ | the 16-character App Password |
| `EMAIL_TO` | | defaults to `das.bishal178@gmail.com`; comma-separated for several |
| `EMAIL_FROM_NAME` | | defaults to *VP Role Radar* |

The BFSI digest bot in this repository already uses the first two names, so a
repo sending that brief needs no new credentials here.

### 2. Confirm the sources

The shipped list was verified on a live runner on 25 Aug 2026 — AIA,
Prudential plc, Manulife and Sun Life all answer. Re-run this after changing
`sources.yaml`, and whenever the digest footer starts naming a source it could
not read:

```
Actions → VP role radar → Run workflow → mode: check-sources
```

It fetches every source and prints what came back — how many jobs, how recent,
and what failed. The mode exits non-zero when any source is dead, so a red run
there means "something needs fixing", not that the radar is broken.

A wrong Workday site name is the likeliest failure and the easiest fix: open
the employer's careers site, copy the URL from the address bar, and paste it
into `sources.yaml` as-is. The adapter derives the JSON endpoint from it.

Then `mode: test-delivery`, then `mode: dry-run` to read the rendered brief as
an artifact, and finally `mode: run` for a real send.

### 3. Optional: widen the coverage

Out of the box the radar reads insurer career sites, and hands you one-click
pre-filtered searches for the boards it cannot read. The boards carrying the
most Indian and Gulf roles — Naukri, iimjobs, Bayt, GulfTalent — are reachable
programmatically only through an aggregator, and all three aggregator adapters
ship switched off for want of a free key:

| Key | From | Coverage |
|---|---|---|
| `CAREERJET_AFFID` | careerjet.com/partners | India **and** the Gulf — start here |
| `JOOBLE_API_KEY` | jooble.org/api/about | India, Gulf, South-East Asia |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | developer.adzuna.com | India |

Add the secret, set `enabled: true` on that source in `sources.yaml`, and it
starts contributing on the next run. Until then `check-sources` reports it as
*off*, not as broken.

## Running it by hand

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m radar preview --explain   # print the brief, and what was passed over
.venv/bin/python -m radar check-sources       # fetch every source, report on it
.venv/bin/python -m radar test-delivery       # verify the Gmail credentials
.venv/bin/python -m radar run --dry-run       # render to out/, send nothing
.venv/bin/python -m radar run                 # scan and send
```

`preview` never touches the seen-store, so running it costs you nothing
tomorrow.

## Tests

```bash
.venv/bin/python -m pytest -q
```

101 tests, none of which touch the network or an inbox — sources are injected
as recorded payloads through the seam `build_digest(results=...)` exists for.
They cover the scoring rules case by case (a VP banca role in Mumbai is Strong;
a Key Account Manager, a VP-Actuarial role, a London posting and a 2-5 year band
are each rejected for their own stated reason), the deduper (one job on three
boards collapses to one, but "VP - Key Accounts, West" and "… South" stay two),
the seen-store, and both renderers.

## Layout

```
config/     profile.yaml (what counts as a match) · sources.yaml · settings.yaml
radar/      the pipeline: normalize → fit → dedupe → state → digest → render
  sources/  one small module per kind of board
prompts/    the prompt the scheduled Claude session fires
docs/       scheduling · tuning · the Claude-chat lane
```

## Honest limits

- **Day-one automated coverage is insurer career sites.** iimjobs and Naukri,
  the richest India sources for these roles, publish nothing key-free to read.
  They are covered by saved-search links in every digest until a Careerjet or
  Jooble key is added — a config change, not a rebuild.
- **Two of the six Workday employers are off.** MetLife and Zurich answer HTTP
  422 on their Workday tenants — both run their careers sites elsewhere and
  publish no key-free endpoint, so they are reachable only through an
  aggregator or by hand. The reason is recorded in `sources.yaml` next to each.
- **Nothing here scrapes a site that forbids it**, and no board login is used.
