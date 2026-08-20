"""The management pack.

Excel, because that is what actually gets forwarded to a leadership team. Built
with XlsxWriter using **native Excel charts** — no image rendering, so there is
no headless-browser dependency to install on a locked-down server.

Two rules carried over from the screen:

* A stage that could not be measured is written as the words "n/a" or "not
  measured", never as ``0``.
* No person-level rows are ever written. The workbook is aggregates only, so
  forwarding it cannot leak an individual's engagement.
"""

from __future__ import annotations

import io
from datetime import datetime

import duckdb
import pandas as pd

from ..config import Settings, get_settings, get_stage_ladder
from ..db import query_df
from ..metrics import FilterState
from ..metrics.kpis import (
    KPI_DEFINITIONS,
    channel_kpis,
    combined_funnel,
    funnel_by_channel,
    headline_kpis,
)
from ..metrics.rag import status_for

RAG_FILL = {"green": "#d5eed5", "amber": "#fdf0cf", "red": "#f6d8d8", "grey": "#eeeeee"}
RAG_TEXT = {"green": "#0b5c0b", "amber": "#7a5600", "red": "#8a1f1f", "grey": "#52514e"}


def build_workbook(
    con: duckdb.DuckDBPyConnection,
    filters: FilterState,
    data_version: int = 0,
    settings: Settings | None = None,
) -> tuple[bytes, str]:
    """Build the pack and return ``(bytes, filename)``."""
    settings = settings or get_settings()
    ladder = get_stage_ladder()
    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        book = writer.book
        fmt = _formats(book)

        _cover_sheet(writer, fmt, filters, settings)
        _summary_sheet(con, writer, fmt, filters, settings, ladder, data_version)
        _funnel_sheet(con, writer, fmt, filters, data_version)
        _campaign_sheet(con, writer, fmt, filters, data_version)
        _coverage_sheet(con, writer, fmt, filters, settings, data_version)
        _quality_sheet(con, writer, fmt, data_version)
        _definitions_sheet(writer, fmt)

    stamp = datetime.now().strftime("%Y-%m-%d")
    return buffer.getvalue(), f"communication-effectiveness-{stamp}.xlsx"


def _formats(book) -> dict:
    return {
        "title": book.add_format({"bold": True, "font_size": 16, "font_color": "#0b0b0b"}),
        "h2": book.add_format({"bold": True, "font_size": 12, "font_color": "#0b0b0b"}),
        "header": book.add_format(
            {"bold": True, "bg_color": "#f0efec", "border": 1, "border_color": "#e1e0d9",
             "text_wrap": True, "valign": "top"}
        ),
        "text": book.add_format({"valign": "top", "text_wrap": True}),
        "muted": book.add_format({"font_color": "#898781", "italic": True, "text_wrap": True}),
        "pct": book.add_format({"num_format": "0.0%"}),
        "int": book.add_format({"num_format": "#,##0"}),
        "na": book.add_format({"font_color": "#898781", "italic": True, "align": "right"}),
        **{
            f"rag_{k}": book.add_format(
                {"bg_color": RAG_FILL[k], "font_color": RAG_TEXT[k], "bold": True,
                 "align": "center"}
            )
            for k in RAG_FILL
        },
    }


def _write_frame(writer, sheet_name: str, frame: pd.DataFrame, fmt: dict,
                 start_row: int = 0, widths: dict[int, int] | None = None):
    frame.to_excel(writer, sheet_name=sheet_name, startrow=start_row, index=False)
    worksheet = writer.sheets[sheet_name]
    for col, name in enumerate(frame.columns):
        worksheet.write(start_row, col, str(name), fmt["header"])
    for col, width in (widths or {}).items():
        worksheet.set_column(col, col, width)
    return worksheet


# --------------------------------------------------------------------------- #
# Sheets
# --------------------------------------------------------------------------- #


