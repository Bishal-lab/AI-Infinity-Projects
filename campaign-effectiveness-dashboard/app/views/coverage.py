"""Audience and coverage — who the campaigns actually reached, and who they missed.

Every breakdown on this page can single out a small group of named colleagues, so
suppression is applied in the query layer and its effect is stated on screen
rather than left implicit.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import data_access
from components.charts import coverage_gap_chart, coverage_heatmap
from components.theme import detect_mode
from comms_dashboard.config import get_settings
from comms_dashboard.metrics.coverage import SEGMENT_COLUMNS


def render() -> None:
    st.title("Audience & coverage")
    version = data_access.data_version()
    if not version:
        st.info("Load some data to see this page.")
        return

    settings = get_settings()
    filters = st.session_state["active_filters"]
    mode = detect_mode()

    resolution = data_access.identity_resolution(version)
    if resolution.empty or resolution["resolved"].sum() == 0:
        st.warning(
            "**No employee roster loaded.** Coverage analysis needs an HR extract "
            "so that a WhatsApp number and an LMS login can be recognised as the "
            "same person. Drop a roster export into `data/inbox/` and load it on "
            "the Data & admin page.",
            icon=":material/groups:",
        )
        return

    st.caption(f"Showing: {filters.summary}")
    _resolution_panel(resolution, settings)

    segment = st.selectbox(
        "Break down by",
        list(SEGMENT_COLUMNS),
        format_func=lambda s: SEGMENT_COLUMNS[s],
    )
    frame = data_access.coverage_by_segment(filters, segment, version)
    if frame.empty:
        st.info("No employees match the current filters.")
        return

    suppressed = int(frame["suppressed"].sum()) if "suppressed" in frame else 0

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Reached vs never reached")
        st.plotly_chart(
            coverage_gap_chart(frame, mode), use_container_width=True,
            config={"displayModeBar": False},
        )
    with right:
        st.subheader("The gap")
        visible = frame[~frame["suppressed"]] if "suppressed" in frame else frame
        worst = visible.dropna(subset=["coverage_rate"]).nsmallest(5, "coverage_rate")
        if worst.empty:
            st.caption("No comparable groups.")
        else:
            for _, row in worst.iterrows():
                missed = row["never_reached"]
                # A zero gap is not a negative delta — showing it in red with an
                # arrow reads as bad news about the best possible outcome.
                st.metric(
                    row["segment"],
                    f"{row['coverage_rate']:.0%} reached",
                    f"{missed:,.0f} never reached",
                    delta_color="inverse" if missed else "off",
                )

    if suppressed:
        st.info(
            f"{suppressed} group(s) are suppressed because they contain fewer than "
            f"{settings.min_group_size} people"
            + (
                ", including one hidden purely so the others cannot be recovered by "
                "subtraction."
                if settings.complementary_suppression
                else "."
            ),
            icon=":material/lock:",
        )

    st.markdown("---")

    st.subheader(f"Which channels land in each {SEGMENT_COLUMNS[segment].lower()}")
    mix = data_access.channel_mix_by_segment(filters, segment, version)
    if mix.empty:
        st.caption("No channel data for these segments.")
    else:
        st.plotly_chart(
            coverage_heatmap(mix, mode), use_container_width=True,
            config={"displayModeBar": False},
        )
        st.caption(
            "Share of each group that was targeted on a channel and went on to see "
            "the message. A dark row that goes pale in one column is a channel that "
            "does not land with that group."
        )
        with st.expander("Channel mix as a table"):
            st.dataframe(
                mix.rename(
                    columns={
                        "segment": SEGMENT_COLUMNS[segment],
                        "channel": "Channel",
                        "targeted": "Targeted",
                        "reached": "Reached",
                        "reach_rate": "Reach rate",
                    }
                ),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Reach rate": st.column_config.NumberColumn(format="%.1f%%")
                },
            )

    st.markdown("---")

    left, right = st.columns([1, 1], gap="large")
    with left:
        st.subheader("How many channels reached each person")
        exposure = data_access.multi_channel_exposure(filters, version)
        if exposure.empty:
            st.caption("No exposure data.")
        else:
            exposure = exposure.rename(
                columns={"channels": "Channels reached on", "employees": "Employees"}
            )
            st.dataframe(exposure, use_container_width=True, hide_index=True)
            zero = exposure[exposure["Channels reached on"] == 0]
            if not zero.empty and pd.notna(zero.iloc[0]["Employees"]):
                st.caption(
                    f"{zero.iloc[0]['Employees']:,.0f} people were targeted but saw "
                    f"the campaign on no channel at all."
                )

    with right:
        st.subheader("Coverage table")
        display = frame.copy()
        display["status"] = display["suppressed"].map(
            {True: "suppressed", False: ""}
        ) if "suppressed" in display else ""
        st.dataframe(
            display[
                ["segment", "headcount", "targeted", "reached", "engaged",
                 "coverage_rate", "status"]
            ].rename(
                columns={
                    "segment": SEGMENT_COLUMNS[segment],
                    "headcount": "Headcount",
                    "targeted": "Targeted",
                    "reached": "Reached",
                    "engaged": "Engaged",
                    "coverage_rate": "Coverage",
                    "status": "",
                }
            ),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Coverage": st.column_config.NumberColumn(format="%.1f%%")
            },
        )


def _resolution_panel(resolution: pd.DataFrame, settings) -> None:
    """Say plainly how much of the workforce the dashboard can actually see."""
    overall = resolution["resolved"].sum() / max(resolution["recipients"].sum(), 1)
    weak = resolution[resolution["resolution_rate"] < settings.dedupe_min_resolution]
    with st.expander(
        f"Identity matching: {overall:.0%} of recipient records matched to an employee",
        expanded=not weak.empty,
    ):
        st.dataframe(
            resolution[["channel_display", "recipients", "resolved", "resolution_rate"]]
            .rename(
                columns={
                    "channel_display": "Channel",
                    "recipients": "Recipient records",
                    "resolved": "Matched to an employee",
                    "resolution_rate": "Match rate",
                }
            ),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Match rate": st.column_config.NumberColumn(format="%.1f%%")
            },
        )
        if not weak.empty:
            st.warning(
                "Below "
                f"{settings.dedupe_min_resolution:.0%} match: "
                + ", ".join(weak["channel_display"].fillna(weak["channel"]))
                + ". Coverage figures understate these channels — the unmatched "
                "recipients are real people the dashboard cannot place in a "
                "department. Add employee IDs or work emails to those exports.",
                icon=":material/warning:",
            )
        st.caption(
            "Aggregate-only channels (Viva Engage) report per post and have no "
            "person-level records at all, so they never appear here and cannot "
            "contribute to coverage."
        )
