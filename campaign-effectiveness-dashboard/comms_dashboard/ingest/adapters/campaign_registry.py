"""Campaign registry — the comms team's own list of what they ran.

Supplies the names, objectives, owners and per-campaign targets that the
platform exports do not carry, so the dashboard can show "Cyber Awareness 2026"
against a 95% completion target instead of a slug with no context.
"""

from __future__ import annotations

import pandas as pd

from ..base import LoadContext, SourceAdapter
from ..registry import register_adapter


@register_adapter
class CampaignRegistryAdapter(SourceAdapter):
    source = "campaign_registry"
    grain = "dimension"

    def post_process(self, canonical: pd.DataFrame, ctx: LoadContext) -> pd.DataFrame:
        if "mandatory" in canonical.columns:
            canonical["mandatory"] = canonical["mandatory"].map(
                lambda v: False if v is None or pd.isna(v) else bool(v)
            )
        return canonical