def _cover_sheet(writer, fmt, filters: FilterState, settings: Settings) -> None:
    sheet = writer.book.add_worksheet("Cover")
    writer.sheets["Cover"] = sheet
    sheet.set_column(0, 0, 26)
    sheet.set_column(1, 1, 86)

    sheet.write(0, 0, settings.title, fmt["title"])
    sheet.write(1, 0, settings.org_name, fmt["muted"])

    rows = [
        ("Generated", datetime.now().strftime("%d %B %Y, %H:%M")),
        ("View", filters.summary),
        ("Funnel basis", "Sum of channels (message-level)"
            if filters.funnel_basis == "sum"
            else "Deduplicated by employee (people-level)"),
        ("Open rate denominator", settings.open_rate_basis),
        ("Minimum group size", str(settings.min_group_size)),
    ]
    for i, (label, value) in enumerate(rows, start=3):
        sheet.write(i, 0, label, fmt["h2"])
        sheet.write(i, 1, value, fmt["text"])

    sheet.write(
        len(rows) + 4, 0,
        "How to read blanks", fmt["h2"],
    )
    sheet.write(
        len(rows) + 4, 1,
        'Cells reading "n/a" mean the platform has no such stage — for example, '
        "email has no completion step and an LMS assignment has no delivery step. "
        '"not measured" means the platform can report it but the export loaded did '
        "not include it. Neither is a zero, and both are excluded from the rates "
        "they would otherwise appear in.",
        fmt["muted"],
    )
    sheet.write(
        len(rows) + 6, 0, "Contains no personal data", fmt["h2"]
    )
    sheet.write(
        len(rows) + 6, 1,
        f"Aggregates only. Groups smaller than {settings.min_group_size} people are "
        "suppressed, and no individual's engagement appears anywhere in this "
        "workbook.",
        fmt["muted"],
    )


def _summary_sheet(con, writer, fmt, filters, settings, ladder, version) -> None:
    kpis = headline_kpis(con, filters, settings, ladder, version)
    rows = []
    statuses = []
    for kpi in kpis.values():
        rag, explanation = status_for(kpi, settings)
        statuses.append(rag)
        rows.append(
            {
                "KPI": kpi.label,
                "Value": kpi.value if kpi.is_renderable else None,
                "Shown as": kpi.display(),
                "Numerator": kpi.numerator,
                "Denominator": kpi.denominator,
                "Status": rag.upper(),
                "Notes": " ".join(kpi.caveats) or explanation,
            }
        )
    frame = pd.DataFrame(rows)
    sheet = _write_frame(
        writer, "Summary KPIs", frame, fmt,
        widths={0: 24, 1: 12, 2: 14, 3: 14, 4: 14, 5: 10, 6: 74},
    )
    for i, rag in enumerate(statuses, start=1):
        sheet.write(i, 5, rag.upper(), fmt[f"rag_{rag}"])
        value = frame.iloc[i - 1]["Value"]
        if pd.isna(value):
            sheet.write(i, 1, frame.iloc[i - 1]["Shown as"], fmt["na"])
        else:
            sheet.write_number(i, 1, float(value), fmt["pct"])
    sheet.freeze_panes(1, 0)


