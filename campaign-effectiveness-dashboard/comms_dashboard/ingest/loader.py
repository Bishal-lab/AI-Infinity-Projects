"""Loading parsed files into the warehouse.

Idempotency is enforced in two independent layers, because either one alone has
a hole:

1. **File ledger.** The SHA-256 of the file's bytes. A byte-identical re-drop is
   skipped outright. This misses the case where somebody re-exports the same
   period with a few extra rows.
2. **Natural-key upsert with a monotonic merge.** Rows key on
   ``(campaign_id, channel, recipient_key)`` and merge so that a *known* result
   never regresses: a recorded open stays open even if a later, thinner export
   is silent about it; first-event timestamps take the earliest; progress and
   dwell take the highest. That is what makes a superset re-export safe.

Rolled-up stage metrics are **recomputed from scratch** for every affected
campaign and channel after each load, never incremented. Derived numbers that
accumulate drift; derived numbers that are recomputed cannot.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from ..config import Settings, StageLadder, get_settings, get_stage_ladder
from ..db import next_batch_id, write_lock
from ..models import (
    STAGE_COLUMNS,
    IngestError,
    LoadReport,
    MeasureUnit,
    SupportStatus,
)
from .base import LoadContext, SourceAdapter
from .identity import ID_TYPE_CANONICALISATION, ROSTER_IDENTITY_COLUMNS, make_key
from .reader import file_sha256, is_supported, read_file
from .registry import build_adapters, detect_source

#: Recipient-grain columns carried through to the fact table.
_PASSTHROUGH_COLUMNS = (
    "bounced",
    "opted_out",
    "progress_pct",
    "score",
    "assessment_passed",
    "attendance_minutes",
    "session_minutes",
    "department",
)
_TIMESTAMP_COLUMNS = (
    "targeted_at",
    "delivered_at",
    "opened_at",
    "engaged_at",
    "completed_at",
)
_MAX_MERGE_COLUMNS = ("progress_pct", "score", "attendance_minutes", "session_minutes")

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    return _SLUG.sub("-", str(value).strip().lower()).strip("-") or "unknown"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def ingest_file(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    *,
    source_override: str | None = None,
    campaign_override: str | None = None,
    force: bool = False,
    settings: Settings | None = None,
    ladder: StageLadder | None = None,
    archive: bool = True,
) -> LoadReport:
    """Ingest one export file. Returns a report whether it succeeded or not."""
    settings = settings or get_settings()
    ladder = ladder or get_stage_ladder()
    started = time.perf_counter()
    report = LoadReport(file_name=path.name)

    if not path.exists():
        report.status = "rejected"
        report.add_issue("error", "FILE_NOT_FOUND", f"{path} does not exist")
        return report
    if not is_supported(path):
        report.status = "rejected"
        report.add_issue(
            "error", "UNSUPPORTED_FILE_TYPE", f"{path.suffix!r} is not a CSV or Excel file"
        )
        return report

    report.file_sha256 = file_sha256(path)

    # Layer 1: has this exact file already been loaded?
    if not force and _already_loaded(con, report.file_sha256):
        report.status = "duplicate"
        report.add_issue(
            "info",
            "DUPLICATE_FILE",
            "this file has already been loaded (identical contents). Nothing was "
            "changed. Use 'Force reload' if you meant to re-import it.",
        )
        _finish(con, report, started, settings, archive_path=path if archive else None)
        return report

    source = source_override or _stored_override(con, report.file_sha256)
    adapters = build_adapters()

    if source is None:
        detection = detect_source(path, adapters)
        source = detection.source
        report.detection_confidence = detection.best.score if detection.best else 0.0
        report.detection_runner_up = (
            detection.runner_up.source if detection.runner_up else None
        )
        if source is None:
            report.status = "rejected"
            report.add_issue(
                "error",
                "SOURCE_UNDETECTED",
                f"could not tell which platform this export came from: "
                f"{detection.reason}. Pick the source on the Data & admin page, or "
                f"add a header fingerprint to the mapping file.",
                example_values=tuple(detection.probe.headers[:6]),
            )
            _finish(con, report, started, settings, archive_path=path if archive else None,
                    reject=True)
            return report

    if source not in adapters:
        report.status = "rejected"
        report.add_issue("error", "UNKNOWN_SOURCE", f"no mapping configured for {source!r}")
        _finish(con, report, started, settings, archive_path=path if archive else None,
                reject=True)
        return report

    adapter = adapters[source]
    report.source = source
    report.mapping_version = adapter.mapping.mapping_version

    try:
        df = read_file(path, adapter.mapping.read)
    except IngestError as exc:
        report.status = "rejected"
        report.add_issue("error", "READ_FAILED", str(exc))
        _finish(con, report, started, settings, archive_path=path if archive else None,
                reject=True)
        return report

    report.rows_read = len(df)
    ctx = LoadContext(
        settings=settings,
        ladder=ladder,
        mapping=adapter.mapping,
        path=path,
        report=report,
        campaign_override=campaign_override,
    )
    parsed = adapter.parse(df, ctx)

    if report.has_errors:
        report.status = "rejected"
        _finish(con, report, started, settings, archive_path=path if archive else None,
                reject=True)
        return report

    batch_id = next_batch_id(con)
    with write_lock:
        try:
            if adapter.grain == "dimension":
                _load_dimension(con, adapter, parsed, ctx, batch_id)
            elif adapter.grain == "aggregate":
                _load_aggregate(con, adapter, parsed, ctx, batch_id)
            else:
                _load_recipient(con, adapter, parsed, ctx, batch_id)
        except duckdb.Error as exc:  # pragma: no cover - defensive
            report.status = "rejected"
            report.add_issue("error", "DB_ERROR", str(exc))
            _finish(con, report, started, settings,
                    archive_path=path if archive else None, reject=True)
            return report

    report.status = "loaded_with_warnings" if report.has_warnings else "loaded"
    _finish(
        con,
        report,
        started,
        settings,
        batch_id=batch_id,
        archive_path=path if archive else None,
    )
    return report


def ingest_inbox(
    con: duckdb.DuckDBPyConnection,
    *,
    settings: Settings | None = None,
    archive: bool = True,
) -> list[LoadReport]:
    """Ingest everything in the inbox, dimensions first.

    Order matters: the roster and campaign registry are what channel exports
    resolve their people and campaigns against, so they go first even if they
    were dropped last.
    """
    settings = settings or get_settings()
    inbox = settings.path("inbox")
    inbox.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in inbox.iterdir() if p.is_file() and is_supported(p))

    def priority(p: Path) -> int:
        name = p.name.lower()
        if "roster" in name or "employee" in name:
            return 0
        if "campaign" in name and "registry" in name:
            return 1
        return 2

    reports = [
        ingest_file(con, path, settings=settings, archive=archive)
        for path in sorted(files, key=priority)
    ]
    if reports:
        resolve_identities(con)
    return reports


# --------------------------------------------------------------------------- #
# Dimension loads
# --------------------------------------------------------------------------- #


def _load_dimension(
    con: duckdb.DuckDBPyConnection,
    adapter: SourceAdapter,
    parsed,
    ctx: LoadContext,
    batch_id: int,
) -> None:
    if adapter.source == "roster":
        _load_roster(con, parsed, ctx, batch_id)
    elif adapter.source == "campaign_registry":
        _load_campaign_registry(con, parsed, ctx, batch_id)
    else:  # pragma: no cover - no other dimension sources today
        raise IngestError(f"no dimension loader for {adapter.source!r}")


def _load_roster(con: duckdb.DuckDBPyConnection, parsed, ctx: LoadContext, batch_id: int) -> None:
    df = parsed.canonical.copy()
    if "recipient_key" not in df.columns:
        raise IngestError("roster: no employee key column resolved")
    df = df[df["recipient_key"].notna()].copy()
    df = df.rename(columns={"recipient_key": "employee_key"})

    employees = pd.DataFrame(
        {
            "employee_key": df["employee_key"],
            "department": df.get("department"),
            "sub_department": df.get("sub_department"),
            "region": df.get("region"),
            "country": df.get("country"),
            "site": df.get("site"),
            "grade_band": df.get("grade_band"),
            "employment_type": df.get("employment_type"),
            "is_people_manager": df.get("is_people_manager"),
            "is_active": df.get("is_active", True),
            "batch_id": batch_id,
        }
    ).drop_duplicates(subset=["employee_key"], keep="last")
    for column in ("department", "sub_department", "region", "country", "site",
                   "grade_band", "employment_type", "is_people_manager"):
        if column not in employees or employees[column] is None:
            employees[column] = None

    con.register("_roster_rows", employees)
    con.execute(
        """
        INSERT INTO dim_employee
            (employee_key, department, sub_department, region, country, site,
             grade_band, employment_type, is_people_manager, is_active, batch_id, updated_at)
        SELECT employee_key, department, sub_department, region, country, site,
               grade_band, employment_type, is_people_manager,
               COALESCE(is_active, TRUE), batch_id, now()
        FROM _roster_rows
        ON CONFLICT (employee_key) DO UPDATE SET
            department        = excluded.department,
            sub_department    = excluded.sub_department,
            region            = excluded.region,
            country           = excluded.country,
            site              = excluded.site,
            grade_band        = excluded.grade_band,
            employment_type   = excluded.employment_type,
            is_people_manager = excluded.is_people_manager,
            is_active         = excluded.is_active,
            batch_id          = excluded.batch_id,
            updated_at        = now()
        """
    )
    con.unregister("_roster_rows")

    # The join spine: one row per (identifier, type) so any channel's native id
    # can find its way back to an employee.
    salt = ctx.settings.salt() if ctx.settings.hash_identifiers else None
    identity_rows: list[dict] = []
    for column, id_type in ROSTER_IDENTITY_COLUMNS.items():
        if column not in df.columns:
            continue
        rules = ID_TYPE_CANONICALISATION.get(id_type, ("strip", "lower"))
        for employee_key, raw in zip(df["employee_key"], df[column]):
            key = make_key(
                raw,
                rules,
                salt=salt,
                hash_enabled=ctx.settings.hash_identifiers,
                country_code=ctx.settings.default_country_code,
            )
            if key:
                identity_rows.append(
                    {"id_hash": key, "id_type": id_type, "employee_key": employee_key}
                )
    # The employee's own id also resolves, so an export keyed on employee number
    # works without any of the contact columns.
    for employee_key in df["employee_key"]:
        identity_rows.append(
            {"id_hash": employee_key, "id_type": "employee_no", "employee_key": employee_key}
        )

    if identity_rows:
        identities = pd.DataFrame(identity_rows).drop_duplicates(
            subset=["id_hash", "id_type"], keep="last"
        )
        con.register("_identity_rows", identities)
        con.execute(
            """
            INSERT INTO dim_employee_identity (id_hash, id_type, employee_key)
            SELECT id_hash, id_type, employee_key FROM _identity_rows
            ON CONFLICT (id_hash, id_type) DO UPDATE SET
                employee_key = excluded.employee_key
            """
        )
        con.unregister("_identity_rows")

    ctx.report.rows_loaded = len(employees)


def _load_campaign_registry(
    con: duckdb.DuckDBPyConnection, parsed, ctx: LoadContext, batch_id: int
) -> None:
    df = parsed.canonical.copy()
    if parsed.campaign_raw is None:
        raise IngestError("campaign registry: no campaign id column resolved")
    df["campaign_id"] = parsed.campaign_raw.map(lambda v: slugify(v) if v else None)
    df = df[df["campaign_id"].notna()]

    rows = pd.DataFrame(
        {
            "campaign_id": df["campaign_id"],
            "campaign_name": df.get("campaign_name", df["campaign_id"]),
            "programme": df.get("programme"),
            "objective": df.get("objective"),
            "owner_team": df.get("owner_team"),
            "start_date": df.get("start_date"),
            "end_date": df.get("end_date"),
            "mandatory": df.get("mandatory", False),
            "target_population": df.get("target_population"),
            "target_completion_rate": df.get("target_completion_rate"),
            "target_engagement_rate": df.get("target_engagement_rate"),
        }
    ).drop_duplicates(subset=["campaign_id"], keep="last")
    for column in ("programme", "objective", "owner_team", "start_date", "end_date",
                   "target_population", "target_completion_rate", "target_engagement_rate"):
        if column not in rows:
            rows[column] = None

    con.register("_campaign_rows", rows)
    con.execute(
        """
        INSERT INTO dim_campaign
            (campaign_id, campaign_name, programme, objective, owner_team,
             start_date, end_date, mandatory, target_population,
             target_completion_rate, target_engagement_rate, updated_at)
        SELECT campaign_id, campaign_name, programme, objective, owner_team,
               try_cast(start_date AS DATE), try_cast(end_date AS DATE),
               COALESCE(mandatory, FALSE), try_cast(target_population AS INTEGER),
               target_completion_rate, target_engagement_rate, now()
        FROM _campaign_rows
        ON CONFLICT (campaign_id) DO UPDATE SET
            campaign_name          = excluded.campaign_name,
            programme              = excluded.programme,
            objective              = excluded.objective,
            owner_team             = excluded.owner_team,
            start_date             = excluded.start_date,
            end_date               = excluded.end_date,
            mandatory              = excluded.mandatory,
            target_population      = excluded.target_population,
            target_completion_rate = excluded.target_completion_rate,
            target_engagement_rate = excluded.target_engagement_rate,
            updated_at             = now()
        """
    )
    con.unregister("_campaign_rows")
    ctx.report.rows_loaded = len(rows)


# --------------------------------------------------------------------------- #
# Campaign attribution
# --------------------------------------------------------------------------- #


def _campaign_lookup(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """Map both campaign ids and campaign names (lowercased) to the canonical id.

    Teams attendance reports name the *meeting*, not the campaign — resolving on
    name is what lets "Q1 Town Hall" land on ``townhall-q1-2026``.
    """
    rows = con.execute("SELECT campaign_id, campaign_name FROM dim_campaign").fetchall()
    lookup: dict[str, str] = {}
    for campaign_id, name in rows:
        lookup[str(campaign_id).strip().lower()] = campaign_id
        lookup[slugify(str(campaign_id))] = campaign_id
        if name:
            lookup[str(name).strip().lower()] = campaign_id
            lookup[slugify(str(name))] = campaign_id
    return lookup


def _resolve_campaign_ids(
    con: duckdb.DuckDBPyConnection, raw: pd.Series, ctx: LoadContext
) -> pd.Series:
    lookup = _campaign_lookup(con)
    unknown: set[str] = set()

    def resolve(value: object) -> str | None:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        text = str(value).strip()
        if not text:
            return None
        hit = lookup.get(text.lower()) or lookup.get(slugify(text))
        if hit:
            return hit
        unknown.add(text)
        return slugify(text)

    resolved = raw.map(resolve)
    if unknown:
        # A stub keeps the data usable; the registry can be loaded later and will
        # fill in the name, objective and targets in place.
        _create_stub_campaigns(con, unknown)
        ctx.report.add_issue(
            "warning",
            "CAMPAIGN_NOT_IN_REGISTRY",
            f"{len(unknown)} campaign(s) in this file are not in the campaign "
            f"registry; placeholder entries were created. Load a campaign registry "
            f"to give them names, owners and targets.",
            example_values=tuple(sorted(unknown)[:3]),
            occurrence_count=len(unknown),
        )
    return resolved


def _create_stub_campaigns(con: duckdb.DuckDBPyConnection, names: set[str]) -> None:
    rows = pd.DataFrame(
        [{"campaign_id": slugify(n), "campaign_name": n} for n in sorted(names)]
    ).drop_duplicates(subset=["campaign_id"])
    con.register("_stub_campaigns", rows)
    con.execute(
        """
        INSERT INTO dim_campaign (campaign_id, campaign_name)
        SELECT campaign_id, campaign_name FROM _stub_campaigns
        ON CONFLICT (campaign_id) DO NOTHING
        """
    )
    con.unregister("_stub_campaigns")


# --------------------------------------------------------------------------- #
# Recipient-grain loads
# --------------------------------------------------------------------------- #


def _merge_within_batch(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate natural keys inside one file.

    Needed before the upsert: DuckDB refuses to update the same row twice in a
    single statement, and a re-export that repeats a recipient is common.
    """
    keys = ["campaign_id", "channel", "recipient_key"]
    if not df.duplicated(subset=keys).any():
        return df

    agg: dict[str, str] = {}
    for column in df.columns:
        if column in keys:
            continue
        if column in STAGE_COLUMNS.values() or column in ("bounced", "opted_out",
                                                          "assessment_passed"):
            agg[column] = "max"  # True beats False beats unknown
        elif column in _TIMESTAMP_COLUMNS:
            agg[column] = "min"  # earliest occurrence
        elif column in _MAX_MERGE_COLUMNS:
            agg[column] = "max"
        else:
            agg[column] = "last"
    return df.groupby(keys, as_index=False, dropna=False).agg(agg)


