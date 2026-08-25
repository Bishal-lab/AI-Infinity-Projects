# The Claude-chat lane

The radar delivers to two places: Gmail, and a Claude session. This is the
second one — what it is, why it is built this way, and how to change it.

## Why it is split in two

The GitHub Actions runner has open outbound access and can read job boards. A
Claude session — including a scheduled one — generally cannot: the sandbox it
runs in blocks job-board hosts outright, which was verified while this project
was written (LinkedIn, Workday, Careerjet and iimjobs all answered `403` to the
proxy, for both a plain HTTP client and Claude's own fetch tool).

So the split follows the capability, not preference:

| Lane | Runs where | Does what |
|---|---|---|
| Data engine | GitHub Actions, 08:07 IST weekdays | Reads the sources, scores, dedupes, mails the digest, publishes it to the `radar-state` branch |
| Claude chat | A scheduled Claude Routine, 08:40 IST weekdays | Reads that published file and posts the brief into Claude, with a push notification |

The half-hour gap is deliberate: it gives the Action time to finish and push
before the Claude session goes looking for its output.

## The handover file

`vp-role-radar/state/latest-digest.md`, on the **`radar-state`** branch.

That branch is orphaned — it carries the digest and the seen-store, not a copy
of the source tree — so it is never something to merge. It is written on every
run, including empty ones, because a stale file would have Monday's brief
announce the previous week's roles as though they were new.

Read it the way the Routine does:

```bash
git fetch origin radar-state
git show origin/radar-state:vp-role-radar/state/latest-digest.md
```

## The Routine

Created with the `create_trigger` tool, `create_new_session_on_fire: true`,
cron `10 3 * * 1-5` (08:40 IST, Monday to Friday), push and email notifications
on. Its prompt is `prompts/routine-prompt.md` in this project, copied verbatim.

Editing what Claude says each morning is therefore a two-step change: edit
`prompts/routine-prompt.md`, then apply it with `update_trigger` (pass the
trigger id and the new prompt). Keeping the text in the repository means the
prompt can be reviewed and diffed rather than living only inside a Routine.

Useful operations, all through the same tool family:

- `list_triggers` — find the trigger id, and check `last_run` is `SUCCEEDED`
- `fire_trigger` — run it now, outside the schedule, to test a change
- `update_trigger` — change the prompt, the cron, or pause it (`enabled: false`)
- `delete_trigger` — remove it

## If the brief stops arriving

1. Check the Action first: Actions → **VP role radar** → the most recent run.
   The Claude lane can only be as good as the file the Action publishes.
2. Check the branch has a recent commit: `git log -1 origin/radar-state`.
3. Check the Routine ran: `list_triggers`, and look at `last_run`.

A Routine that fires against a stale file is designed to say so rather than
repeat itself — if the morning brief tells you the digest is more than three
days old, the problem is in the Action, not in Claude.
