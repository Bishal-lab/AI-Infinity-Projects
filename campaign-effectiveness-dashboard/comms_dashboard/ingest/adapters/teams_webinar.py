"""MS Teams webinar / town hall attendance report.

Channel-specific bits:

* COMPLETED is derived from dwell time — attending for two minutes of a
  sixty-minute town hall is not the same as attending it, and only the ratio
  can tell those apart.
* ENGAGED falls back to "spent any time in the session" when the report has no
  explicit attended column, which several Teams report variants do not.
"""

from __future__ import annotations

import pandas as pd

from ..base import LoadContext, SourceAdapter
from ..registry import register_adapter


@register_adapter
class TeamsWebinarAdapter(SourceAdapter):
    source = "teams_webinar"

    def post_process(self, canonical: pd.DataFrame, ctx: LoadContext) -> pd.DataFrame:
        minutes = canonical.get("attendance_minutes")

        if minutes is not None:
            attended_by_time = minutes.map(
                lambda v: None if v is None or pd.isna(v) else bool(float(v) > 0)
            )
            if "engaged" not in canonical.columns:
                canonical["engaged"] = attended_by_time
            else:
                blank = canonical["engaged"].isna()
                canonical.loc[blank, "engaged"] = attended_by_time[blank]

        # Attending proves registration in practice — Teams lets people join a
        # town hall they never registered for, and counting them as unregistered
        # attendees would make the funnel widen at a later stage.
        if "engaged" in canonical.columns and "opened" in canonical.columns:
            attended = canonical["engaged"] == True  # noqa: E712 - object dtype
            canonical.loc[attended & (canonical["opened"] != True), "opened"] = True  # noqa: E712

        canonical["completed"] = self._derive_completion(canonical, ctx)
        return canonical

    @staticmethod
    def _derive_completion(canonical: pd.DataFrame, ctx: LoadContext) -> pd.Series:
        """Attended for at least ``rules.webinar_completion_fraction`` of the session."""
        fraction = ctx.settings.webinar_completion_fraction
        attendance = canonical.get("attendance_minutes")
        session = canonical.get("session_minutes")

        if attendance is None:
            # No dwell data at all: the stage is genuinely unmeasurable for this
            # export, so every row is unknown rather than a failure to complete.
            return pd.Series([None] * len(canonical), index=canonical.index, dtype="object")

        def judge(idx: int) -> bool | None:
            minutes = attendance.iloc[idx]
            if minutes is None or pd.isna(minutes):
                return None
            length = session.iloc[idx] if session is not None else None
            if length is None or pd.isna(length) or float(length) <= 0:
                # Without a session length there is no ratio to test. Fall back
                # to "was there at all" rather than inventing a threshold.
                return bool(float(minutes) > 0)
            return bool(float(minutes) / float(length) >= fraction)

        return pd.Series(
            [judge(i) for i in range(len(canonical))],
            index=canonical.index,
            dtype="object",
        )