def _load_recipient(
    con: duckdb.DuckDBPyConnection,
    adapter: SourceAdapter,
    parsed,
    ctx: LoadContext,
    batch_id: int,
) -> None:
    canonical = parsed.canonical.copy()
    if "recipient_key" not in canonical.columns:
        raise IngestError(f"{adapter.source}: no recipient key resolved")
    if parsed.campaign_raw is None:
        raise IngestError(f"{adapter.source}: no campaign resolved")

    canonical["campaign_id"] = _resolve_campaign_ids(con, parsed.campaign_raw, ctx)
    canonical["channel"] = adapter.source

    before = len(canonical)
    canonical = canonical[
        canonical["recipient_key"].notna() & canonical["campaign_id"].notna()
    ].copy()
    ctx.report.rows_rejected = before - len(canonical)
    if canonical.empty:
        ctx.report.add_issue("error", "NO_USABLE_ROWS", "no rows had both a campaign and a recipient")
        raise IngestError("no usable rows")

    statuses = adapter.stage_availability(canonical, ctx)

    frame = pd.DataFrame(
        {
            "campaign_id": canonical["campaign_id"],
            "channel": canonical["channel"],
            "recipient_key": canonical["recipient_key"],
            "id_type": canonical.get("id_type"),
        }
    )
    for stage, column in STAGE_COLUMNS.items():
        frame[column] = (
            canonical[column] if column in canonical.columns else None
        )
    for column in _TIMESTAMP_COLUMNS + _PASSTHROUGH_COLUMNS:
        frame[column] = canonical[column] if column in canonical.columns else None
    frame["batch_id"] = batch_id

    frame = _merge_within_batch(frame)
    _upsert_recipient_rows(con, frame)

    affected = sorted(frame["campaign_id"].dropna().unique())
    _recompute_max_stage(con, affected, adapter.source)
    _recompute_rollups(con, affected, adapter.source, statuses, ctx, batch_id)

    ctx.report.rows_loaded = len(frame)
    ctx.report.campaign_id = affected[0] if len(affected) == 1 else None
    _record_date_range(ctx, frame)


