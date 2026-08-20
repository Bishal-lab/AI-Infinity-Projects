# Profile: Agency — Ely 2.0

A worked example of connecting the dashboard to a real set of exports, kept as a reference for the
next platform onboarding. **No company data lives here** — only the campaign metadata, which
contains no personal information.

## The exports this profile was built against

| Source | Shape | What it could and could not measure |
|---|---|---|
| LMS (`Ely 2.0_Jun'26`) | 100 learners, one row each | Full funnel: assigned → started → completed & passed. Delivery is n/a (assignment is instant) |
| MS Teams attendance | 100 attendees, 25 Jun 2026 | **Attendance only.** No invite list and no registration column, so Targeted and Registered are *not measured* — see below |
| Viva Engage | 200 daily community rows, Jan–Jul 2026 | Reach and interactions. **No community-size column**, so no reach *rate* |

## Three things the real data taught us

**1. Date order is per platform, not per organisation.** The LMS exports `MM/DD/YYYY`
(`07/31/2026` settles it) while other tools here export `DD/MM/YYYY`. A single global setting would
have silently mis-parsed one of them — a value like `08-06-2026` read as DMY lands ten days *before*
the course was assigned. `config/mappings/lms.yaml` now carries `read.date_order: MDY`, which
overrides the global default for that source alone.

**2. An attendance report cannot tell you reach.** The plain Teams export lists the people who
turned up and nobody else. Counting each row as "targeted" would report the attendee count as the
audience and an attendance rate of exactly 100%. The adapter now detects a report with no
registration and no attended flag, and leaves both stages unknown. To measure them, export the
event's **registration report** as well.

**3. Daily analytics must not be summed.** Viva's "People reached" totals 22,993 across 200 days
against a maximum of 389 on any single day. Adding them would claim a reach nearly sixty times the
community. The aggregation policy takes the peak instead, and labels it a lower bound.

## Loading it

```bash
# 1. Campaign metadata first, so the exports have something to resolve against
cp profiles/agency-ely/campaign_registry.csv data/inbox/

# 2. The LMS and Viva exports carry their own campaign identifiers
cp /path/to/Ely_2.0_*.csv /path/to/Viva_*Community_Analytics.csv data/inbox/
python -m comms_dashboard.ingest.cli load

# 3. The Teams report has no campaign column, so assign one explicitly
python -m comms_dashboard.ingest.cli load \
    --file /path/to/Sample_Attendance_Report.xlsx \
    --source teams_webinar --campaign ely-2-0-jun-26
```

## Not available in this profile

No employee roster, and no shared person key: the LMS keys on numeric employee IDs while Teams uses
masked participant identifiers. Cross-channel deduplication and true workforce-coverage percentages
are therefore switched off, and the dashboard says so rather than approximating. Segments come from
the data itself — **Job Role** from the LMS, **City** parsed from the Teams participant names.

Supply an HR extract mapping employee IDs to the Teams identifiers and both features turn on with no
further change.
