"""Definitions & settings.

Non-negotiable for a management tool: every number on every page must be one
click from its definition, including the reasons it might not be a number at all.
The KPI table is rendered from the same ``KPI_DEFINITIONS`` the metrics layer
computes from, so the documentation cannot drift away from the arithmetic.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import data_access
from comms_dashboard import __version__
from comms_dashboard.config import get_settings

STATUS_EXPLANATION = {
    "measured": "The platform reports it and the export carried it.",
    "not_applicable": (
        "The platform has no such stage. Shown as n/a and excluded from both "
        "sides of any rate — never counted as zero."
    ),
    "not_measured": (
        "The platform can report it, but the export loaded did not include it. "
        "Also excluded rather than zeroed."
    ),
}


def render() -> None:
    st.title("Definitions & settings")
    settings = get_settings()
    version = data_access.data_version()

    tabs = st.tabs(["KPI definitions", "Stage ladder", "Targets", "Privacy", "About"])

    with tabs[0]:
        _kpi_tab()
    with tabs[1]:
        _ladder_tab(version)
    with tabs[2]:
        _targets_tab(settings)
    with tabs[3]:
        _privacy_tab(settings)
    with tabs[4]:
        _about_tab(settings)


def _kpi_tab() -> None:
    from comms_dashboard.metrics.kpis import KPI_DEFINITIONS

    st.subheader("How every rate is calculated")
    st.caption(
        "Rendered from the same definitions the dashboard computes with, so these "
        "formulas are the ones actually in use."
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "KPI": d.label,
                    "Formula": d.formula,
                    "Applies to": ", ".join(d.channels) if d.channels else "all channels",
                    "What it means": d.description,
                }
                for d in KPI_DEFINITIONS
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("### Cross-channel rates")
    st.markdown(
        """
Channels do not all measure the same stages, so a headline rate restricts
**both** its numerator and its denominator to the channels that measure both
stages. Adding every channel's opens and dividing by every channel's deliveries
would mix two different populations — with the bundled sample data that produces
an open rate of 126%, and with real data it would produce something merely
plausible and still wrong. Each headline tile names its basis.
        """
    )

    st.markdown("### When a rate is not a number")
    st.markdown(
        """
- **n/a** — no channel in view measures that stage, or the two sides count
  different things (interactions cannot be divided by people).
- **not measured** — the channel supports the stage but the export loaded did
  not carry it.
- **suppressed** — the group is smaller than the disclosure threshold.

None of these are ever shown as `0`. A zero is a measurement; these are the
absence of one.
        """
    )

    st.markdown("### Known measurement caveats")
    st.markdown(
        """
- **Email open rate is unreliable.** Apple Mail Privacy Protection pre-fetches
  tracking pixels (inflating opens); Outlook image blocking suppresses them
  (deflating opens); corporate link scanners click every URL (inflating clicks).
  Lead with engagement, and treat open rate as directional.
- **WhatsApp read receipts can be switched off** by the recipient, understating
  reads by an amount the export cannot reveal.
- **Viva Engage engagement counts interactions, not people.** One person can
  react, comment and share the same post.
- **Viva Engage reach is a lower bound.** Viewers are unique within a post, but
  post-level data cannot say whether the same person saw two posts, so the
  largest single post's unique viewers is used rather than a sum.