def _upsert_recipient_rows(con: duckdb.DuckDBPyConnection, frame: pd.DataFrame) -> None:
    """Insert or monotonically merge recipient rows.

    The merge rule for a stage flag is "known TRUE wins, then known FALSE, then
    unknown". A later export that is silent about an open must not erase an open
    we already recorded — that is exactly how re-dropping a trimmed export would
    otherwise quietly delete engagement.
    """
    con.register("_recipient_rows", frame)

    def bool_merge(column: str) -> str:
        return (
            f"{column} = CASE "
            f"WHEN excluded.{column} IS TRUE OR fact_recipient_stage.{column} IS TRUE THEN TRUE "
            f"WHEN excluded.{column} IS FALSE OR fact_recipient_stage.{column} IS FALSE THEN FALSE "
            f"ELSE NULL END"
        )

    def earliest(column: str) -> str:
        return (
            f"{column} = LEAST("
            f"COALESCE(excluded.{column}, fact_recipient_stage.{column}), "
            f"COALESCE(fact_recipient_stage.{column}, excluded.{column}))"
        )

    def highest(column: str) -> str:
        return (
            f"{column} = GREATEST("
            f"COALESCE(excluded.{column}, fact_recipient_stage.{column}), "
            f"COALESCE(fact_recipient_stage.{column}, excluded.{column}))"
        )

    updates = [bool_merge(c) for c in STAGE_COLUMNS.values()]
    updates += [bool_merge(c) for c in ("bounced", "opted_out", "assessment_passed")]
    updates += [earliest(c) for c in _TIMESTAMP_COLUMNS]
    updates += [highest(c) for c in _MAX_MERGE_COLUMNS]
    updates += [
        "department = COALESCE(excluded.department, fact_recipient_stage.department)",
        "batch_id = excluded.batch_id",
        "updated_at = now()",
    ]

    columns = (
        ["campaign_id", "channel", "recipient_key"]
        + list(STAGE_COLUMNS.values())
        + list(_TIMESTAMP_COLUMNS)
        + ["bounced", "opted_out", "progress_pct", "score", "assessment_passed",
           "attendance_minutes", "session_minutes", "department", "batch_id"]
    )
    select_list = ", ".join(
        f"try_cast({c} AS TIMESTAMP) AS {c}" if c in _TIMESTAMP_COLUMNS else c
        for c in columns
    )

    con.execute(
        f"""
        INSERT INTO fact_recipient_stage ({", ".join(columns)})
        SELECT {select_list} FROM _recipient_rows
        ON CONFLICT (campaign_id, channel, recipient_key) DO UPDATE SET
            {", ".join(updates)}
        """
    )
    con.unregister("_recipient_rows")


