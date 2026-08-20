"""Red / amber / green status against targets.

Per-campaign targets from the campaign registry override the organisation-wide
defaults in ``settings.yaml``, because a mandatory compliance course and an
optional wellbeing post should not be judged against the same bar.
"""

from __future__ import annotations

import duckdb

from ..config import Settings, Threshold, get_settings
from ..db import query_df
from ..models import KpiValue

#: Status labels carry a symbol as well as a colour. Colour alone excludes
#: readers with colour-vision deficiency, and RAG is exactly the case where the
#: distinction is the whole message.
RAG_SYMBOL = {"green": "●", "amber": "▲", "red": "■", "grey": "–"}
RAG_LABEL = {
    "green": "On target",
    "amber": "Below target",
    "red": "Off target",
    "grey": "No target",
}


def campaign_overrides(
    con: duckdb.DuckDBPyConnection, campaign_id: str
) -> dict[str, float]:
    """Per-campaign targets from the registry, if any were supplied."""
    frame = query_df(
        con,
        "SELECT target_completion_rate, target_engagement_rate "
        "FROM dim_campaign WHERE campaign_id = ?",
        [campaign_id],
    )
    if frame.empty:
        return {}
    row = frame.iloc[0]
    out: dict[str, float] = {}
    if row["target_completion_rate"] is not None and not _isna(row["target_completion_rate"]):
        out["completion_rate"] = float(row["target_completion_rate"])
    if row["target_engagement_rate"] is not None and not _isna(row["target_engagement_rate"]):
        out["engagement_rate"] = float(row["target_engagement_rate"])
    return out


def _isna(value: object) -> bool:
    try:
        import pandas as pd

        return bool(pd.isna(value))
    except (TypeError, ValueError):  # pragma: no cover
        return False


def threshold_for(
    key: str,
    settings: Settings | None = None,
    overrides: dict[str, float] | None = None,
) -> Threshold | None:
    settings = settings or get_settings()
    thresholds = settings.thresholds
    base = thresholds.get(key)
    if overrides and key in overrides:
        amber = base.amber_factor if base else 0.85
        return Threshold(target=overrides[key], amber_factor=amber)
    return base


def status_for(
    kpi: KpiValue,
    settings: Settings | None = None,
    overrides: dict[str, float] | None = None,
) -> tuple[str, str]:
    """Return ``(rag, explanation)`` for a KPI.

    A KPI that is not applicable, not measured or suppressed gets ``grey`` and
    says why — it is never quietly scored as failing.
    """
    settings = settings or get_settings()
    if kpi.suppressed:
        return "grey", "suppressed to protect a small group"
    if not kpi.is_renderable:
        return "grey", kpi.display()

    threshold = threshold_for(kpi.key, settings, overrides)
    if threshold is None:
        return "grey", "no target configured"

    rag = threshold.rag(kpi.value)
    delta = kpi.value - threshold.target
    return rag, (
        f"{RAG_LABEL[rag]} — {kpi.value:.1%} against a target of "
        f"{threshold.target:.0%} ({delta:+.1%})"
    )
