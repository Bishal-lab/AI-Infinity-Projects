"""Employee roster — a dimension, not a campaign fact.

Loading it unlocks coverage by department/region/grade and cross-channel
deduplication. Its real job is populating ``dim_employee_identity``: the table
that lets a WhatsApp phone number and an LMS login resolve to the same person.
"""

from __future__ import annotations

import pandas as pd

from ...ingest import normalize
from ..base import LoadContext, SourceAdapter
from ..registry import register_adapter


@register_adapter
class RosterAdapter(SourceAdapter):
    source = "roster"
    grain = "dimension"

    def post_process(self, canonical: pd.DataFrame, ctx: LoadContext) -> pd.DataFrame:
        # Fold the department spellings the HR extract uses into the taxonomy
        # the dashboard groups by, so "Fin" and "Finance" are one bar, not two.
        aliases = ctx.settings.department_aliases
        if aliases and "department" in canonical.columns:
            canonical["department"] = canonical["department"].map(
                lambda v: normalize.apply_alias_map(v, aliases)
            )
        if "is_active" in canonical.columns:
            canonical["is_active"] = canonical["is_active"].map(
                lambda v: True if v is None or pd.isna(v) else bool(v)
            )
        return canonical
