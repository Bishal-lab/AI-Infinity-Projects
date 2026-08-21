"""KPI tiles, RAG badges and caveat rendering.

A tile has one job that matters more than looking tidy: it must not let a number
that could not be measured masquerade as a result. "n/a", "not measured" and
"suppressed" are rendered as distinct, readable states — never as a dash the
reader will assume means zero.

RAG status carries a symbol and a word alongside the colour, because red/amber/
green is precisely the case where colour-vision deficiency loses the message.
"""

from __future__ import annotations

import streamlit as st

from comms_dashboard.metrics.rag import RAG_LABEL, RAG_SYMBOL
from comms_dashboard.models import KpiValue, SupportStatus

from .theme import CHROME, STATUS, Mode

_STATE_HELP = {
    SupportStatus.NOT_APPLICABLE: (
        "This channel has no such stage, so there is nothing to measure. "
        "Shown as n/a rather than zero on purpose."
    ),
    SupportStatus.NOT_MEASURED: (
        "This channel normally reports this, but the export loaded did not "
        "include it. Shown as 'not measured' rather than zero."
    ),
}


def kpi_card(
    kpi: KpiValue,
    rag: str = "grey",
    explanation: str = "",
    mode: Mode = "light",
) -> None:
    """Render one KPI tile."""
    chrome = CHROME[mode]
    colour = STATUS.get(rag, chrome["muted"])
    symbol = RAG_SYMBOL.get(rag, "–")
    value = kpi.display()

    detail = ""
    if kpi.is_renderable and kpi.numerator is not None and kpi.denominator is not None:
        detail = f"{kpi.numerator:,.0f} of {kpi.denominator:,.0f}"
    elif kpi.status in _STATE_HELP:
        detail = _STATE_HELP[kpi.status]
    elif kpi.suppressed:
        detail = "group too small to report"

    st.markdown(
        f"""
        <div style="
            border:1px solid {chrome['border']};
            border-radius:10px;
            padding:14px 16px;
            background:{chrome['surface']};
            height:100%;
        ">
          <div style="font-size:12px;color:{chrome['muted']};
                      text-transform:uppercase;letter-spacing:.04em;">
            {kpi.label}
          </div>
          <div style="font-size:30px;line-height:1.15;margin:6px 0 2px;
                      color:{chrome['text_primary']};font-weight:600;">
            {value}
          </div>
          <div style="font-size:12px;color:{chrome['text_secondary']};min-height:18px;">
            {detail}
          </div>
          <div style="font-size:12px;margin-top:8px;color:{colour};font-weight:600;">
            <span style="font-size:14px;">{symbol}</span>
            {RAG_LABEL.get(rag, '')}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if explanation and rag != "grey":
        st.caption(explanation)


def kpi_row(
    kpis: dict[str, KpiValue],
    statuses: dict[str, tuple[str, str]],
    mode: Mode = "light",
) -> None:
    """A row of tiles, one column each."""
    if not kpis:
        return
    columns = st.columns(len(kpis))
    for column, (key, kpi) in zip(columns, kpis.items()):
        rag, explanation = statuses.get(key, ("grey", ""))
        with column:
            kpi_card(kpi, rag, explanation, mode)


def caveat_list(kpis: dict[str, KpiValue] | list[KpiValue], title: str = "How to read these") -> None:
    """Collect the caveats attached to a set of KPIs into one expander.

    Caveats belong next to the number, not in a footnote nobody opens — but a
    tile has no room, so they are gathered here and the expander sits directly
    under the tiles.
    """
    values = list(kpis.values()) if isinstance(kpis, dict) else list(kpis)
    seen: list[str] = []
    for kpi in values:
        for caveat in kpi.caveats:
            if caveat not in seen:
                seen.append(caveat)
    if not seen:
        return
    with st.expander(title):
        for caveat in seen:
            st.markdown(f"- {caveat}")


def stage_support_note(frame, mode: Mode = "light") -> None:
    """Explain, in words, which stages are missing from the current view and why."""
    if frame.empty or "support_status" not in frame:
        return
    unmeasured = frame[frame["support_status"] != "measured"]
    if unmeasured.empty:
        return
    lines: list[str] = []
    for _, row in unmeasured.iterrows():
        label = row.get("channel_display") or row.get("channel")
        reason = row.get("caveat") or (
            "the platform has no such stage"
            if row["support_status"] == "not_applicable"
            else "the export loaded did not include it"
        )
        lines.append(f"- **{label} — {row['stage'].title()}**: {reason}")
    with st.expander(f"{len(lines)} stage(s) show as n/a or not measured — why"):
        st.markdown("\n".join(lines))
        st.caption(
            "These are excluded from both sides of any rate they would appear in, "
            "rather than counted as zero."
        )
