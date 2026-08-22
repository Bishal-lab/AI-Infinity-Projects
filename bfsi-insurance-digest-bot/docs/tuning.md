# Tuning the brief

Everything below lives in `config/`. After any change:

```bash
python -m bot preview --explain
```

which prints the brief that would go out, then the stories that were passed over
with the score and the reason for each. That output is the feedback loop — the
scoring is deliberately transparent so it can be tuned on evidence rather than
guesswork.

## How a story is scored

1. **Exclusions.** Anything matching `exclude` in `topics.yaml` is dropped
   outright — cricket, cinema, horoscopes, the "gold rate today" filler that
   rides along on general business feeds.
2. **The domain gate.** The story must show it is about financial services, in
   one of three ways: it matched a `strong` keyword from a section marked
   `certifies_domain` (the default); it matched something in `domain_gate`; or
   it came from a specialist desk (a source whose `weight` is at or above
   `gate_bypass_weight`). A story matching only *supporting* words — "market",
   "app", "data" — does not get in.

   Three sections set `certifies_domain: false`: **Results, Deals & People**,
   **Markets & Macro** and **Technology & InsurTech**. Their keywords name a
   *kind of event* rather than this industry — "IPO", "appoints", "inflation",
   "AI" happen everywhere — so a story filed under them has to prove the domain
   some other way. Without this, the single word "IPO" admitted any small-cap
   listing on earth into a BFSI brief.

   The trade-off: those three sections are stricter than the other four. If one
   starts looking thin, either flip its `certifies_domain` back to `true`, or —
   better — add the specific term you are missing to `domain_gate`, which keeps
   the gate meaningful.
3. **Section scores.** Each section scores the story on its own keywords: a
   `strong` hit is worth `strong_weight` (3.0), a `supporting` hit
   `supporting_weight` (1.0), and a hit in the headline is worth
   `title_multiplier` (1.5×) a hit in the summary. Each distinct keyword counts
   once, at most `max_keywords_counted` (8) of them, and the subtotal is scaled
   by the section's `multiplier`.
4. **Routing.** The best-scoring section wins; ties go to whichever section is
   declared first. A source's `section_hint` adds `hint_bonus` to that section.
5. **Admission.** Best section score + the source's `weight` must reach
   `digest.min_score`.

Worked example — "IRDAI eases surrender value norms for life insurers" from
Business Standard (weight 0):

| Contribution | Value |
| --- | --- |
| `surrender value` — strong, in the headline | 3.0 × 1.5 = 4.5 |
| `life insurer` — strong, in the headline | 3.0 × 1.5 = 4.5 |
| `insurer` — supporting, in the headline | 1.0 × 1.5 = 1.5 |
| Life Insurance subtotal | (4.5 + 4.5 + 1.5) × 1.2 = **12.6** |
| Regulation, scoring the same story | `IRDAI` 4.5 × 1.1 = 4.95 |
| Winner | Life Insurance, 12.6 ≥ `min_score` 3.0 |

`preview --explain` prints exactly these numbers, so you never have to work one
out by hand — but knowing the arithmetic is what makes the knobs predictable.

## Common adjustments

**Too much noise.** Raise `digest.min_score` from 3.0 to 4.5 or 6.0. At 4.5 a
story needs a strong keyword in its headline; at 6.0 it needs two, or one plus a
specialist source.

**Too thin.** Lower `min_score` to 2.0, raise `digest.max_items_total`, or add
sources. Check `preview --explain` first — if the passed-over list is full of
things you *would* have wanted, the fix is keywords, not the threshold.

**Only life insurance, nothing else.** Set every other section's `multiplier` to
`0.1` rather than deleting them: they then act as a sink for off-topic stories
that would otherwise be forced into the Life Insurance bucket.

**One source dominates.** Lower `digest.max_items_per_source` to 2, or reduce
that source's `weight`.

**A company or theme you always want.** Add it to the relevant section's
`strong` list. Plurals are handled automatically ("life insurer" matches "life
insurers"), as are flexible spacing and hyphens ("non-par" matches "non par").

**A recurring story type you never want.** Add a distinctive phrase to
`exclude`. Be specific: `exclude` beats everything else, so a broad term there
will silently remove stories you wanted.

## Adding a source

Any public RSS or Atom feed works:

```yaml
- id: my-source            # stable slug; used by the seen-store and the caps
  name: My Source          # shown as the attribution
  url: https://example.com/feed.xml
  weight: 1.0              # relevance bonus for everything from here
  section_hint: regulation # optional nudge when the headline is ambiguous
  enabled: true
```

Then `python -m bot check-sources` to confirm it parses and is fresh.

For a topic rather than a publication, use a Google News search feed — this is
how IRDAI, which publishes no feed of its own, reaches the brief:

```
https://news.google.com/rss/search?q=YOUR+QUERY+when:2d&hl=en-IN&gl=IN&ceid=IN:en
```

Quote phrases as `%22like+this%22`, combine with `OR`, and keep `when:2d` so the
feed only returns the last two days.

## Timing and repeats

`digest.lookback_hours` (26) is intentionally wider than a day: feeds publish
late, and the overlap means a story filed at 07:55 IST is not lost between two
runs. Repeats are prevented by the seen-store, not by a narrow window — so
shortening the window to 24 hours buys nothing and risks a gap.

`state.retention_days` (14) is how long a story stays remembered. Two weeks
comfortably covers a follow-up story reappearing under a new URL.
