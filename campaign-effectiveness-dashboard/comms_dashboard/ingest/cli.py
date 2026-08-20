"""Headless ingestion.

    python -m comms_dashboard.ingest.cli load            # ingest data/inbox/
    python -m comms_dashboard.ingest.cli load --samples  # copy samples in first
    python -m comms_dashboard.ingest.cli status          # what is in the warehouse

Running the loader from here rather than from the dashboard is the recommended
production mode: DuckDB allows one writer, so a scheduled CLI load does not
compete with someone browsing for the write lock.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from comms_dashboard import db  # noqa: E402
from comms_dashboard.config import get_settings  # noqa: E402
from comms_dashboard.ingest.loader import (  # noqa: E402
    ingest_file,
    ingest_inbox,
    resolve_identities,
)
from comms_dashboard.models import LoadReport  # noqa: E402

STATUS_MARK = {
    "loaded": "ok",
    "loaded_with_warnings": "warn",
    "duplicate": "skip",
    "rejected": "FAIL",
}


def _print_report(report: LoadReport) -> None:
    mark = STATUS_MARK.get(report.status, report.status)
    print(
        f"[{mark:>4}] {report.file_name:44s} "
        f"source={report.source or '-':16s} "
        f"read={report.rows_read:>6} loaded={report.rows_loaded:>6} "
        f"rejected={report.rows_rejected:>4} ({report.elapsed_ms} ms)"
    )
    for issue in report.issues:
        if issue.severity == "info" and issue.code == "UNMAPPED_COLUMNS":
            continue
        prefix = {"error": "  !!", "warning": "   !", "info": "    "}[issue.severity]
        print(f"{prefix} {issue.code}: {issue.message}")
        if issue.example_values:
            print(f"        e.g. {', '.join(issue.example_values)}")


def cmd_load(args: argparse.Namespace) -> int:
    settings = get_settings()
    inbox = settings.path("inbox")
    inbox.mkdir(parents=True, exist_ok=True)

    if args.file:
        return _load_single(args, settings)

    if args.samples:
        samples = settings.path("samples")
        copied = 0
        for path in sorted(samples.iterdir()):
            if path.is_file():
                shutil.copy2(path, inbox / path.name)
                copied += 1
        print(f"Copied {copied} sample file(s) into {inbox}\n")

    con = db.connect(settings=settings)
    db.init_schema(con)
    reports = ingest_inbox(con, settings=settings, archive=not args.keep)

    if not reports:
        print(f"Nothing to load — {inbox} is empty.")
        return 0

    print()
    for report in reports:
        _print_report(report)

    resolved = resolve_identities(con)
    if resolved:
        print(f"\n{resolved:,} recipient row(s) resolve to a known employee via the roster.")

    failures = sum(1 for r in reports if r.status == "rejected")
    print(
        f"\n{len(reports)} file(s): "
        f"{sum(1 for r in reports if r.status.startswith('loaded'))} loaded, "
        f"{sum(1 for r in reports if r.status == 'duplicate')} duplicate, "
        f"{failures} rejected."
    )
    con.close()
    return 1 if failures else 0


def _load_single(args: argparse.Namespace, settings) -> int:
    """Load one named file, optionally forcing its source and campaign.

    Exists for the awkward exports: a Teams attendance report carries no
    campaign identifier and no usable filename pattern, so without this the only
    way to assign one is by hand in the admin page. This is the same seam the
    admin page uses, made scriptable so a monthly load can be repeated exactly.
    """
    path = Path(args.file)
    con = db.connect(settings=settings)
    db.init_schema(con)

    report = ingest_file(
        con,
        path,
        source_override=args.source,
        campaign_override=args.campaign,
        force=args.force,
        settings=settings,
        archive=not args.keep,
    )
    print()
    _print_report(report)
    resolved = resolve_identities(con)
    if resolved:
        print(f"\n{resolved:,} recipient row(s) resolve to a known employee via the roster.")
    con.close()
    return 1 if report.status == "rejected" else 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = get_settings()
    con = db.connect(settings=settings)
    db.init_schema(con)

    print("Row counts")
    for table, count in db.table_counts(con).items():
        print(f"  {table:28s} {count:>8,}")

    print("\nFunnel by channel (all campaigns)")
    frame = db.query_df(
        con,
        """
        -- v_stage_totals, not v_funnel: it applies each row's aggregation
        -- policy, so an audience size repeated on every Viva post is not summed.
        SELECT channel, stage, stage_ordinal,
               ANY_VALUE(support_status) AS support_status,
               ANY_VALUE(measure_unit)   AS measure_unit,
               SUM(metric_value)         AS value
        FROM v_stage_totals
        GROUP BY channel, stage, stage_ordinal
        ORDER BY channel, stage_ordinal
        """,
    )
    if frame.empty:
        print("  (no data loaded yet)")
    else:
        for channel, group in frame.groupby("channel"):
            print(f"  {channel}")
            for _, row in group.iterrows():
                if row["support_status"] != "measured":
                    shown = row["support_status"].replace("_", " ")
                else:
                    shown = f"{row['value']:,.0f} {row['measure_unit']}"
                print(f"    {row['stage']:<10s} {shown}")
    con.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="comms_dashboard.ingest.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    load = sub.add_parser("load", help="ingest everything in the inbox")
    load.add_argument(
        "--samples", action="store_true", help="copy data/samples/ into the inbox first"
    )
    load.add_argument(
        "--keep", action="store_true", help="leave files in the inbox instead of archiving"
    )
    load.add_argument(
        "--file", help="load one named file instead of sweeping the inbox"
    )
    load.add_argument(
        "--source",
        help="force the source for --file, skipping detection "
             "(e.g. teams_webinar); use when a file cannot be identified from "
             "its headers",
    )
    load.add_argument(
        "--campaign",
        help="assign --file to this campaign id, overriding anything in the "
             "file; required for exports with no campaign column, such as a "
             "Teams attendance report",
    )
    load.add_argument(
        "--force", action="store_true",
        help="reload even if this exact file was loaded before",
    )
    load.set_defaults(func=cmd_load)

    status = sub.add_parser("status", help="show what is in the warehouse")
    status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
