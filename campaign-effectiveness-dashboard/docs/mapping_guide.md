# Connecting your own exports

This is the only configuration job that matters, and it is a YAML edit. Nothing here needs a code
change.

## The five-minute version

1. Drop an export into `data/inbox/`.
2. Open **Data & admin** in the dashboard. The file appears with what the app thinks it is.
3. Expand it. The **Column mapping** table lists every canonical field and which of your columns it
   matched — `—` means no match.
4. For each `—` that matters, add your real column header to the `aliases` list in
   `config/mappings/<source>.yaml`.
5. Restart the app (config is cached at startup) and press **Load everything in the inbox**.

Alias matching ignores case, spaces, underscores and punctuation. `Unique Opens`, `unique_opens`,
`UNIQUE-OPENS ` and `uniqueOpens` all match the same alias, so you only need one spelling.

## Anatomy of a mapping file

```yaml
source: email                    # must be unique across all mapping files
mapping_version: "1.0"           # stamped on every load, so changes are traceable
display_name: Email campaign export
grain: recipient                 # recipient | aggregate | dimension

detect:
  priority: 10
  filename_glob: ["*email*", "*mailer*"]      # +2 if the filename matches
  header_fingerprint:
    strong: ["Recipient Email", "Delivery Status"]   # +3 each
    weak: ["Campaign Name", "Bounced"]               # +1 each
    exclude: ["Attendance Duration", "Course Name"]  # -5 each, kills false positives
  min_score: 4                   # below this, the app asks rather than guesses

read:
  sheet: 0                       # Excel sheet index or name
  header_row: 1                  # 1-based, as you see it in Excel
  encoding: auto                 # auto tries utf-8-sig, utf-8, cp1252, latin-1
  delimiter: auto
  na_values: ["", "-", "N/A", "NULL"]

keys:
  campaign:
    aliases: ["Campaign ID", "Campaign Name"]
    fallback_from_filename: 'email_(?P<campaign>[A-Za-z0-9._-]+?)_\d{4}-\d{2}'
    required: true
  recipient:
    aliases: ["Recipient Email", "Email Address"]
    id_type: email               # email | upn | msisdn | lms_user_id | employee_no
    canonicalise: [strip, lower]
    required: true

fields:
  delivered:
    aliases: ["Delivery Status", "Delivered"]
    type: bool
    required: true
    true_values: ["delivered", "success"]
    false_values: ["bounced", "failed"]

on_unknown_columns: warn         # warn | ignore | error
on_missing_optional: warn
on_missing_required: reject_file
```

## Field types

| Type | Accepts | Produces |
|---|---|---|
| `bool` | `Yes`/`No`, `TRUE`/`FALSE`, `1`/`0`, plus your `true_values` / `false_values` | true / false / **unknown** |
| `bool_from_count` | a number — `>0` is true, `0` is false | true / false / unknown |
| `number` | anything numeric, tolerating currency symbols and thousands separators | float |
| `percent` | `85%`, `85`, `0.85` | a 0–1 fraction |
| `duration_minutes` | `65`, `65 min`, `1h 05m`, `01:05:00`, `1.5 hours`, `PT1H5M` | float minutes |
| `datetime` / `date` | your `parsing.date_order` first, then Excel serial numbers | timestamp / date |
| `string` | anything | trimmed text |

A **blank cell always produces unknown, never `false`**. That distinction is the reason the funnel
can be trusted: a blank means the export did not tell us, and inventing a `false` there would
manufacture a negative result out of missing data.

A value the coercer cannot read is left blank, counted, and reported on the load report with up to
three examples — it never aborts the file unless the field is required.

## Canonical fields

The stage fields are the ones that drive the funnel. Their names are fixed:

| Field | Stage | Notes |
|---|---|---|
| `targeted` | TARGETED | usually `const: true` — the row existing means the person was targeted |
| `delivered` | DELIVERED | |
| `opened` | OPENED | |
| `engaged` | ENGAGED | |
| `completed` | COMPLETED | |

Supporting fields, all optional: `targeted_at`, `delivered_at`, `opened_at`, `engaged_at`,
`completed_at`, `bounced`, `opted_out`, `progress_pct`, `score`, `assessment_passed`,
`attendance_minutes`, `session_minutes`, `department`.

Some channels derive a stage rather than reading it — WhatsApp builds `opened` from the read-receipt
timestamp, Teams derives `completed` from the dwell-time ratio, LMS gates `completed` on the
assessment. Those live in `comms_dashboard/ingest/adapters/` and are the only Python a channel
normally needs.

## Campaign attribution

Resolution order:

1. A mapped `campaign` key column. Matched against the registry by campaign id **or** campaign name,
   case-insensitively — so a Teams "Meeting Title" of `Q1 Town Hall` resolves to `townhall-q1-2026`.
2. The `fallback_from_filename` regex, which needs a named `(?P<campaign>…)` group.
3. The admin picking a campaign on the Data & admin page before loading.

Teams attendance reports have no campaign identifier at all, which is why step 3 exists. A campaign
that resolves to nothing in the registry gets a placeholder entry, so the data is still usable, and
loading a registry later fills in the name, owner and targets in place.

## What happens when something is wrong

| Situation | Behaviour |
|---|---|
| Source cannot be determined | File is rejected; the admin page shows the score for every source and asks. The choice is remembered by file hash. |
| Required field missing | File is rejected, naming the field and the exact YAML path to fix. |
| Optional stage field missing | Loads. That stage becomes **not measured** for this campaign — reported as such, never as zero. |
| Unknown columns | Loads, ignoring them, and lists them so you can add any that matter. |
| Values that will not coerce | Left blank, counted, with examples on the load report. |
| The same file dropped twice | Skipped by content hash. |
| A re-export with extra rows | Loads and merges on `(campaign, channel, recipient)`. Known positives never regress; nothing is double-counted. |

## Adding a whole new channel

1. Add the channel to `config/stage_ladder.yaml` with an explicit entry for **every** stage —
   `measured`, `not_applicable` or `not_measured`, plus its unit. A missing entry is rejected at
   startup, because an omission is ambiguous between "n/a" and "we forgot".
2. Add `config/mappings/<channel>.yaml`.
3. Only if the channel needs a derivation that aliases cannot express, add
   `comms_dashboard/ingest/adapters/<channel>.py` subclassing `SourceAdapter` and overriding
   `post_process`. Register it with `@register_adapter`.

A mapping with no bespoke adapter still works — it gets the generic base, which is the point of the
YAML-driven design.

## Later: API connectors instead of file drops

Adapters take a DataFrame, not a path. A future Microsoft Graph or WhatsApp Business API puller only
has to produce a DataFrame and hand it to the same `parse()`; the mapping, validation, idempotency
and rollup logic are unchanged. That seam is deliberate — file drops are the day-one answer, not the
only possible one.
