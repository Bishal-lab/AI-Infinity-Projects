# KPI definitions

The same definitions the dashboard computes from. The in-app **Definitions & settings** page renders
this table directly from `KPI_DEFINITIONS` in `comms_dashboard/metrics/kpis.py`, so the documentation
and the arithmetic cannot drift apart.

## The funnel

| Stage | Meaning |
|---|---|
| **Targeted** | The campaign intended to reach this person. The workforce-coverage denominator. |
| **Delivered** | The message technically arrived at the person's endpoint. |
| **Opened** | Passive consumption — saw, read, opened, or registered. |
| **Engaged** | An active act — click, reply, reaction, attendance. |
| **Completed** | The campaign's own success criterion was met. |

## Rates

| KPI | Formula | Why this denominator |
|---|---|---|
| Delivery rate | delivered / targeted | |
| Open / view rate | opened / **delivered** | A delivery failure should not be scored as disinterest. Set `rules.open_rate_basis: targeted` to change it. |
| Engagement rate | engaged / **delivered** | The headline measure. Far less corrupted by tracking artefacts than open rate. |
| Engagement of those who looked | engaged / **opened** | Isolates content quality from reach. A low number here is a message problem, not a distribution problem. |
| Completion rate | completed / **targeted** | Against everyone assigned, because that is the population management is accountable for — not just those who started. |
| Completion of those who started | completed / **opened** | Separates "nobody started" from "people started and gave up". |
| Registration rate | registered / invited | Teams only. |
| Attendance rate | attended / **registered** | Teams only. |
| Show-up rate | attended / **invited** | Teams only. Shown separately from attendance rate because the two are chronically conflated and tell very different stories. |
| Bounce rate | bounced / targeted | |
| Opt-out rate | opted out / delivered | |
| Workforce coverage | distinct employees reached (≥ Opened) / in-scope headcount | Needs identity resolution. Without a roster this returns "not measured" rather than a flattering approximation. |
| Channel effectiveness index | 0–100 weighted composite of the rates a channel supports | Renormalised across supported stages. Scoped to within one campaign. |

## When a rate is not a number

| Shown as | Meaning |
|---|---|
| `n/a` | No channel in view measures that stage, or the two sides count different things. |
| `not measured` | The channel supports the stage but the export loaded did not carry it. |
| `suppressed` | The group is smaller than `privacy.min_group_size`. |

None of these is ever shown as `0`. A zero is a measurement; these are the absence of one.

`0.0%` **is** shown when it is real — nobody engaging is a finding, not a gap.

## Cross-channel rates

Channels do not all measure the same stages, so a headline rate restricts **both** its numerator and
its denominator to the channels that measure both stages. Adding every channel's opens and dividing
by every channel's deliveries mixes two different populations: against the bundled sample data that
produces an open rate of 126%, which is visibly wrong; against real data it would produce something
merely plausible and still wrong.

Every headline tile names its basis, e.g. *"Basis: Email, WhatsApp. Excluded (does not measure both
stages): Learning Management, MS Teams webinar, Viva Engage."*

## Persons versus events

Every value is tagged as counting **persons** or **events**.

Viva Engage engagement is events: one person can react, comment and share the same post, so the
count can exceed the community size. Event-unit values are displayed, but they are excluded from
person-based funnel arithmetic and can never become the numerator over an audience size. The rate
function refuses the division outright rather than returning a confident-looking number.

## Aggregation policy

Rolled-up recipient counts are summed across periods — they are already distinct people per period.

Viva Engage is different, and it is the reason the policy exists. Its export repeats the community's
audience size on every post, and reports unique viewers *per post*. Summing either would count the
same people once per post and claim a reach several times the size of the workforce. So:

- **Targeted** takes the largest audience seen, not the sum.
- **Opened** takes the best single post's unique viewers — a **lower bound** on true reach, because
  post-level data cannot say whether the same person saw two posts.
- **Engaged** sums, because interactions genuinely add up.

## The combined funnel: two bases

| Basis | What it counts | Availability |
|---|---|---|
| **Sum of channels** (default) | Message-level. Someone reached by email *and* WhatsApp counts twice. | Always. |
| **Deduplicated by employee** | People-level. Distinct employees across channels. | Only when at least two channels match the employee roster at `rules.dedupe_min_resolution` (default 80%) or better. Aggregate-only channels are always excluded. |

When deduplication is unavailable the option is disabled and the reason is shown, naming the channels
and their match rates. The app never silently sums and calls it reach.

## Measurement caveats

These belong next to the numbers, and the UI puts them there.

- **Email open rate is unreliable.** Apple Mail Privacy Protection pre-fetches tracking pixels
  (inflating opens); Outlook image blocking suppresses them (deflating opens); corporate link
  scanners click every URL (inflating clicks). Directional only.
- **WhatsApp read receipts can be switched off** by the recipient, understating reads invisibly.
- **Teams "registered" is mapped to Opened**, because registration is an act of intent rather than
  passive consumption. This preserves a four-step ladder (invited → registered → attended →
  qualified) instead of discarding the data. Configurable in one line of `stage_ladder.yaml`.
- **Teams "delivered" is deliberately n/a.** An attendance report carries no invitation telemetry —
  whether the invite arrived belongs to the email channel — so delivery rate renders n/a rather than
  a misleading 100%.
- **Teams completion is a dwell-time ratio**, `attendance_minutes / session_minutes >=
  rules.webinar_completion_fraction` (default 50%). Two minutes of a sixty-minute town hall is not
  attendance.
- **LMS completion requires passing** the assessment where one was recorded. A course marked complete
  with a failed assessment has not achieved the campaign's objective.
- **The trend line tracks campaign launches.** Every stage for a person is attributed to the period
  the campaign reached them, so all five stages of a campaign share one denominator. That keeps the
  funnel internally consistent at the cost of a trend that reads as campaign activity rather than
  trickling engagement.

## Targets and RAG

Organisation defaults live in `thresholds` in `config/settings.yaml`. A campaign registry can
override completion and engagement targets per campaign — a mandatory compliance course and an
optional wellbeing post should not be judged against the same bar.

- **Green** at or above target
- **Amber** at or above `target × amber_factor`
- **Red** below that
- **Grey** where there is no target, or no number to judge

Status is always shown with a symbol and a word as well as a colour, because red/amber/green is
precisely the case where colour-vision deficiency loses the message.