def _recompute_max_stage(
    con: duckdb.DuckDBPyConnection, campaigns: list[str], channel: str
) -> None:
    if not campaigns:
        return
    placeholders = ", ".join("?" for _ in campaigns)
    con.execute(
        f"""
        UPDATE fact_recipient_stage SET max_stage_ordinal =
            CASE WHEN completed IS TRUE THEN 5
                 WHEN engaged   IS TRUE THEN 4
                 WHEN opened    IS TRUE THEN 3
                 WHEN delivered IS TRUE THEN 2
                 WHEN targeted  IS TRUE THEN 1
                 ELSE 0 END
        WHERE channel = ? AND campaign_id IN ({placeholders})
        """,
        [channel, *campaigns],
    )


def _period_expression(grain: str) -> str:
    """Bucket a campaign's activity into a period.

    Every stage for a recipient is attributed to the period the campaign
    *reached* them, not the period each individual event happened. That keeps
    the funnel internally consistent — all five stages of one campaign share a
    denominator — at the cost of a trend line that tracks campaign launches
    rather than trickling engagement. For management reporting that is the right
    trade; note it when reading the trend chart.
    """
    grain = grain if grain in {"day", "week", "month"} else "month"
    return (
        f"CAST(date_trunc('{grain}', COALESCE("
        "r.targeted_at, r.delivered_at, r.opened_at, r.engaged_at, r.completed_at, "
        "CAST(c.start_date AS TIMESTAMP), TIMESTAMP '1970-01-01')) AS DATE)"
    )


