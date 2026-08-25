# Tuning

Two files decide what reaches the inbox: `config/profile.yaml` (what counts as
a match) and `config/sources.yaml` (where openings come from). Neither needs a
code change, and both take effect on the next run.

Work with `preview` rather than by waiting for tomorrow's e-mail:

```bash
python -m radar preview --explain
```

`--explain` prints what was passed over and the one-line reason for each, which
is usually the fastest way to find out why something you expected is missing.

## The brief is too quiet

In rough order of what to try:

1. **Lower the floor.** `radar.min_fit` in `settings.yaml`, default `45`. It is
   the bottom of the Stretch tier; `40` widens noticeably.
2. **Widen the window.** `radar.lookback_days`, default `7`.
3. **Add a key.** This is usually the real answer. The boards carrying the most
   Indian and Gulf roles — Naukri, iimjobs, Bayt, GulfTalent — are reachable
   only through an aggregator, and all three aggregator adapters ship switched
   off for want of a free key. Set `CAREERJET_AFFID` first: it has the widest
   reach across both regions. See `.env.example`.
4. **Add employers.** Any insurer on Workday is four lines in `sources.yaml`,
   and the URL is the one from your browser's address bar.

## The brief is too noisy

1. **Raise the floor.** `min_fit: 55` shows only Possible and Strong; `70` only
   Strong.
2. **Exclude a function.** Add the word to `exclude.title` in `profile.yaml`.
   That list is checked against the job title only, on purpose: a genuine
   key-accounts role mentions underwriting and claims in its responsibilities
   all the time, and excluding on the body would throw those away.
3. **Tighten the caps.** `max_items_total`, `max_items_per_tier`,
   `max_items_per_source`.

## The scores look wrong

The fit score is 100 points across five dimensions, set in `profile.yaml`:

| Dimension | Points | Also a gate? |
|---|---:|---|
| Seniority | 30 | Yes — no VP or AVP marker in the title, no entry |
| Function | 30 | Yes — no `function.strong` keyword anywhere, no entry |
| Domain | 20 | No — non-life insurance scores at `adjacent_value` |
| Geography | 10 | Yes — outside the three regions, no entry |
| Edge | 10 | No |

So a role can only ever be missing because it failed a gate or scored below the
floor, and `--explain` says which.

Things worth knowing before you change them:

- **Seniority takes the most specific marker.** "Assistant Vice President"
  scores as AVP, not VP, because the longest matching keyword wins. Adding
  "vice president" to the AVP level would break that.
- **Title hits count double.** `scoring.title_multiplier`, default `2.0`.
- **Preferred cities score geography in full**, others at the region's `value`.
  Gurgaon, Delhi and the rest of NCR are preferred because that is home.
- **The experience floor is a gate, not a score.** A posting advertising a band
  topping out below `experience.floor_years` (default 10) is dropped whatever
  its title says. `15+ years` has no upper end, so it always passes.

## Changing what the search is for

If the target changes — a different function, a different industry, a different
part of the world — edit the dimension lists and the `queries` block at the
foot of `profile.yaml`. The queries feed both the aggregator adapters and the
generated saved-search links, so they only need saying once.

The one thing to keep in mind: `function.strong` and the geography regions are
gates. Emptying either does not widen the search, it breaks it — an empty
function gate would admit every senior role on earth, and the config loader
rejects that outright rather than let it happen quietly.