def _funnel_sheet(con, writer, fmt, filters, version) -> None:
    per_channel = funnel_by_channel(con, filters, version)
    combined = combined_funnel(con, filters, data_version=version)

    frame = per_channel.copy()
    for column in ("native_name", "caveat"):
        frame[column] = frame[column].fillna("")
    frame["Value"] = [
        f"{v:,.0f}" if s == "measured" and pd.notna(v)
        else ("n/a" if s == "not_applicable" else "not measured")
        for v, s in zip(frame["metric_value"], frame["support_status"])
    ]
    display = frame[
        ["channel_display", "stage", "native_name", "Value", "measure_unit", "caveat"]
    ].rename(
        columns={
            "channel_display": "Channel",
            "stage": "Stage",
            "native_name": "Reported as",
            "measure_unit": "Counts",
            "caveat": "Note",
        }
    )
    sheet = _write_frame(
        writer, "Funnel by channel", display, fmt,
        widths={0: 22, 1: 12, 2: 30, 3: 14, 4: 10, 5: 70},
    )
    sheet.freeze_panes(1, 0)

    start = len(display) + 3
    sheet.write(start - 1, 0, "Combined funnel", fmt["h2"])
    combined_display = combined[
        ["stage_label", "metric_value", "support_status", "contributing_channels"]
    ].rename(
        columns={
            "stage_label": "Stage",
            "metric_value": "People",
            "support_status": "Status",
            "contributing_channels": "Counted from",
        }
    )
    combined_display.to_excel(writer, sheet_name="Funnel by channel",
                              startrow=start, index=False)
    for col, name in enumerate(combined_display.columns):
        sheet.write(start, col, str(name), fmt["header"])

    # A native Excel chart: no image rendering, so no browser dependency.
    chart = writer.book.add_chart({"type": "bar"})
    chart.add_series(
        {
            "name": "People",
            "categories": ["Funnel by channel", start + 1,
                           0, start + len(combined_display), 0],
            "values": ["Funnel by channel", start + 1,
                       1, start + len(combined_display), 1],
            "fill": {"color": "#2a78d6"},
            "gap": 40,
        }
    )
    chart.set_title({"name": "Communication funnel"})
    chart.set_legend({"none": True})
    chart.set_y_axis({"reverse": True})
    sheet.insert_chart(start, 6, chart, {"x_scale": 1.3, "y_scale": 1.1})


def _campaign_sheet(con, writer, fmt, filters, version) -> None:
    where, params = filters.where("f")
    frame = query_df(
        con,
        f"""
        WITH per_campaign AS (
            SELECT campaign_id, campaign_name, programme, objective, channel, stage,
                   measure_unit, support_status, aggregation,
                   CASE WHEN aggregation = 'max'
                        THEN MAX(metric_value) ELSE SUM(metric_value) END AS metric_value
            FROM v_funnel f WHERE {where}
            GROUP BY campaign_id, campaign_name, programme, objective, channel,
                     stage, measure_unit, support_status, aggregation
        )
        SELECT ANY_VALUE(campaign_name) AS "Campaign",
               ANY_VALUE(programme)     AS "Programme",
               ANY_VALUE(objective)     AS "Objective",
               COUNT(DISTINCT channel)  AS "Channels",
               SUM(metric_value) FILTER (WHERE stage='TARGETED'
                   AND support_status='measured' AND measure_unit='persons') AS "Targeted",
               SUM(metric_value) FILTER (WHERE stage='OPENED'
                   AND support_status='measured' AND measure_unit='persons') AS "Reached",
               SUM(metric_value) FILTER (WHERE stage='ENGAGED'
                   AND support_status='measured' AND measure_unit='persons') AS "Engaged",
               SUM(metric_value) FILTER (WHERE stage='COMPLETED'
                   AND support_status='measured' AND measure_unit='persons') AS "Completed"
        FROM per_campaign
        GROUP BY campaign_id
        ORDER BY "Targeted" DESC NULLS LAST
        """,
        params,
    )
    if not frame.empty:
        frame["Reach rate"] = frame["Reached"] / frame["Targeted"]
        frame["Engagement rate"] = frame["Engaged"] / frame["Targeted"]
        frame["Completion rate"] = frame["Completed"] / frame["Targeted"]

    sheet = _write_frame(
        writer, "Campaign detail", frame, fmt,
        widths={0: 30, 1: 26, 2: 14, 3: 10, 4: 12, 5: 12, 6: 12, 7: 12,
                8: 13, 9: 16, 10: 15},
    )
    if not frame.empty:
        for col in (8, 9, 10):
            sheet.set_column(col, col, 15, fmt["pct"])
        for col in (4, 5, 6, 7):
            sheet.set_column(col, col, 12, fmt["int"])
    sheet.freeze_panes(1, 1)