def _recompute_rollups(
    con: duckdb.DuckDBPyConnection,
    campaigns: list[str],
    channel: str,
    statuses: dict[str, SupportStatus],
    ctx: LoadContext,
    batch_id: int,
) -> None:
    """Rebuild the rolled-up stage metrics for the affected campaigns.

    Deleted and recomputed rather than incremented, so the rollup can never
    drift away from the recipient rows it is derived from.
    """
    if not campaigns:
        return
    placeholders = ", ".join("?" for _ in campaigns)
    con.execute(
        f"""
        DELETE FROM fact_stage_metric
        WHERE derived_from = 'recipient_rollup'
          AND channel = ? AND campaign_id IN ({placeholders})
        """,
        [channel, *campaigns],
    )

    period = _period_expression(ctx.settings.period_grain)
    counts = con.execute(
        f"""
        SELECT r.campaign_id, {period} AS period_start,
               COUNT(*) FILTER (WHERE r.targeted  IS TRUE) AS TARGETED,
               COUNT(*) FILTER (WHERE r.delivered IS TRUE) AS DELIVERED,
               COUNT(*) FILTER (WHERE r.opened    IS TRUE) AS OPENED,
               COUNT(*) FILTER (WHERE r.engaged   IS TRUE) AS ENGAGED,
               COUNT(*) FILTER (WHERE r.completed IS TRUE) AS COMPLETED
        FROM fact_recipient_stage r
        LEFT JOIN dim_campaign c ON c.campaign_id = r.campaign_id
        WHERE r.channel = ? AND r.campaign_id IN ({placeholders})
        GROUP BY r.campaign_id, {period}
        """,
        [channel, *campaigns],
    ).df()

    rows: list[dict] = []
    for _, row in counts.iterrows():
        for stage, column in STAGE_COLUMNS.items():
            declared = ctx.ladder.get(channel, stage)
            status = statuses.get(stage, declared.status)
            # A stage the channel cannot measure is stored as NULL with its
            # status, so the grid is complete and the UI renders "n/a" without
            # having to reason about missing rows.
            value = float(row[stage]) if status is SupportStatus.MEASURED else None
            rows.append(
                {
                    "campaign_id": row["campaign_id"],
                    "channel": channel,
                    "stage": stage,
                    "stage_ordinal": declared.ordinal,
                    "period_start": row["period_start"],
                    "segment_key": "ALL",
                    "source_object_id": "-",
                    "derived_from": "recipient_rollup",
                    "metric_value": value,
                    "measure_unit": declared.unit.value,
                    # Recipient rollups are already distinct people per period,
                    # so adding periods together is correct.
                    "aggregation": "sum",
                    "support_status": status.value,
                    "batch_id": batch_id,
                }
            )

    if rows:
        _insert_stage_metrics(con, pd.DataFrame(rows))


