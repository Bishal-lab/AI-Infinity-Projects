"""Email campaign export.

Nothing here needs deriving — the mapping YAML covers it — except tightening the
relationship between delivery and bounce, which exports are inconsistent about.
"""

from __future__ import annotations

import pandas as pd

from ..base import LoadContext, SourceAdapter
from ..registry import register_adapter


@register_adapter
class EmailAdapter(SourceAdapter):
    source = "email"

    def post_process(self, canonical: pd.DataFrame, ctx: LoadContext) -> pd.DataFrame:
        # Some tools ship a bounce column but no delivery status, and some the
        # reverse. Each can stand in for the other, but only where the other is
        # genuinely absent — never overwriting a value the export did give us.
        if "bounced" in canonical.columns and "delivered" in canonical.columns:
            missing = canonical["delivered"].isna() & canonical["bounced"].notna()
            canonical.loc[missing, "delivered"] = ~canonical.loc[missing, "bounced"].astype(bool)

        # An open or a click is proof of delivery even when the status column
        # disagrees; the reverse inference is not safe, so it is not made.
        for column in ("opened", "engaged"):
            if column in canonical.columns and "delivered" in canonical.columns:
                positive = canonical[column] == True  # noqa: E712 - object dtype
                canonical.loc[positive & canonical["delivered"].isna(), "delivered"] = True

        return canonical