def _coverage_sheet(con, writer, fmt, filters, settings, version) -> None:
    from ..metrics.coverage import coverage_by_segment

    frame = coverage_by_segment(con, filters, "department", settings, version)
    if frame.empty:
        sheet = writer.book.add_worksheet("Coverage")
        writer.sheets["Coverage"] = sheet
        sheet.set_column(0, 0, 90)
        sheet.write(
            0, 0,
            "No employee roster has been loaded, so coverage by department is not "
            "available. Load an HR extract to enable it.",
            fmt["muted"],
        )
        return

    display = frame[
        ["segment", "headcount", "targeted", "reached", "engaged", "completed",
         "never_reached", "coverage_rate", "suppressed"]
    ].rename(
        columns={
            "segment": "Department",
            "headcount": "Headcount",
            "targeted": "Targeted",
            "reached": "Reached",
            "engaged": "Engaged",
            "completed": "Completed",
            "never_reached": "Never reached",
            "coverage_rate": "Coverage",
            "suppressed": "Suppressed",
        }
    )
    sheet = _write_frame(
        writer, "Coverage", display, fmt,
        widths={0: 24, 1: 12, 2: 12, 3: 12, 4: 12, 5: 12, 6: 15, 7: 12, 8: 12},
    )
    sheet.set_column(7, 7, 12, fmt["pct"])
    sheet.freeze_panes(1, 1)
    sheet.write(
        len(display) + 2, 0,
        f"Groups smaller than {settings.min_group_size} people are suppressed to "
        f"prevent individuals being identified.",
        fmt["muted"],
    )


def _quality_sheet(con, writer, fmt, version) -> None:
    frame = query_df(
        con,
        """
        SELECT batch_id AS "Batch", finished_at AS "When", file_name AS "File",
               source AS "Source", status AS "Status", rows_read AS "Read",
               rows_loaded AS "Loaded", rows_rejected AS "Rejected"
        FROM load_batch ORDER BY batch_id DESC LIMIT 200
        """,
    )
    if "When" in frame.columns:
        frame["When"] = frame["When"].astype(str)
    sheet = _write_frame(
        writer, "Data quality", frame, fmt,
        widths={0: 8, 1: 20, 2: 44, 3: 18, 4: 22, 5: 10, 6: 10, 7: 10},
    )
    sheet.freeze_panes(1, 0)


def _definitions_sheet(writer, fmt) -> None:
    frame = pd.DataFrame(
        [
            {
                "KPI": d.label,
                "Formula": d.formula,
                "Applies to": ", ".join(d.channels) if d.channels else "all channels",
                "What it means": d.description,
            }
            for d in KPI_DEFINITIONS
        ]
    )
    sheet = _write_frame(
        writer, "Definitions", frame, fmt, widths={0: 30, 1: 26, 2: 26, 3: 90}
    )
    sheet.freeze_panes(1, 0)

    notes = [
        "Cross-channel rates restrict both the numerator and the denominator to "
        "the channels that measure both stages, so different populations are "
        "never mixed. Each headline KPI names its basis.",
        "Email open rate is inflated by Apple Mail Privacy Protection and "
        "deflated by Outlook image blocking; corporate link scanners inflate "
        "clicks. Read it as directional and lead with engagement.",
        "WhatsApp read receipts can be switched off by the recipient, "
        "understating reads.",
        "Viva Engage engagement counts interactions, not people, and its reach "
        "is a lower bound because post-level data cannot deduplicate viewers "
        "across posts.",
        "Every stage is attributed to the period the campaign reached a person, "
        "so all stages of a campaign share one denominator.",
    ]
    start = len(frame) + 3
    sheet.write(start - 1, 0, "Caveats", fmt["h2"])
    for i, note in enumerate(notes):
        sheet.write(start + i, 0, f"{i + 1}.", fmt["text"])
        sheet.write(start + i, 3, note, fmt["muted"])
