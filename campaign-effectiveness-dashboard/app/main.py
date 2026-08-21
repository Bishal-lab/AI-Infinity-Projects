"""Campaign Communication Effectiveness — Streamlit entrypoint.

Run with:  streamlit run app/main.py

Uses ``st.navigation`` rather than the magic ``pages/`` directory so the shared
filter sidebar and the DuckDB connection are built once here, and each page is
an ordinary function that receives them.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402

from comms_dashboard.config import get_settings  # noqa: E402

import data_access  # noqa: E402
from components import filters as filter_ui  # noqa: E402
from components.theme import CHROME, detect_mode  # noqa: E402

settings = get_settings()

st.set_page_config(
    page_title=settings.title,
    # No emoji page_icon: Streamlit renders emoji favicons by fetching a PNG from
    # the twemoji CDN, which is a real outbound request and fails on an
    # air-gapped network. Streamlit's own bundled favicon is served locally.
    layout="wide",
    initial_sidebar_state="expanded",
)


def _header() -> None:
    mode = detect_mode()
    chrome = CHROME[mode]
    st.markdown(
        f"""
        <div style="margin-bottom:4px;">
          <span style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;
                       color:{chrome['muted']};">{settings.org_name}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _freshness_banner() -> None:
    version = data_access.data_version()
    if version == 0:
        st.warning(
            "**No data loaded yet.** Open **Data & admin** to load the bundled "
            "sample exports and see the dashboard working, or drop your own "
            "platform exports into `data/inbox/` first.",
        )
        return

    quality = data_access.data_quality(version)
    if quality.empty:
        return
    row = quality.iloc[0]
    last = row["last_load_at"]
    parts = [f"Last load: **{last:%d %b %Y, %H:%M}**" if last is not None else "Never loaded"]
    if row["batches_with_warnings"]:
        parts.append(f"{int(row['batches_with_warnings'])} file(s) loaded with warnings")
    if row["batches_rejected"]:
        parts.append(f"**{int(row['batches_rejected'])} file(s) rejected**")
    message = " · ".join(parts)
    if row["batches_rejected"]:
        st.warning(f"{message} — see Data & admin.")
    else:
        st.caption(message)


def main() -> None:
    version = data_access.data_version()
    con = data_access.connection()
    dedupe_ok, dedupe_reason = (False, "no data loaded yet")
    if version:
        from comms_dashboard.metrics import FilterState

        dedupe_ok, dedupe_reason = data_access.dedupe_availability(FilterState(), version)

    active = filter_ui.render_sidebar(con, dedupe_ok, dedupe_reason)
    st.session_state["active_filters"] = active

    with st.sidebar:
        st.markdown("---")
        st.caption(
            "Runs entirely on this machine. No data leaves the network — see the "
            "Definitions page for the privacy controls in force."
        )

    from views import (
        admin,
        campaign_comparison,
        channel_deep_dive,
        coverage,
        definitions,
        executive,
    )

    # Every page function is called render(), so st.navigation cannot infer
    # distinct URLs from them — url_path is set explicitly.
    #
    # No icons on the nav entries: Streamlit renders st.Page icons by fetching an
    # SVG from fonts.gstatic.com (emoji icons go to the twemoji CDN instead).
    # Both fail on an air-gapped network, so the titles stand alone. Icons inside
    # st.warning/st.info are fine — those use the Material Symbols webfont that
    # ships inside Streamlit's own static assets.
    pages = [
        st.Page(executive.render, title="Executive overview", url_path="overview", default=True),
        st.Page(channel_deep_dive.render, title="Channel deep dive",
                url_path="channels"),
        st.Page(campaign_comparison.render, title="Campaign comparison",
                url_path="campaigns"),
        st.Page(coverage.render, title="Audience & coverage", url_path="coverage"),
        st.Page(admin.render, title="Data & admin", url_path="admin"),
        st.Page(definitions.render, title="Definitions & settings",
                url_path="definitions"),
    ]
    page = st.navigation(pages)
    _header()
    _freshness_banner()
    page.run()


main()
