"""Workforce coverage by segment, with disclosure control.

Every breakdown here can single out a small group of named colleagues, so
suppression is applied inside the query layer rather than left to the UI to
remember.

Two rules:

* Any cell whose population is below ``privacy.min_group_size`` is suppressed.
* If exactly one sub-group within a parent is suppressed, the next-smallest is
  suppressed too — otherwise the hidden value is trivially recoverable by
  subtracting the visible ones from the total.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from ..config import Settings, get_settings
from ..db import query_df
from .filters import FilterState

SEGMENT_COLUMNS = {
    "department": "Department",
    "region": "Region",
    "grade_band": "Grade band",
    "site": "Site",
}


def coverage_by_segment(
    con: duckdb.DuckDBPyConnection,
    filters: FilterState,
    segment: str = "department",
    settings: Settings | None = None,
    data_version: int = 0,  # noqa: ARG001 - cache key only
) -> pd.DataFrame:
    """Headcount, targeted, reached, engaged and completed per segment."""
    settings = settings or get_settings()
    if segment not in SEGMENT_COLUMNS:
        raise ValueError(f"unknown segment {segment!r}; expected {sorted(SEGMENT_COLUMNS)}")

    where, params = filters.where("r", include_dates=False)
    segment_where, segment_params = filters.segment_where("e")

    frame = query_df(
        con,
        f"""
        WITH population AS (
            SELECT COALESCE(e.{segment}, 'Unknown') AS segment,
                   COUNT(*) AS headcount
            FROM dim_employee e
            WHERE {segment_where} AND COALESCE(e.is_active, TRUE)
            GROUP BY 1
        ),
        activity AS (
            SELECT COALESCE(e.{segment}, 'Unknown') AS segment,
                   COUNT(DISTINCT ri.employee_key)
                       FILTER (WHERE r.targeted  IS TRUE) AS targeted,
                   COUNT(DISTINCT ri.employee_key)
                       FILTER (WHERE r.opened    IS TRUE) AS reached,
                   COUNT(DISTINCT ri.employee_key)
                       FILTER (WHERE r.engaged   IS TRUE) AS engaged,
                   COUNT(DISTINCT ri.employee_key)
                       FILTER (WHERE r.completed IS TRUE) AS completed
            FROM fact_recipient_stage r
            JOIN v_recipient_identity ri ON ri.recipient_key = r.recipient_key
            JOIN dim_employee e ON e.employee_key = ri.employee_key
            WHERE {where} AND {segment_where}
            GROUP BY 1
        )
        SELECT p.segment,
               p.headcount,
               COALESCE(a.targeted, 0)  AS targeted,
               COALESCE(a.reached, 0)   AS reached,
               COALESCE(a.engaged, 0)   AS engaged,
               COALESCE(a.completed, 0) AS completed
        FROM population p
        LEFT JOIN activity a ON a.segment = p.segment
        ORDER BY p.headcount DESC
        """,
        segment_params + params + segment_params,
    )
    if frame.empty:
        return frame

    frame["coverage_rate"] = frame["reached"] / frame["headcount"].replace(0, pd.NA)
    frame["engagement_rate"] = frame["engaged"] / frame["headcount"].replace(0, pd.NA)
    frame["never_reached"] = frame["headcount"] - frame["reached"]
    return apply_suppression(frame, settings, population_column="headcount")


def apply_suppression(
    frame: pd.DataFrame,
    settings: Settings,
    population_column: str = "headcount",
) -> pd.DataFrame:
    """Blank out cells for groups too small to report on.

    Adds a ``suppressed`` column and nulls the measures — the row itself stays,
    so the reader can see that a group exists and was deliberately withheld
    rather than silently missing.
    """
    if frame.empty or population_column not in frame:
        return frame

    minimum = settings.min_group_size
    frame = frame.copy()
    frame["suppressed"] = frame[population_column] < minimum

    if settings.complementary_suppression and frame["suppressed"].sum() == 1:
        # Exactly one hidden group is not hidden at all: subtract the visible
        # rows from the total and it reappears. Hide the next-smallest too.
        visible = frame[~frame["suppressed"]]
        if not visible.empty:
            next_smallest = visible[population_column].idxmin()
            frame.loc[next_smallest, "suppressed"] = True

    measure_columns = [
        c for c in ("targeted", "reached", "engaged", "completed", "never_reached",
                    "coverage_rate", "engagement_rate")
        if c in frame.columns
    ]
    frame.loc[frame["suppressed"], measure_columns] = pd.NA
    return frame


def channel_mix_by_segment(
    con: duckdb.DuckDBPyConnection,
    filters: FilterState,
    segment: str = "department",
    settings: Settings | None = None,
    data_version: int = 0,  # noqa: ARG001 - cache key only
) -> pd.DataFrame:
    """Reach per channel per segment — which channels actually land where."""
    settings = settings or get_settings()
    if segment not in SEGMENT_COLUMNS:
        raise ValueError(f"unknown segment {segment!r}")

    where, params = filters.where("r", include_dates=False)
    segment_where, segment_params = filters.segment_where("e")

    frame = query_df(
        con,
        f"""
        SELECT COALESCE(e.{segment}, 'Unknown') AS segment,
               c.display_name AS channel,
               COUNT(DISTINCT ri.employee_key) FILTER (WHERE r.targeted IS TRUE) AS targeted,
               COUNT(DISTINCT ri.employee_key) FILTER (WHERE r.opened   IS TRUE) AS reached
        FROM fact_recipient_stage r
        JOIN v_recipient_identity ri ON ri.recipient_key = r.recipient_key
        JOIN dim_employee e ON e.employee_key = ri.employee_key
        LEFT JOIN dim_channel c ON c.channel = r.channel
        WHERE {where} AND {segment_where}
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
        params + segment_params,
    )
    if frame.empty:
        return frame
    frame["reach_rate"] = frame["reached"] / frame["targeted"].replace(0, pd.NA)
    small = frame["targeted"] < settings.min_group_size
    frame.loc[small, ["targeted", "reached", "reach_rate"]] = pd.NA
    frame["suppressed"] = small
    return frame


def multi_channel_exposure(
    con: duckdb.DuckDBPyConnection,
    filters: FilterState,
    settings: Settings | None = None,
    data_version: int = 0,  # noqa: ARG001 - cache key only
) -> pd.DataFrame:
    """How many channels each reached employee heard the campaign on."""
    settings = settings or get_settings()
    where, params = filters.where("r", include_dates=False)
    frame = query_df(
        con,
        f"""
        WITH per_employee AS (
            SELECT ri.employee_key,
                   COUNT(DISTINCT r.channel) FILTER (WHERE r.opened IS TRUE) AS channels
            FROM fact_recipient_stage r
            JOIN v_recipient_identity ri ON ri.recipient_key = r.recipient_key
            WHERE {where}
            GROUP BY 1
        )
        SELECT channels, COUNT(*) AS employees
        FROM per_employee
        GROUP BY 1
        ORDER BY 1
        """,
        params,
    )
    if frame.empty:
        return frame
    small = frame["employees"] < settings.min_group_size
    frame.loc[small, "employees"] = pd.NA
    return frame
