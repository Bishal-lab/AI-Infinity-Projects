"""One channel at a time, in its own vocabulary."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import data_access
from components.charts import dwell_histogram, rate_comparison_chart, score_histogram
from components.kpi_cards import caveat_list, kpi_row
from components.theme import CHROME, channel_colours, detect_mode
from comms_dashboard.metrics.rag import status_for


def _pass_mark(measures: pd.DataFrame, default: float = 70.0) -> float:
    """The assessment pass mark reported by the export, if there is one."""
    if "pass_mark" not in measures.columns:
        return default
    values = measures["pass_mark"].dropna()
    return float(values.mode().iloc[0]) if not values.empty else default


def render() -> None:
    st.title("Channel deep dive")
    version = data_access.data_version()
    if not version:
        st.info("Load some data to see this page.")
        return

    filters = st.session_state["active_filters"]
    mode = detect_mode()
    chrome = CHROME[mode]
    per_channel = data_access.funnel_by_channel(filters, version)
    if per_channel.empty:
        st.info("No channel data in this view.")
        return

    options = per_channel[["channel", "channel_display"]].drop_duplicates()
    labels = dict(zip(options["channel"], options["channel_display"]))
    channel = st.selectbox(
        "Channel", list(labels), format_func=lambda c: labels.get(c, c)
    )
    block = per_channel[per_channel["channel"] == channel].sort_values("stage_ordinal")

    st.markdown(f"### {labels.get(channel, channel)}")

    # -- the ladder in this channel's own words ----------------------------- #
    st.subheader("Stage ladder")
    ladder = block[["stage", "native_name", "metric_value", "measure_unit",
                    "support_status", "caveat"]].copy()
    ladder["Reported as"] = ladder["native_name"].fillna("—")
    # Without this an absent caveat renders as the literal string "None".
    ladder["caveat"] = ladder["caveat"].fillna("")
    ladder["Value"] = [
        f"{v:,.0f} {u}" if pd.notna(v) and s == "measured"
        else ("n/a" if s == "not_applicable" else "not measured")
        for v, u, s in zip(ladder["metric_value"], ladder["measure_unit"],
                           ladder["support_status"])
    ]
    st.dataframe(
        ladder[["stage", "Reported as", "Value", "caveat"]].rename(
            columns={"stage": "Canonical stage", "caveat": "How to read it"}
        ),
        use_container_width=True,
        hide_index=True,
    )

    # -- this channel's KPIs ------------------------------------------------ #
    all_kpis = data_access.channel_kpis(filters, version)
    kpis = all_kpis.get(channel, {})
    renderable = {k: v for k, v in kpis.items() if v.status.value != "not_applicable"}
    if renderable:
        st.subheader("Rates")
        # Four to a row keeps the tiles readable at laptop width.
        items = list(renderable.items())
        for start in range(0, len(items), 4):
            chunk = dict(items[start:start + 4])
            kpi_row(chunk, {k: status_for(v) for k, v in chunk.items()}, mode)
        caveat_list(renderable)

    not_applicable = [v.label for v in kpis.values() if v.status.value == "not_applicable"]
    if not_applicable:
        st.caption(
            "Not applicable to this channel: " + ", ".join(not_applicable)
            + " — the platform has no such stage."
        )

    st.markdown("---")

    # -- detractors --------------------------------------------------------- #
    extras = data_access.channel_extras(filters, channel, version)
    if not extras.empty:
        totals = extras.sum(numeric_only=True)
        recipients = totals.get("recipients", 0)
        columns = st.columns(4)
        columns[0].metric("People in scope", f"{recipients:,.0f}")
        if totals.get("bounced", 0) or channel == "email":
            columns[1].metric(
                "Bounced",
                f"{totals.get('bounced', 0):,.0f}",
                f"{totals.get('bounced', 0) / recipients:.1%}" if recipients else None,
                delta_color="inverse",
            )
        if totals.get("opted_out", 0) or channel in ("email", "whatsapp"):
            columns[2].metric(
                "Opted out",
                f"{totals.get('opted_out', 0):,.0f}",
                f"{totals.get('opted_out', 0) / recipients:.1%}" if recipients else None,
                delta_color="inverse",
            )
        median_hours = extras["median_hours_to_engage"].dropna()
        if not median_hours.empty:
            columns[3].metric(
                "Median time to engage", f"{median_hours.median():.0f} h"
            )

    # -- channel-specific panels -------------------------------------------- #
    measures = data_access.recipient_measures(filters, channel, version)

    if channel == "teams_webinar" and not measures.empty:
        st.subheader("How long attendees stayed")
        session = measures["session_minutes"].dropna()
        st.plotly_chart(
            dwell_histogram(
                measures["attendance_minutes"],
                float(session.median()) if not session.empty else None,
                mode,
            ),
            use_container_width=True,
            config={"displayModeBar": False},
        )
        no_show = int(extras["registered_no_show"].sum()) if not extras.empty else 0
        st.caption(
            f"{no_show:,} people registered and did not join. Completion for this "
            f"channel means attending to the configured share of the session, not "
            f"merely joining."
        )

    if channel == "lms" and not measures.empty:
        left, right = st.columns(2, gap="large")
        with left:
            st.subheader("Assessment scores")
            st.plotly_chart(
                score_histogram(
                    measures["score"],
                    # The course's own pass mark, where the export supplies it.
                    # A hard-coded default would draw the threshold line in the
                    # wrong place for any course that does not use it.
                    pass_mark=_pass_mark(measures),
                    mode=mode,
                ),
                use_container_width=True,
                config={"displayModeBar": False},
            )
        with right:
            st.subheader("Progress of those who did not complete")
            partial = measures[(measures["completed"] != True) &  # noqa: E712
                               measures["progress_pct"].notna()]
            if partial.empty:
                st.caption("No partial progress recorded.")
            else:
                st.bar_chart(
                    partial["progress_pct"].mul(100).round(-1).value_counts().sort_index(),
                    color=chrome["grid"],
                )
                st.caption(
                    "Where people stopped. A cluster near 100% is a different "
                    "problem from a cluster near 0%."
                )

    if channel == "viva_engage":
        st.info(
            "Viva Engage reports per post, not per person. Its figures cannot "
            "contribute to workforce coverage or cross-channel deduplication, and "
            "its engagement is counted in **interactions**, not people — one person "
            "can react and comment on the same post.",
            icon=":material/info:",
        )

    if channel == "email":
        st.warning(
            "Email open rate is structurally unreliable: Apple Mail Privacy "
            "Protection pre-fetches tracking pixels (inflating opens), Outlook "
            "image blocking suppresses them (deflating opens), and corporate link "
            "scanners click every URL (inflating clicks). Read it as directional "
            "only, and lead with engagement.",
            icon=":material/warning:",
        )

    if channel == "whatsapp":
        st.warning(
            "WhatsApp read receipts can be switched off by the recipient, which "
            "understates reads by an amount the export cannot show.",
            icon=":material/warning:",
        )

    # -- per-campaign breakdown for this channel ---------------------------- #
    st.markdown("---")
    st.subheader("By campaign")
    campaign_filters = filters.with_(channels=(channel,))
    campaigns = data_access.campaign_table(campaign_filters, version)
    if campaigns.empty:
        st.info("No campaigns used this channel in the current view.")
        return
    campaigns = campaigns.copy()
    campaigns["engagement_rate"] = campaigns["engaged"] / campaigns["targeted"].replace(0, pd.NA)
    campaigns["channel"] = channel
    campaigns["channel_display"] = campaigns["campaign_name"]
    st.plotly_chart(
        rate_comparison_chart(
            campaigns, channel_colours(data_access.channel_order(), mode),
            "engagement_rate", mode=mode,
        ),
        use_container_width=True,
        config={"displayModeBar": False},
    )