- **The trend line tracks campaign launches**, not trickling engagement: every
  stage for a person is attributed to the period the campaign reached them, so
  that all five stages of a campaign share one denominator.
        """
    )


def _ladder_tab(version: int) -> None:
    st.subheader("What each platform can and cannot measure")
    st.caption(
        "From `config/stage_ladder.yaml`. Editing that file changes every number "
        "and label in the dashboard, with no code change."
    )
    matrix = data_access.stage_support_matrix(version)
    if matrix.empty:
        st.info("No stage matrix loaded.")
        return

    pivot = matrix.pivot(index="channel", columns="stage", values="support_status")
    stage_order = [
        s for s in ["TARGETED", "DELIVERED", "OPENED", "ENGAGED", "COMPLETED"]
        if s in pivot.columns
    ]
    symbols = {"measured": "✅ measured", "not_applicable": "— n/a",
               "not_measured": "◌ not measured"}
    st.dataframe(
        pivot[stage_order].map(lambda v: symbols.get(v, v)),
        use_container_width=True,
    )

    for status, explanation in STATUS_EXPLANATION.items():
        st.markdown(f"- **{symbols.get(status, status)}** — {explanation}")

    st.markdown("### Native names and notes")
    notes = matrix.copy()
    for column in ("native_name", "caveat"):
        notes[column] = notes[column].fillna("")
    st.dataframe(
        notes[["channel", "stage", "native_name", "unit", "caveat"]].rename(
            columns={
                "channel": "Channel",
                "stage": "Canonical stage",
                "native_name": "Called this by the platform",
                "unit": "Counts",
                "caveat": "Note",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )


def _targets_tab(settings) -> None:
    st.subheader("Targets and RAG bands")
    st.caption(
        "Organisation defaults from `config/settings.yaml`. A campaign registry "
        "can override completion and engagement targets per campaign — a "
        "mandatory compliance course and an optional wellbeing post should not "
        "be judged against the same bar."
    )
    rows = [
        {
            "KPI": key.replace("_", " ").title(),
            "Green at or above": f"{t.target:.0%}",
            "Amber at or above": f"{t.target * t.amber_factor:.0%}",
            "Red below": f"{t.target * t.amber_factor:.0%}",
        }
        for key, t in settings.thresholds.items()
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(
        "RAG is shown with a symbol and a word as well as a colour, so the status "
        "is readable without colour vision."
    )

    st.markdown("### Rules in force")
    st.dataframe(
        pd.DataFrame(
            [
                {"Setting": "Open rate denominator", "Value": settings.open_rate_basis},
                {
                    "Setting": "Webinar completion threshold",
                    "Value": f"{settings.webinar_completion_fraction:.0%} of the session",
                },
                {
                    "Setting": "Deduplication requires match rate",
                    "Value": f"{settings.dedupe_min_resolution:.0%} on 2+ channels",
                },
                {"Setting": "Date order in exports", "Value": settings.date_order},
                {"Setting": "Period grain", "Value": settings.period_grain},
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )


def _privacy_tab(settings) -> None:
    st.subheader("Privacy controls in force")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Control": "Identifier hashing",
                    "Setting": "on" if settings.hash_identifiers else "OFF",
                    "Effect": (
                        "Email addresses, phone numbers and logins are replaced by "
                        "a keyed HMAC-SHA256 digest before storage."
                    ),
                },
                {
                    "Control": "Minimum group size",
                    "Setting": str(settings.min_group_size),
                    "Effect": "Breakdowns smaller than this are suppressed.",
                },
                {
                    "Control": "Complementary suppression",
                    "Setting": "on" if settings.complementary_suppression else "off",
                    "Effect": (
                        "If one group is hidden, the next-smallest is hidden too — "
                        "otherwise the hidden value is recoverable by subtraction."
                    ),
                },
                {
                    "Control": "Individual drill-down",
                    "Setting": "off" if not settings.allow_individual_drilldown else "ON",
                    "Effect": "No page exposes a named individual's engagement.",
                },
                {
                    "Control": "Retention",
                    "Setting": f"{settings.retention_months} months",
                    "Effect": "Archived exports older than this should be purged.",
                },
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.warning(
        "**Hashing is pseudonymisation, not anonymisation.** Someone who knows "
        "your email convention can hash a name list and match it. The stored data "
        "remains personal data under GDPR/DPDP and comparable regimes. It reduces "
        "casual exposure; it does not discharge your obligations.",
        icon=":material/key:",
    )
    st.info(
        "Measuring individual employees' engagement with internal communications "
        "is consultable or restricted in several jurisdictions — EU works councils "
        "in particular. This dashboard supports aggregate-only operation; get "
        "employee-relations sign-off before go-live.",
        icon=":material/balance:",
    )
    st.error(
        "**Streamlit has no authentication.** Anyone who can reach this port sees "
        "everything. Keep it bound to loopback, or put it behind the corporate "
        "reverse proxy with SSO before sharing it beyond the comms team.",
        icon=":material/lock_open:",
    )


def _about_tab(settings) -> None:
    st.subheader("About")
    st.markdown(
        f"""
**{settings.title}** · version {__version__}

Runs entirely on this machine. At runtime the app makes **no outbound network
calls at all**: charts are drawn by the copy of plotly.js bundled inside
Streamlit's own static assets, DuckDB extension autoloading is disabled, and
Streamlit's usage telemetry is switched off in `.streamlit/config.toml`.

Configuration lives in three places, all editable without touching code:

| File | What it controls |
|---|---|
| `config/settings.yaml` | paths, targets, thresholds, privacy, date order |
| `config/stage_ladder.yaml` | what each channel can measure, and in what units |
| `config/mappings/*.yaml` | which of your export columns map to which field |
        """
    )
    counts = pd.DataFrame(
        [{"Table": k, "Rows": v} for k, v in
         __import__("comms_dashboard.db", fromlist=["db"]).table_counts(
             data_access.connection()).items()]
    )
    st.dataframe(counts, use_container_width=True, hide_index=True)