def _insert_stage_metrics(con: duckdb.DuckDBPyConnection, frame: pd.DataFrame) -> None:
    con.register("_stage_rows", frame)
    con.execute(
        """
        INSERT INTO fact_stage_metric
            (campaign_id, channel, stage, stage_ordinal, period_start, segment_key,
             source_object_id, derived_from, metric_value, measure_unit,
             aggregation, support_status, batch_id, updated_at)
        SELECT campaign_id, channel, stage, stage_ordinal,
               try_cast(period_start AS DATE), segment_key, source_object_id,
               derived_from, metric_value, measure_unit,
               COALESCE(aggregation, 'sum'), support_status, batch_id, now()
        FROM _stage_rows
        ON CONFLICT (campaign_id, channel, stage, period_start, segment_key,
                     source_object_id, derived_from) DO UPDATE SET
            metric_value   = excluded.metric_value,
            measure_unit   = excluded.measure_unit,
            aggregation    = excluded.aggregation,
            support_status = excluded.support_status,
            batch_id       = excluded.batch_id,
            updated_at     = now()
        """
    )
    con.unregister("_stage_rows")


# --------------------------------------------------------------------------- #
# Aggregate-grain loads (Viva Engage)
# --------------------------------------------------------------------------- #


def _load_aggregate(
    con: duckdb.DuckDBPyConnection,
    adapter: SourceAdapter,
    parsed,
    ctx: LoadContext,
    batch_id: int,
) -> None:
    if parsed.campaign_raw is None:
        raise IngestError(f"{adapter.source}: no campaign resolved")

    stage_rows = adapter.to_stage_rows(parsed, ctx)  # type: ignore[attr-defined]
    if stage_rows.empty:
        ctx.report.add_issue("error", "NO_USABLE_ROWS", "no aggregate stage values found")
        raise IngestError("no usable rows")

    campaign_ids = _resolve_campaign_ids(con, parsed.campaign_raw, ctx)
    stage_rows = stage_rows.copy()
    # Each stage row carries the canonical row index it came from, so the
    # campaign is looked up through that rather than inferred from ordering —
    # one file can carry posts for several campaigns.
    stage_rows["campaign_id"] = stage_rows["row_index"].map(campaign_ids)
    dropped = int(stage_rows["campaign_id"].isna().sum())
    if dropped:
        ctx.report.rows_rejected += dropped
        stage_rows = stage_rows[stage_rows["campaign_id"].notna()]
    if stage_rows.empty:
        ctx.report.add_issue("error", "NO_USABLE_ROWS", "no rows resolved to a campaign")
        raise IngestError("no usable rows")

    rows: list[dict] = []
    declared_by_stage = {
        stage: ctx.ladder.get(adapter.source, stage) for stage in STAGE_COLUMNS
    }

    # Emit the measured aggregate values.
    emitted: set[tuple[str, str]] = set()
    for _, row in stage_rows.iterrows():
        stage = row["stage"]
        declared = declared_by_stage[stage]
        rows.append(
            {
                "campaign_id": row["campaign_id"],
                "channel": adapter.source,
                "stage": stage,
                "stage_ordinal": declared.ordinal,
                "period_start": row["period_start"],
                "segment_key": "ALL",
                "source_object_id": row["source_object_id"],
                "derived_from": "source_aggregate",
                "metric_value": row["metric_value"],
                "measure_unit": row["measure_unit"],
                "aggregation": row.get("aggregation", "sum"),
                "support_status": SupportStatus.MEASURED.value,
                "batch_id": batch_id,
            }
        )
        emitted.add((row["campaign_id"], stage))

    # Complete the grid so the UI can render n/a cells without a left join.
    periods = stage_rows.groupby("campaign_id")["period_start"].min().to_dict()
    for campaign_id, period in periods.items():
        for stage, declared in declared_by_stage.items():
            if (campaign_id, stage) in emitted:
                continue
            rows.append(
                {
                    "campaign_id": campaign_id,
                    "channel": adapter.source,
                    "stage": stage,
                    "stage_ordinal": declared.ordinal,
                    "period_start": period,
                    "segment_key": "ALL",
                    "source_object_id": "-",
                    "derived_from": "source_aggregate",
                    "metric_value": None,
                    "measure_unit": declared.unit.value,
                    "aggregation": "sum",
                    "support_status": (
                        declared.status.value
                        if declared.status is not SupportStatus.MEASURED
                        else SupportStatus.NOT_MEASURED.value
                    ),
                    "batch_id": batch_id,
                }
            )

    frame = pd.DataFrame(rows)
    affected = sorted(frame["campaign_id"].dropna().unique())
    placeholders = ", ".join("?" for _ in affected)
    con.execute(
        f"""
        DELETE FROM fact_stage_metric
        WHERE derived_from = 'source_aggregate'
          AND channel = ? AND campaign_id IN ({placeholders})
        """,
        [adapter.source, *affected],
    )
    _insert_stage_metrics(con, frame)

    ctx.report.rows_loaded = len(stage_rows)
    ctx.report.campaign_id = affected[0] if len(affected) == 1 else None
    ctx.report.add_issue(
        "info",
        "AGGREGATE_GRAIN",
        f"{ctx.ladder.channel(adapter.source).display_name} reports per post, not "
        f"per person, so these figures cannot contribute to workforce coverage or "
        f"cross-channel deduplication.",
    )


