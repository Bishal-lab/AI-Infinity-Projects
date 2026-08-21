"""The shared sidebar filter, rendered once in ``main.py``.

State lives in ``st.session_state`` so it survives page switches, and is mirrored
into the URL so a filtered view can be shared — the second thing every executive
user asks for.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from comms_dashboard.db import query_df
from comms_dashboard.metrics import FilterState

SESSION_KEY = "filters"


def _options(con, sql: str, column: str) -> list[str]:
    frame = query_df(con, sql)
    if frame.empty:
        return []
    return [v for v in frame[column].dropna().tolist()]


def _from_query_params() -> dict:
    params = st.query_params
    out: dict = {}
    for key in ("campaign_ids", "channels", "departments", "regions", "grade_bands"):
        raw = params.get(key)
        if raw:
            out[key] = tuple(raw.split("|"))
    if params.get("basis") in ("sum", "dedup"):
        out["funnel_basis"] = params["basis"]
    for key in ("date_from", "date_to"):
        raw = params.get(key)
        if raw:
            try:
                out[key] = date.fromisoformat(raw)
            except ValueError:
                pass
    return out


def _to_query_params(filters: FilterState) -> None:
    params: dict[str, str] = {}
    for key in ("campaign_ids", "channels", "departments", "regions", "grade_bands"):
        values = getattr(filters, key)
        if values:
            params[key] = "|".join(values)
    if filters.date_from:
        params["date_from"] = filters.date_from.isoformat()
    if filters.date_to:
        params["date_to"] = filters.date_to.isoformat()
    if filters.funnel_basis != "sum":
        params["basis"] = filters.funnel_basis
    st.query_params.clear()
    for key, value in params.items():
        st.query_params[key] = value


def render_sidebar(con, dedupe_ok: bool, dedupe_reason: str) -> FilterState:
    """Draw the filter controls and return the resulting state."""
    if SESSION_KEY not in st.session_state:
        st.session_state[SESSION_KEY] = FilterState(**_from_query_params())
    current: FilterState = st.session_state[SESSION_KEY]

    campaigns = query_df(
        con,
        "SELECT campaign_id, campaign_name, programme FROM dim_campaign "
        "ORDER BY campaign_name",
    )
    channels = query_df(
        con, "SELECT channel, display_name FROM dim_channel ORDER BY sort_order"
    )
    bounds = query_df(
        con, "SELECT MIN(period_start) AS lo, MAX(period_start) AS hi FROM fact_stage_metric"
    )

    with st.sidebar:
        st.markdown("### Filters")

        campaign_labels = dict(zip(campaigns["campaign_id"], campaigns["campaign_name"])) \
            if not campaigns.empty else {}
        chosen_campaigns = st.multiselect(
            "Campaign",
            options=list(campaign_labels),
            default=[c for c in current.campaign_ids if c in campaign_labels],
            format_func=lambda c: campaign_labels.get(c, c),
            placeholder="All campaigns",
        )

        channel_labels = dict(zip(channels["channel"], channels["display_name"])) \
            if not channels.empty else {}
        chosen_channels = st.multiselect(
            "Channel",
            options=list(channel_labels),
            default=[c for c in current.channels if c in channel_labels],
            format_func=lambda c: channel_labels.get(c, c),
            placeholder="All channels",
        )

        lo = bounds.iloc[0]["lo"] if not bounds.empty else None
        hi = bounds.iloc[0]["hi"] if not bounds.empty else None
        date_from = date_to = None
        if lo is not None and hi is not None and str(lo) != "NaT":
            chosen_range = st.date_input(
                "Period",
                value=(current.date_from or lo, current.date_to or hi),
                min_value=lo,
                max_value=hi,
            )
            if isinstance(chosen_range, tuple) and len(chosen_range) == 2:
                date_from, date_to = chosen_range

        st.markdown("---")
        st.markdown("**Audience**")
        departments = _options(
            con,
            "SELECT DISTINCT department FROM dim_employee WHERE department IS NOT NULL "
            "ORDER BY department",
            "department",
        )
        regions = _options(
            con,
            "SELECT DISTINCT region FROM dim_employee WHERE region IS NOT NULL ORDER BY region",
            "region",
        )
        grades = _options(
            con,
            "SELECT DISTINCT grade_band FROM dim_employee WHERE grade_band IS NOT NULL "
            "ORDER BY grade_band",
            "grade_band",
        )
        if not departments:
            st.caption(
                "No employee roster loaded, so audience filters are unavailable. "
                "Drop an HR extract into the inbox to enable coverage analysis."
            )
        chosen_departments = st.multiselect(
            "Department", departments,
            default=[d for d in current.departments if d in departments],
            placeholder="All departments", disabled=not departments,
        )
        chosen_regions = st.multiselect(
            "Region", regions,
            default=[r for r in current.regions if r in regions],
            placeholder="All regions", disabled=not regions,
        )
        chosen_grades = st.multiselect(
            "Grade band", grades,
            default=[g for g in current.grade_bands if g in grades],
            placeholder="All grades", disabled=not grades,
        )

        st.markdown("---")
        st.markdown("**Combined funnel basis**")
        basis_options = ["sum", "dedup"]
        basis_labels = {
            "sum": "Sum of channels (message-level)",
            "dedup": "Deduplicated by employee (people-level)",
        }
        index = basis_options.index(current.funnel_basis) if not (
            current.funnel_basis == "dedup" and not dedupe_ok
        ) else 0
        basis = st.radio(
            "Basis",
            basis_options,
            index=index,
            format_func=lambda b: basis_labels[b]
            + ("" if b == "sum" or dedupe_ok else "  (unavailable)"),
            label_visibility="collapsed",
        )
        if basis == "dedup" and not dedupe_ok:
            st.warning(dedupe_reason)
            basis = "sum"
        elif basis == "sum":
            st.caption(
                "Channels are added together, so someone reached by both email and "
                "WhatsApp counts twice. This is a **message** funnel, not a people "
                "funnel."
            )
        else:
            st.caption(
                f"Distinct employees across channels. {dedupe_reason}. "
                "Aggregate-only channels are excluded."
            )

    filters = FilterState(
        date_from=date_from,
        date_to=date_to,
        campaign_ids=tuple(chosen_campaigns),
        channels=tuple(chosen_channels),
        departments=tuple(chosen_departments),
        regions=tuple(chosen_regions),
        grade_bands=tuple(chosen_grades),
        funnel_basis=basis,
    )
    st.session_state[SESSION_KEY] = filters
    _to_query_params(filters)
    return filters
