"""Campaigns side by side, with a guardrail against unfair comparisons."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import data_access
from components.charts import bullet_chart, channel_funnel_chart
from components.theme import CHROME, channel_colours, detect_mode
from comms_dashboard.db import query_df


def render() -> None:
    st.title("Campaign comparison")
    version = data_access.data_version()
    if not version:
        st.info("Load some data to see this page.")
        return

    filters = st.session_state["active_filters"]
    mode = detect_mode()
    chrome = CHROME[mode]
    colours = channel_colours(data_access.channel_order(), mode)

    campaigns = data_access.campaign_table(filters, version)
    if campaigns.empty:
        st.info("No campaigns in this view.")
        return

    labels = dict(zip(campaigns["campaign_id"], campaigns["campaign_name"]))
    default = list(labels)[: min(4, len(labels))]
    chosen = st.multiselect(
        "Campaigns to compare",
        list(labels),
        default=default,
        format_func=lambda c: labels.get(c, c),
    )
    if not chosen:
        st.info("Pick at least one campaign.")
        return

    selected = campaigns[campaigns["campaign_id"].isin(chosen)].copy()
    _fairness_warning(selected, chosen, mode)

    # -- rate matrix -------------------------------------------------------- #
    st.subheader("Stage rates")
    selected["Reach"] = selected["opened"] / selected["targeted"].replace(0, pd.NA)
    selected["Engagement"] = selected["engaged"] / selected["targeted"].replace(0, pd.NA)
    selected["Completion"] = selected["completed"] / selected["targeted"].replace(0, pd.NA)
    st.dataframe(
        selected[
            ["campaign_name", "objective", "channels", "targeted",
             "Reach", "Engagement", "Completion"]
        ].rename(
            columns={
                "campaign_name": "Campaign",
                "objective": "Objective",
                "channels": "Channels",
                "targeted": "Targeted",
            }
        ),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Reach": st.column_config.ProgressColumn(
                format="%.1f%%", min_value=0, max_value=1
            ),
            "Engagement": st.column_config.ProgressColumn(
                format="%.1f%%", min_value=0, max_value=1
            ),
            "Completion": st.column_config.ProgressColumn(
                format="%.1f%%", min_value=0, max_value=1
            ),
            "Targeted": st.column_config.NumberColumn(format="%d"),
        },
    )
    st.caption(
        "A blank cell means the campaign ran only on channels with no such stage. "
        "That is n/a, not zero."
    )

    st.markdown("---")

    # -- target vs actual --------------------------------------------------- #
    st.subheader("Against target")
    targets = query_df(
        data_access.connection(),
        f"""
        SELECT campaign_id, campaign_name, target_completion_rate, target_engagement_rate
        FROM dim_campaign
        WHERE campaign_id IN ({", ".join("?" for _ in chosen)})
        """,
        chosen,
    )
    merged = selected.merge(targets, on="campaign_id", how="left", suffixes=("", "_t"))

    metric = st.radio(
        "Measure", ["Engagement", "Completion"], horizontal=True, label_visibility="collapsed"
    )
    target_column = (
        "target_engagement_rate" if metric == "Engagement" else "target_completion_rate"
    )
    usable = merged[merged[metric].notna() & merged[target_column].notna()]
    if usable.empty:
        st.caption(
            f"No campaign in this selection has both a measured {metric.lower()} rate "
            f"and a {metric.lower()} target in the campaign registry."
        )
    else:
        st.plotly_chart(
            bullet_chart(
                list(usable["campaign_name"]),
                list(usable[metric].astype(float)),
                list(usable[target_column].astype(float)),
                mode,
            ),
            use_container_width=True,
            config={"displayModeBar": False},
        )

    st.markdown("---")

    # -- funnel small multiples --------------------------------------------- #
    st.subheader("Funnels")
    for campaign_id in chosen:
        campaign_filters = filters.with_(campaign_ids=(campaign_id,))
        block = data_access.funnel_by_channel(campaign_filters, version)
        if block.empty:
            continue
        st.markdown(f"**{labels.get(campaign_id, campaign_id)}**")
        st.plotly_chart(
            channel_funnel_chart(block, colours, mode, height=300),
            use_container_width=True,
            config={"displayModeBar": False},
            key=f"funnel_{campaign_id}",
        )

    st.markdown("---")

    # -- channel effectiveness, within campaign only ------------------------ #
    st.subheader("Channel effectiveness within a campaign")
    st.caption(
        "Deliberately scoped to one campaign at a time. Across campaigns, "
        "channels serve different audiences with different intent, so a global "
        "league table mostly measures the audience rather than the channel."
    )
    focus = st.selectbox(
        "Campaign", chosen, format_func=lambda c: labels.get(c, c), key="cei_campaign"
    )
    index = data_access.effectiveness_index(
        filters.with_(campaign_ids=(focus,)), version
    )
    if index.empty or index["index"].isna().all():
        st.caption("Not enough comparable rates in this campaign.")
    else:
        display = index.dropna(subset=["index"]).copy()
        display["Channel"] = display["channel"].map(
            lambda c: colours and c
        )
        st.dataframe(
            index.rename(
                columns={
                    "channel": "Channel",
                    "index": "Effectiveness index",
                    "components": "Rates used",
                    "basis": "Composed from",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "0–100, weighted across only the rates each channel supports and "
            "renormalised — so a channel is not penalised for lacking a stage it "
            "was never able to measure."
        )


def _fairness_warning(selected: pd.DataFrame, chosen: list[str], mode: str) -> None:
    """Warn when the selected campaigns are not really comparable.

    Two campaigns with audiences an order of magnitude apart, or with different
    objectives, will produce rates that look comparable and are not.
    """
    if len(chosen) < 2:
        return
    notes: list[str] = []

    sizes = selected["targeted"].dropna()
    if len(sizes) >= 2 and sizes.min() > 0 and (sizes.max() / sizes.min()) >= 4:
        notes.append(
            f"audience sizes differ by more than 4x "
            f"({sizes.min():,.0f} to {sizes.max():,.0f})"
        )
    objectives = selected["objective"].dropna().unique()
    if len(objectives) > 1:
        notes.append(f"different objectives ({', '.join(sorted(objectives))})")
    channel_counts = selected["channels"].dropna().unique()
    if len(channel_counts) > 1:
        notes.append("different channel mixes")

    if notes:
        st.warning(
            "These campaigns are not like for like — " + "; ".join(notes)
            + ". Rates are still correct, but comparing them directly will mostly "
            "tell you about the differences above rather than about the "
            "communication.",
            icon=":material/balance:",
        )
