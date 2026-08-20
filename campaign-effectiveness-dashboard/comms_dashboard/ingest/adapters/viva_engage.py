"""Viva Engage community post analytics — the one aggregate-only source.

Viva reports per post, not per person. Two consequences run through the whole
dashboard and start here:

* These rows can never contribute to person-level coverage or cross-channel
  deduplication, because there are no people in them.
* ENGAGED is a count of *interactions*, not of people: one person can react,
  comment and share the same post. It is emitted with ``measure_unit='events'``
  so no rate can ever divide it by an audience size.
"""

from __future__ import annotations

import pandas as pd

from ...models import MeasureUnit
from ..base import LoadContext, ParseResult, SourceAdapter
from ..registry import register_adapter


@register_adapter
class VivaEngageAdapter(SourceAdapter):
    source = "viva_engage"
    grain = "aggregate"

    def post_process(self, canonical: pd.DataFrame, ctx: LoadContext) -> pd.DataFrame:
        for column in ("reactions", "comments", "shares"):
            if column not in canonical.columns:
                canonical[column] = 0.0
        canonical["interaction_events"] = (
            canonical[["reactions", "comments", "shares"]].fillna(0).sum(axis=1)
        )

        # Unique viewers is the only person-shaped number Viva gives us. Where
        # it is absent, impressions cannot stand in — impressions count views,
        # and a single person scrolling past a post three times is three of them.
        if "unique_viewers" not in canonical.columns or canonical["unique_viewers"].isna().all():
            ctx.report.add_issue(
                "warning",
                "VIVA_NO_UNIQUE_VIEWERS",
                "this export has no unique-viewers column, so the Opened stage "
                "cannot be counted in people. Impressions are not substituted — "
                "they count views, not viewers.",
                column_name="unique_viewers",
            )
        return canonical

    def to_stage_rows(self, result: ParseResult, ctx: LoadContext) -> pd.DataFrame:
        """Emit aggregate stage rows directly, one per post per stage."""
        canonical = result.canonical
        if canonical.empty:
            return pd.DataFrame()

        post_ids = (
            result.post_ids
            if result.post_ids is not None
            else pd.Series("-", index=canonical.index)
        )
        # Aggregation policy matters more here than anywhere else in the project.
        # The audience size is the same community repeated on every post, and
        # unique viewers are unique only *within* a post — adding either across
        # posts would report several times the workforce as reached.
        stage_specs = [
            ("TARGETED", "audience_size", MeasureUnit.PERSONS, "max"),
            ("OPENED", "unique_viewers", MeasureUnit.PERSONS, "max"),
            ("ENGAGED", "interaction_events", MeasureUnit.EVENTS, "sum"),
        ]

        rows: list[dict] = []
        for stage, column, unit, aggregation in stage_specs:
            if column not in canonical.columns:
                continue
            for position, idx in enumerate(canonical.index):
                value = canonical.at[idx, column]
                if value is None or pd.isna(value):
                    continue
                rows.append(
                    {
                        # Carried so the loader can align each stage row back to
                        # the post it came from, and therefore to its campaign.
                        "row_index": idx,
                        "stage": stage,
                        "period_start": canonical.at[idx, "period_start"],
                        "source_object_id": str(post_ids.iloc[position]),
                        "metric_value": float(value),
                        "measure_unit": unit.value,
                        "aggregation": aggregation,
                    }
                )
        return pd.DataFrame(rows)
