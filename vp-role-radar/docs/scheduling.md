# Scheduling

## The default

`.github/workflows/vp-role-radar.yml` runs `37 2 * * 1-5` — **02:37 UTC, Monday
to Friday, which is 08:07 IST**.

Two deliberate choices in that line:

- **The minute is not `00`.** GitHub's scheduler queues heavily on the hour,
  and a job asked for at `:00` routinely starts ten or fifteen minutes late.
- **The days are `1-5`.** A job search is a weekday activity, and postings
  published over a weekend are still caught: the scan looks back seven days.

Cron on GitHub is always UTC. IST is UTC+5:30, so a run wanted at *HH:MM* IST
is *HH-5:MM-30* UTC — and if that subtraction crosses midnight, the day field
moves back a day too. It does not here: 08:07 IST is comfortably inside the
same UTC day.

## Changing the time

Edit the `cron:` line. Some worked examples:

| Wanted (IST) | Cron (UTC) |
|---|---|
| 08:07, weekdays (default) | `37 2 * * 1-5` |
| 07:00, weekdays | `30 1 * * 1-5` |
| 09:15, every day | `45 3 * * *` |
| 08:07, Mondays only | `37 2 * * 1` |
| Twice daily, 08:07 and 18:07 | `37 2,12 * * 1-5` |

If you move the Action, move the Claude Routine's cron with it — it should stay
about thirty minutes behind, so it reads a digest that has actually been
published. See [claude-routine.md](claude-routine.md).

## The 60-day pause

GitHub disables a scheduled workflow after 60 days with no commits to the
repository's **default** branch. This radar pushes to its own `radar-state`
branch, which does not count. Either commit something to `main` every couple of
months, or re-enable the workflow from the Actions tab when GitHub emails to
say it has been paused.

## Running it somewhere else

Nothing here is GitHub-specific. On any machine with outbound access:

```bash
cp .env.example .env      # fill in GMAIL_ADDRESS and GMAIL_APP_PASSWORD
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh                  # same as: python -m radar run
```

and a crontab line, if the host runs on IST:

```
7 8 * * 1-5  /path/to/vp-role-radar/run.sh >> /var/log/vp-role-radar.log 2>&1
```

The seen-store then lives in `state/seen.json` on that machine rather than in
the Actions cache, which is simpler — back it up and the radar keeps its memory.
