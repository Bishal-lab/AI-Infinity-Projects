# Operations

## Routine: the monthly reporting cycle

1. Export from each platform for the period.
2. Copy the files into `data/inbox/`.
3. Run the load — scheduled, or from the **Data & admin** page:
   ```bash
   python -m comms_dashboard.ingest.cli load
   ```
4. Check the load report for warnings. Anything rejected needs attention before the numbers are
   trusted.
5. Build the management pack from the **Executive overview** page.

Processed files move to `data/archive/YYYY-MM/`; rejected ones to `data/rejected/` with the reason
recorded in the load ledger.

## Scheduling

Prefer the CLI over the UI for production loads. DuckDB permits a single writer, so a scheduled load
does not compete with someone browsing for the write lock.

**cron** (weekdays at 06:00):

```cron
0 6 * * 1-5 cd /opt/comms-dashboard && .venv/bin/python -m comms_dashboard.ingest.cli load >> /var/log/comms-dashboard.log 2>&1
```

**Windows Task Scheduler**: run `run.bat`'s virtual environment directly —
`C:\comms-dashboard\.venv\Scripts\python.exe -m comms_dashboard.ingest.cli load`.

**Docker**: uncomment the `loader` service in `docker/docker-compose.yml`.

## The single-writer rule

One process may hold the warehouse file, and DuckDB's lock excludes **readers as well as writers**.
In practice:

- The app keeps one cached connection and serialises writes through a lock.
- While the dashboard is running, a second process cannot open the file at all. Read-only does not
  help — `duckdb -readonly` and `duckdb.connect(read_only=True)` are both refused with
  `IO Error: Could not set lock on file`.
- To query or export from a shell while the app is up, either stop the app, or work on a copy:
  ```bash
  cp data/warehouse/comms.duckdb /tmp/snapshot.duckdb
  duckdb /tmp/snapshot.duckdb
  ```
- This bites someone eventually; it is not a fault. It is also why scheduled loads should run from
  the CLI on a schedule the browsing window does not overlap, rather than from the UI.

## Checking the state

```bash
python -m comms_dashboard.ingest.cli status
```

Prints row counts and the funnel by channel, including which stages are n/a. The **Data & admin →
Warehouse** tab shows the same thing plus the stage-support matrix and the database file size.

## Re-loading a file

A byte-identical file is skipped on a second drop. To force a re-import, use **Data & admin → Load
history → Supersede this batch**, then drop the file in again. The row-level merge still prevents
double counting, so this is safe.

## Correcting bad data

Because the merge is monotonic — a known positive never regresses — a re-export cannot *remove* an
engagement that was recorded in error. To genuinely correct a campaign, delete its rows and reload:

```sql
-- with the app stopped
DELETE FROM fact_recipient_stage WHERE campaign_id = 'the-campaign' AND channel = 'email';
DELETE FROM fact_stage_metric   WHERE campaign_id = 'the-campaign' AND channel = 'email';
```

Then drop the corrected export into the inbox. Rollups are recomputed on load, so they will be
consistent with whatever recipient rows remain.

## Retention

Archived exports contain real identifiers in plaintext. `privacy.retention_months` (default 24)
records the intended policy; enforce it:

```bash
find data/archive -type f -mtime +730 -delete
```

Give `data/archive/` the same access controls as an HR file share.

## Backup

The warehouse is a single file. Stop the app (or use a read-only connection) and copy
`data/warehouse/comms.duckdb`. Back up `config/secret_salt` **separately and securely**: losing it
breaks every existing identity join, and leaking it makes the hashed identifiers reversible.

Copy the salt in a way that preserves its mode (`cp -p`, `install -m 600`, `tar -p`) and check it
after restoring — a plain `cp` gives the copy your umask, typically `0644`:

```bash
install -m 600 config/secret_salt /secure/backup/secret_salt
ls -l config/secret_salt          # expect -rw-------
```

The app narrows the file to `0600` on every read and warns if it cannot, so a restored copy is
corrected on the next start rather than silently left readable. It never rewrites the *contents*:
a changed salt would break every identity join already in the warehouse.

## Upgrading

`config/` and `data/` are the only stateful directories, and both are bind-mounted under Docker.
The DDL is idempotent and the stage-support matrix is rebuilt from `stage_ladder.yaml` on every
start, so editing the ladder takes effect on the next launch without a migration.

Adding a column to `fact_recipient_stage` does require a manual `ALTER TABLE` — there is no
migration framework, deliberately, given the single-file warehouse and the ease of a full reload
from `data/archive/`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "could not tell which platform this export came from" | Detection was not decisive. Pick the source on the admin page; it is remembered by file hash. To make it permanent, add a header to `header_fingerprint.strong` in the mapping file. |
| A stage shows "not measured" that you expect to have | The export did not carry a column matching any alias. The admin page's column-mapping table shows which fields found nothing; add your header to the `aliases` list. |
| Dates are a month out | `parsing.date_order` does not match the export. It is never inferred — set `DMY` or `MDY`. |
| Coverage page says no roster loaded | Load an HR extract. Without it, no channel identifier can be resolved to a department. |
| Deduplication is unavailable | Fewer than two channels match the roster at `rules.dedupe_min_resolution`. The **Audience & coverage** page shows the per-channel match rates. Add employee IDs or work emails to the weak exports. |
| Numbers did not change after a load | If loading from the UI this should not happen — the cache is invalidated on load. If it does, check the load report: the file may have been skipped as a duplicate. |
| Charts do not render | Confirm the app is served from its own host, not opened as a file. plotly.js comes from Streamlit's static assets; there is no CDN fallback by design. |
| `IO Error: Could not set lock on file` | Another process has the warehouse open for writing. Close it, or open read-only. |