# --------------------------------------------------------------------------- #
# Identity resolution
# --------------------------------------------------------------------------- #


def resolve_identities(con: duckdb.DuckDBPyConnection) -> int:
    """Report how many recipient rows currently resolve to a known employee.

    There is nothing to write: identity is joined at query time through
    ``v_recipient_identity``. That means a roster loaded *after* the channel
    exports retroactively resolves all existing history, with no re-processing
    step for anyone to forget.
    """
    row = con.execute(
        """
        SELECT COUNT(*)
        FROM fact_recipient_stage r
        JOIN v_recipient_identity ri ON ri.recipient_key = r.recipient_key
        """
    ).fetchone()
    return int(row[0]) if row else 0


# --------------------------------------------------------------------------- #
# Bookkeeping
# --------------------------------------------------------------------------- #


def _already_loaded(con: duckdb.DuckDBPyConnection, sha: str) -> bool:
    row = con.execute(
        """
        SELECT COUNT(*) FROM load_batch
        WHERE file_sha256 = ?
          AND status IN ('loaded', 'loaded_with_warnings')
          AND superseded_by IS NULL
        """,
        [sha],
    ).fetchone()
    return bool(row and row[0])


def _stored_override(con: duckdb.DuckDBPyConnection, sha: str) -> str | None:
    row = con.execute(
        "SELECT source FROM manual_source_override WHERE file_sha256 = ?", [sha]
    ).fetchone()
    return row[0] if row else None


