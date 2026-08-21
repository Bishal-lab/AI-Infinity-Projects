"""Learning Management System enrolment / completion export.

Channel-specific bits:

* ENGAGED is not a column — it is "made progress or attempted the assessment".
* COMPLETED means completed *and* passed where an assessment exists. A course
  marked complete with a failed assessment has not achieved the campaign's
  objective, and reporting it as a completion overstates compliance.
"""

from __future__ import annotations

import pandas as pd

from ..base import LoadContext, SourceAdapter
from ..registry import register_adapter


@register_adapter
class LmsAdapter(SourceAdapter):
    source = "lms"

    def post_process(self, canonical: pd.DataFrame, ctx: LoadContext) -> pd.DataFrame:
        # Many platforms report the start only as a last-access timestamp: a
        # filled cell means the learner opened the course, an empty one means
        # they never did. Where the column exists, absence is a real negative.
        opened_at = canonical.get("opened_at")
        if opened_at is not None:
            derived = opened_at.notna()
            if "opened" not in canonical.columns or canonical["opened"].isna().all():
                canonical["opened"] = derived
            else:
                blank = canonical["opened"].isna()
                canonical.loc[blank, "opened"] = derived[blank]

        progress = canonical.get("progress_pct")
        attempted = canonical.get("assessment_passed")

        def engaged(row: pd.Series) -> bool | None:
            signals: list[bool] = []
            value = row.get("progress_pct")
            if value is not None and not pd.isna(value):
                signals.append(float(value) > 0)
            attempt = row.get("assessment_passed")
            if attempt is not None and not pd.isna(attempt):
                signals.append(True)  # a recorded pass/fail means they attempted
            # A recorded attempt count is engagement in its own right. Someone
            # who sat the assessment seven times and never passed engaged with
            # the course rather more than someone who never opened it, and
            # progress alone misses them: a submitted-but-unfinalised attempt
            # can sit at 0% progress.
            tries = row.get("attempts")
            if tries is not None and not pd.isna(tries):
                signals.append(float(tries) > 0)
            completed = row.get("completed")
            if completed is not None and not pd.isna(completed) and bool(completed):
                signals.append(True)
            if not signals:
                return None
            return any(signals)

        if (
            progress is not None
            or attempted is not None
            or "attempts" in canonical
            or "completed" in canonical
        ):
            canonical["engaged"] = canonical.apply(engaged, axis=1)

        # Completion requires the assessment to have been passed, where one was
        # recorded. Silence in that column is not treated as a failure.
        if "completed" in canonical.columns and "assessment_passed" in canonical.columns:
            failed = canonical["assessment_passed"] == False  # noqa: E712
            canonical.loc[failed, "completed"] = False

        # Completing implies having started, and both imply having progressed.
        if "completed" in canonical.columns and "opened" in canonical.columns:
            done = canonical["completed"] == True  # noqa: E712
            canonical.loc[done & canonical["opened"].isna(), "opened"] = True

        return canonical
