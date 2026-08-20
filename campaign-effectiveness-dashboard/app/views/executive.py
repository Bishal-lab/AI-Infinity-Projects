"""Executive overview — the one page a director will actually look at."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import data_access
from components.charts import (
    channel_funnel_chart,
    combined_funnel_chart,
    rate_comparison_chart,
    trend_chart,
)
from components.kpi_cards import caveat_list, kpi_row, stage_support_note
from components.theme import channel_colours, detect_mode
from comms_dashboard.metrics.rag import status_for


def render() -> None:
    st.title("Executive overview")
    version = data_access.data_version()
    if not version:
        st.info("Load some data to see this page.")
        return

    filters = st.session_state["active_filters"]
    mode = detect_mode()
    colours = channel_colours(data_access.channel_order(), mode)
    st.caption(f"Showing: {filters.summary}")

    # -- headline tiles ----------------------------------------------------- #
    kpis = data_access.headline_kpis(filters, version)
    statuses = {key: status_for(kpi) for key, kpi in kpis.items()}
    kpi_row(kpis, statuses, mode)
    caveat_list(kpis, "What these headline numbers are based on")

    st.markdown("---")

    # -- funnel + channel comparison ---------------------------------------- #
    left, right = st.columns([1, 1], gap="large")
    funnel = data_access.combined_funnel(filters, version)
    per_channel = data_access.funnel_by_channel(filters, version)

    with left:
        basis_note = (
            "Sum of channels — one person reached twice counts twice"
            if filters.funnel_basis == "sum"
            else "Distinct employees across channels"
        )
        st.subheader("Communication funnel")
        st.caption(basis_note)
        st.plotly_chart(
            combined_funnel_chart(funnel, mode), use_container_width=True,
            config={"displayModeBar": False},
        )
        with st.expander("Funnel as a table"):
            st.dataframe(
                funnel[
                    ["stage_label", "metric_value", "support_status",
                     "contributing_channels", "excluded_channels"]
                ].rename(
                    columns={
                        "stage_label": "Stage",
                        "metric_value": "People",
                        "support_status": "Status",
                        "contributing_channels": "Counted from",
                        "excluded_channels": "Excluded",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    with right:
        st.subheader("Channels side by side")
        st.caption("Bars appear only where the channel measures that stage.")
        st.plotly_chart(
            channel_funnel_chart(per_channel, colours, mode), use_container_width=True,
            config={"displayModeBar": False},
        )

    stage_support_note(per_channel, mode)

    st.markdown("---")

    # -- engagement ranking + trend ----------------------------------------- #
    left, right = st.columns([1, 1], gap="large")
    channel_kpis = data_access.channel_kpis(filters, version)

    with left:
        st.subheader("Engagement rate by channel")
        rows = []
        for channel, kpis_for_channel in channel_kpis.items():
            kpi = kpis_for_channel.get("engagement_rate")
            display = per_channel[per_channel["channel"] == channel]
            label = (
                display["channel_display"].iloc[0] if not display.empty else channel
            )
            rows.append(
                {
                    "channel": channel,
                    "channel_display": label,
                    "engagement_rate": kpi.value if kpi and kpi.is_renderable else None,
                    "state": kpi.display() if kpi else "—",
                }
            )
        frame = pd.DataFrame(rows)
        st.plotly_chart(
            rate_comparison_chart(frame, colours, "engagement_rate", mode=mode),
            use_container_width=True,
            config={"displayModeBar": False},
        )
        unavailable = frame[frame["engagement_rate"].isna()]
        if not unavailable.empty:
            st.caption(
                "Not shown: "
                + ", ".join(
                    f"{r['channel_display']} ({r['state']})"
                    for _, r in unavailable.iterrows()
                )
            )

    with right:
        st.subheader("Engaged people over time")
        st.caption(
            "Attributed to the period each campaign reached people, so the line "
            "tracks campaign activity rather than trickling engagement."
        )
        st.plotly_chart(
            trend_chart(data_access.trend(filters, version), colours, mode),
            use_container_width=True,
            config={"displayModeBar": False},
        )

    st.markdown("---")

    # -- campaign leaderboard ----------------------------------------------- #
    st.subheader("Campaigns")
    campaigns = data_access.campaign_table(filters, version)
    if campaigns.empty:
        st.info("No campaigns in this view.")
        return

    table = campaigns.copy()
    table["Engagement"] = table["engaged"] / table["targeted"].replace(0, pd.NA)
    table["Reach"] = table["opened"] / table["targeted"].replace(0, pd.NA)
    table["Completion"] = table["completed"] / table["targeted"].replace(0, pd.NA)
    st.dataframe(
        table[
            ["campaign_name", "programme", "objective", "channels",
             "targeted", "Reach", "Engagement", "Completion"]
        ].rename(
            columns={
                "campaign_name": "Campaign",
                "programme": "Programme",
                "objective": "Objective",
                "channels": "Channels",
                "targeted": "Targeted",
            }
        ),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Reach": st.column_config.NumberColumn(format="%.1f%%"),
            "Engagement": st.column_config.NumberColumn(format="%.1f%%"),
            "Completion": st.column_config.NumberColumn(format="%.1f%%"),
            "Targeted": st.column_config.NumberColumn(format="%d"),
        },
    )
    st.caption(
        "Completion is blank for campaigns run only on channels with no "
        "completion step — that is n/a, not zero."
    )

    _download_button(filters, version)


def _download_button(filters, version: int) -> None:
    from comms_dashboard.export.excel import build_workbook

    st.markdown("---")
    if st.button("Build management pack (Excel)", type="primary"):
        with st.spinner("Building workbook…"):
            payload, filename = build_workbook(
                data_access.connection(), filters, version
            )
        st.download_button(
            "Download management pack",
            data=payload,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
