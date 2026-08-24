# Scheduling the 08:00 IST run

The bot itself has no scheduler — it is a one-shot command. Something outside it
has to run `python -m bot run` once a day. Three ways, in order of how little
there is to maintain.

**08:00 IST is 02:30 UTC.** India does not observe daylight saving, so that
mapping never changes. Anything that runs on UTC and fires on time — most cloud
servers, most containers — needs `30 2 * * *`.

GitHub Actions is the exception, and it is deliberate: it fires late, so the
workflow asks for `30 1 * * *` instead. See below.

---

## 1. GitHub Actions

Already set up in `.github/workflows/bfsi-digest.yml`. Add the secrets listed in
the main README and the schedule takes care of itself.

Worth knowing:

- **Scheduled workflows stop after 60 days** of no commits to the default
  branch. GitHub e-mails a warning first; any commit re-arms them.
- **Scheduled runs are best-effort, and late.** GitHub queues them, can drop
  them under load, and does not fire on the minute. Both runs measured on this
  repo started ~60 minutes behind: asked 02:30 UTC, started 03:29:14 (23 Aug)
  and 03:30:49 (24 Aug). That is why the workflow asks for `30 1 * * *`
  (07:00 IST) rather than the arithmetically correct `30 2 * * *` — an hour of
  lead time puts 08:00 IST in the middle of the window rather than at its
  earliest edge. Re-measure before changing it; if the delay shifts, move the
  cron rather than the expectation. A skipped morning is not lost either way:
  the 26-hour window picks it up the next day.
- **The runner is ephemeral**, so `state/seen.json` is carried between runs in
  the Actions cache. An evicted cache costs one repeated story, nothing more.
- **Check the run.** The job fails loudly on a delivery error, and GitHub
  e-mails you about failed scheduled workflows.

## 2. cron

```cron
# Host clock on UTC
30 2 * * * /path/to/bfsi-insurance-digest-bot/run.sh >> /var/log/bfsi-digest.log 2>&1

# Host clock on IST
0 8 * * * /path/to/bfsi-insurance-digest-bot/run.sh >> /var/log/bfsi-digest.log 2>&1
```

Check which one you have with `timedatectl` or `date`.

`run.sh` changes into its own directory, prefers `.venv/bin/python` if present,
and forwards any arguments — so `run.sh check-sources` works from cron too.
Credentials come from the `.env` file beside it, because cron jobs inherit a
famously bare environment.

If the machine may be asleep at 02:30, cron simply skips the run. Use a systemd
timer instead.

## 3. systemd timer

Catches up a missed run after a reboot or a suspend, which plain cron will not.

`/etc/systemd/system/bfsi-digest.service`:

```ini
[Unit]
Description=BFSI & life insurance daily brief
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=youruser
WorkingDirectory=/path/to/bfsi-insurance-digest-bot
ExecStart=/path/to/bfsi-insurance-digest-bot/run.sh
# Feeds are occasionally slow to wake up; give the run two more chances.
Restart=on-failure
RestartSec=15min
```

`/etc/systemd/system/bfsi-digest.timer`:

```ini
[Unit]
Description=Run the BFSI brief at 08:00 IST

[Timer]
OnCalendar=*-*-* 08:00:00 Asia/Kolkata
# Run as soon as possible after a missed trigger (machine off, suspended).
Persistent=true
# Spread the load on shared hosts; drop this if you want it exactly on time.
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bfsi-digest.timer
systemctl list-timers bfsi-digest.timer     # when it will next fire
journalctl -u bfsi-digest.service -n 50     # what happened last time
```

`OnCalendar` takes a timezone directly, so no UTC arithmetic here.

## 4. Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# The seen-store must outlive the container: mount a volume at /app/state.
VOLUME ["/app/state"]
ENTRYPOINT ["python", "-m", "bot"]
CMD ["run"]
```

```bash
docker build -t bfsi-digest .
docker run --rm --env-file .env -v bfsi-state:/app/state bfsi-digest run
```

Then schedule that `docker run` with cron or a systemd timer as above. Mount the
volume — without it every run starts with an empty seen-store and repeats
stories.

---

## Verifying the schedule

Do not wait until tomorrow morning to find out:

```bash
python -m bot check-sources    # feeds reachable?
python -m bot test-delivery --send   # credentials good, test message received?
python -m bot run              # a real brief, right now
```

On GitHub Actions, **Actions → BFSI digest → Run workflow** does the same
through the `mode` dropdown.