def remember_source_override(
    con: duckdb.DuckDBPyConnection, sha: str, source: str, campaign_id: str | None = None
) -> None:
    """Remember an admin's answer so the same export never has to be classified twice."""
    with write_lock:
        con.execute(
            """
            INSERT INTO manual_source_override (file_sha256, source, campaign_id)
            VALUES (?, ?, ?)
            ON CONFLICT (file_sha256) DO UPDATE SET
                source = excluded.source, campaign_id = excluded.campaign_id
            """,
            [sha, source, campaign_id],
        )


def supersede_batch(con: duckdb.DuckDBPyConnection, sha: str) -> None:
    """Mark previous loads of a file as superseded so it can be force-reloaded."""
    with write_lock:
        con.execute(
            "UPDATE load_batch SET superseded_by = -1 WHERE file_sha256 = ? "
            "AND superseded_by IS NULL",
            [sha],
        )


def _record_date_range(ctx: LoadContext, frame: pd.DataFrame) -> None:
    stamps = [c for c in _TIMESTAMP_COLUMNS if c in frame.columns]
    if not stamps:
        return
    values = pd.concat([pd.to_datetime(frame[c], errors="coerce") for c in stamps])
    values = values.dropna()
    if not values.empty:
        ctx.report.date_range = (
            values.min().date().isoformat(),
            values.max().date().isoformat(),
        )


def _finish(
    con: duckdb.DuckDBPyConnection,
    report: LoadReport,
    started: float,
    settings: Settings,
    *,
    batch_id: int | None = None,
    archive_path: Path | None = None,
    reject: bool = False,
) -> None:
    report.elapsed_ms = int((time.perf_counter() - started) * 1000)
    if batch_id is None:
        batch_id = next_batch_id(con)

    with write_lock:
        con.execute(
            """
            INSERT INTO load_batch
                (batch_id, file_name, file_sha256, source, detection_confidence,
                 mapping_version, campaign_id, rows_read, rows_loaded, rows_rejected,
                 status, started_at, finished_at, report)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                batch_id,
                report.file_name,
                report.file_sha256,
                report.source,
                report.detection_confidence,
                report.mapping_version,
                report.campaign_id,
                report.rows_read,
                report.rows_loaded,
                report.rows_rejected,
                report.status,
                datetime.now(),
                datetime.now(),
                json.dumps(report.to_dict(), default=str),
            ],
        )
        if report.issues:
            con.executemany(
                """
                INSERT INTO load_issue
                    (batch_id, severity, code, column_name, message,
                     example_values, occurrence_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        batch_id,
                        i.severity,
                        i.code,
                        i.column_name,
                        i.message,
                        ", ".join(i.example_values),
                        i.occurrence_count,
                    )
                    for i in report.issues
                ],
            )

    if archive_path is not None and archive_path.exists():
        _archive(archive_path, settings, reject=reject or report.status == "rejected")


def _archive(path: Path, settings: Settings, *, reject: bool) -> None:
    """Move a processed file out of the inbox.

    Archived exports still contain real identifiers in plaintext, so this folder
    needs the same access controls as an HR share.
    """
    root = settings.path("rejected") if reject else settings.path("archive")
    if not reject:
        root = root / date.today().strftime("%Y-%m")
    root.mkdir(parents=True, exist_ok=True)
    target = root / path.name
    counter = 1
    while target.exists():
        target = root / f"{path.stem}__{counter}{path.suffix}"
        counter += 1
    try:
        shutil.move(str(path), str(target))
    except OSError:  # pragma: no cover - permissions or cross-device edge cases
        pass
