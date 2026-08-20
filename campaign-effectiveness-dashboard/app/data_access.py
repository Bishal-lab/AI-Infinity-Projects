"""Cached query layer between the pages and the warehouse.

Every cached function takes ``data_version`` as an argument even where it does
not read it. That is deliberate and it is the single most important line in this
file: ``st.cache_data`` keys on the arguments, so without it the dashboard
happily serves pre-ingest numbers after a load and looks exactly like a loader
that silently failed. ``data_version`` is the highest batch id, so it changes on
every successful load and invalidates everything.

The DuckDB connection is cached as a resource and shared. DuckDB allows a single
writer, so every write goes through ``comms_dashboard.db.write_lock`` and reads
use short-lived cursors.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import streamlit as st

from comms_dashboard import db
from comms_dashboard.config import get_mappings, get_settings, get_stage_ladder
from comms_dashboard.metrics import FilterState, kpis as kpi_module
from comms_dashboard.metrics import coverage as coverage_module


@st.cache_resource
def connection() -> duckdb.DuckDBPyConnection:
    settings = get_settings()
    con = db.connect(settings=settings)
    db.init_schema(con)
    return con


def data_version() -> int:
    """Bump key for every cache below. Never cache this itself."""
    return db.data_version(connection())


@st.cache_data(show_spinner=False)
def funnel_by_channel(filters: FilterState, version: int) -> pd.DataFrame:
    return kpi_module.funnel_by_channel(connection(), filters, version)


@st.cache_data(show_spinner=False)
def combined_funnel(filters: FilterState, version: int) -> pd.DataFrame:
    return kpi_module.combined_funnel(
        connection(), filters, get_settings(), get_stage_ladder(), version
    )


@st.cache_data(show_spinner=False)
def headline_kpis(filters: FilterState, version: int) -> dict:
    return kpi_module.headline_kpis(
        connection(), filters, get_settings(), get_stage_ladder(), version
    )


@st.cache_data(show_spinner=False)
def channel_kpis(filters: FilterState, version: int) -> dict:
    return kpi_module.channel_kpis(connection(), filters, get_settings(), version)


@st.cache_data(show_spinner=False)
def effectiveness_index(filters: FilterState, version: int) -> pd.DataFrame:
    return kpi_module.channel_effectiveness_index(
        connection(), filters, get_settings(), version
    )


@st.cache_data(show_spinner=False)
def dedupe_availability(filters: FilterState, version: int) -> tuple[bool, str]:
    return kpi_module.dedupe_availability(connection(), filters, get_settings(), version)


@st.cache_data(show_spinner=False)
def coverage_by_segment(filters: FilterState, segment: str, version: int) -> pd.DataFrame:
    return coverage_module.coverage_by_segment(
        connection(), filters, segment, get_settings(), version
    )


@st.cache_data(show_spinner=False)
def channel_mix_by_segment(filters: FilterState, segment: str, version: int) -> pd.DataFrame:
    return coverage_module.channel_mix_by_segment(
        connection(), filters, segment, get_settings(), version
    )


@st.cache_data(show_spinner=False)
def multi_channel_exposure(filters: FilterState, version: int) -> pd.DataFrame:
    return coverage_module.multi_channel_exposure(
        connection(), filters, get_settings(), version
    )


@st.cache_data(show_spinner=False)
def trend(filters: FilterState, version: int) -> pd.DataFrame:
    """Engaged people per period per channel."""
    where, params = filters.where("f")
    return db.query_df(
        connection(),
        f"""
        SELECT channel, channel_display, period_start, SUM(metric_value) AS metric_value
        FROM v_funnel f
        WHERE {where} AND stage = 'ENGAGED'
          AND support_status = 'measured' AND measure_unit = 'persons'
        GROUP BY channel, channel_display, period_start
        ORDER BY period_start
        """,
        params,
    )


@st.cache_data(show_spinner=False)
def campaign_table(filters: FilterState, version: int) -> pd.DataFrame:
    where, params = filters.where("f")
    return db.query_df(
        connection(),
        f"""
        WITH per_campaign AS (
            SELECT campaign_id, campaign_name, programme, objective, channel, stage,
                   measure_unit, support_status, aggregation,
                   CASE WHEN aggregation = 'max'
                        THEN MAX(metric_value) ELSE SUM(metric_value) END AS metric_value
            FROM v_funnel f
            WHERE {where}
            GROUP BY campaign_id, campaign_name, programme, objective, channel,
                     stage, measure_unit, support_status, aggregation
        )
        SELECT campaign_id,
               ANY_VALUE(campaign_name) AS campaign_name,
               ANY_VALUE(programme)     AS programme,
               ANY_VALUE(objective)     AS objective,
               COUNT(DISTINCT channel)  AS channels,
               SUM(metric_value) FILTER (
                   WHERE stage='TARGETED'  AND support_status='measured'
                     AND measure_unit='persons') AS targeted,
               SUM(metric_value) FILTER (
                   WHERE stage='OPENED'    AND support_status='measured'
                     AND measure_unit='persons') AS opened,
               SUM(metric_value) FILTER (
                   WHERE stage='ENGAGED'   AND support_status='measured'
                     AND measure_unit='persons') AS engaged,
               SUM(metric_value) FILTER (
                   WHERE stage='COMPLETED' AND support_status='measured'
                     AND measure_unit='persons') AS completed
        FROM per_campaign
        GROUP BY campaign_id
        ORDER BY targeted DESC NULLS LAST
        """,
        params,
    )


@st.cache_data(show_spinner=False)
def channel_extras(filters: FilterState, channel: str, version: int) -> pd.DataFrame:
    where, params = filters.where("", include_dates=False)
    return db.query_df(
        connection(),
        f"SELECT * FROM v_channel_extras WHERE {where} AND channel = ?",
        params + [channel],
    )


@st.cache_data(show_spinner=False)
def recipient_measures(filters: FilterState, channel: str, version: int) -> pd.DataFrame:
    """Raw per-person measures for a channel's distribution charts.

    Returns measures only — never identifiers. ``privacy.allow_individual_drilldown``
    stays off by default and nothing here would honour it anyway.
    """
    where, params = filters.where("r", include_dates=False)
    return db.query_df(
        connection(),
        f"""
        SELECT r.attendance_minutes, r.session_minutes, r.score, r.progress_pct,
               r.opened, r.engaged, r.completed
        FROM fact_recipient_stage r
        WHERE {where} AND r.channel = ?
        """,
        params + [channel],
    )


@st.cache_data(show_spinner=False)
def load_history(version: int) -> pd.DataFrame:
    return db.query_df(
        connection(),
        """
        SELECT batch_id, finished_at, file_name, source, status,
               rows_read, rows_loaded, rows_rejected, campaign_id, report
        FROM load_batch
        ORDER BY batch_id DESC
        LIMIT 200
        """,
    )


@st.cache_data(show_spinner=False)
def load_issues(batch_id: int, version: int) -> pd.DataFrame:
    return db.query_df(
        connection(),
        "SELECT severity, code, column_name, message, example_values, occurrence_count "
        "FROM load_issue WHERE batch_id = ? ORDER BY "
        "CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END",
        [batch_id],
    )


@st.cache_data(show_spinner=False)
def data_quality(version: int) -> pd.DataFrame:
    return db.query_df(connection(), "SELECT * FROM v_data_quality")


@st.cache_data(show_spinner=False)
def identity_resolution(version: int) -> pd.DataFrame:
    return db.query_df(
        connection(),
        """
        SELECT r.channel, c.display_name AS channel_display,
               SUM(r.recipients) AS recipients, SUM(r.resolved) AS resolved,
               SUM(r.resolved)::DOUBLE / NULLIF(SUM(r.recipients), 0) AS resolution_rate
        FROM v_identity_resolution r
        LEFT JOIN dim_channel c ON c.channel = r.channel
        GROUP BY r.channel, c.display_name
        ORDER BY resolution_rate DESC
        """,
    )


@st.cache_data(show_spinner=False)
def stage_support_matrix(version: int) -> pd.DataFrame:
    return db.query_df(
        connection(),
        """
        SELECT c.display_name AS channel, s.stage, s.stage_ordinal,
               s.support_status, s.unit, s.native_name, s.caveat
        FROM dim_channel_stage_support s
        JOIN dim_channel c ON c.channel = s.channel
        ORDER BY c.sort_order, s.stage_ordinal
        """,
    )


def clear_caches() -> None:
    """Drop every cached query. Called after an ingest from the admin page."""
    st.cache_data.clear()


def channel_order() -> list[str]:
    return list(get_stage_ladder().channel_names)


def mappings():
    return get_mappings()
