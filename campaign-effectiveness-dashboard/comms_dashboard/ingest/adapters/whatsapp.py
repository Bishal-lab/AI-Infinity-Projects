"""WhatsApp Business API broadcast export.

Channel-specific bit: engagement arrives split across two columns — a free-text
reply and a template button tap — and either one counts.
"""

from __future__ import annotations

import pandas as pd

from ..base import LoadContext, SourceAdapter
from ..registry import register_adapter


@register_adapter
class WhatsAppAdapter(SourceAdapter):
    source = "whatsapp"

    def post_process(self, canonical: pd.DataFrame, ctx: LoadContext) -> pd.DataFrame:
        # Most providers report the read receipt only as a timestamp. Where the
        # column exists, a filled cell means read and an empty one means not
        # read — so absence is a genuine False here, not an unknown.
        opened_at = canonical.get("opened_at")
        if opened_at is not None:
            derived = opened_at.notna()
            if "opened" not in canonical.columns or canonical["opened"].isna().all():
                canonical["opened"] = derived
            else:
                blank = canonical["opened"].isna()
                canonical.loc[blank, "opened"] = derived[blank]

        replied = canonical.get("replied")
        clicked = canonical.get("button_clicked")

        if replied is not None or clicked is not None:
            def combine(row: pd.Series) -> bool | None:
                values = [row.get("replied"), row.get("button_clicked")]
                present = [v for v in values if v is not None and not pd.isna(v)]
                if not present:
                    return None  # neither column told us anything
                return any(bool(v) for v in present)

            canonical["engaged"] = canonical.apply(combine, axis=1)

        # A read receipt is proof of delivery even if the status column lags.
        if "opened" in canonical.columns and "delivered" in canonical.columns:
            read = canonical["opened"] == True  # noqa: E712 - object dtype
            canonical.loc[read & canonical["delivered"].isna(), "delivered"] = True

        return canonical
