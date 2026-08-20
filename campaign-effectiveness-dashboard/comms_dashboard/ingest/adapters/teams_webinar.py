"""MS Teams webinar / town hall attendance report.

Channel-specific bits:

* An **attendance-only report cannot tell you reach**. The plain Teams export
  lists the people who turned up and nobody else, so treating each row as
  "targeted" would report the attendee count as the audience and an attendance
  rate of 100%. When the file carries no registration and no explicit attended
  flag, Targeted and Registered are left unknown.
* COMPLETED is derived from dwell time — attending two minutes of a sixty-minute
  town hall is not the same as attending it, and only the ratio separates them.
  Where the export has no scheduled-duration column, the session length is
  derived from the observed meeting span.
* The participant name often carries the location, which is the only segment
  dimension a Teams report offers.
"""

from __future__ import annotations

import re

import pandas as pd

from ..base import LoadContext, SourceAdapter
from ..registry import register_adapter

#: "Participant 4 (Chennai - Agency)" -> "Chennai"
_LOCATION_IN_NAME = re.compile(r"\(([^)]+)\)")

_INTERACTION_COLUMNS = ("camera_on", "raise_hands", "unmute")


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

        canonical = self._blank_reach_for_attendance_only_report(canonical, ctx)
        canonical["session_minutes"] = self._session_minutes(canonical, ctx)
        canonical["completed"] = self._derive_completion(canonical, ctx)
        canonical = self._location_from_name(canonical, ctx)
        canonical = self._sum_interactions(canonical)
        return canonical

    # -- reach ------------------------------------------------------------- #

    @staticmethod
    def _blank_reach_for_attendance_only_report(
        canonical: pd.DataFrame, ctx: LoadContext
    ) -> pd.DataFrame:
        """Leave Targeted and Registered unknown when the file is attendees-only.

        A report with a registration column, or an explicit ``Attended`` yes/no,
        enumerates the invited population — those rows legitimately count as
        targeted. A report with neither enumerates only the people who joined,
        and its row count is an attendance figure wearing an audience's clothes.
        """
        if {"opened", "engaged"} & ctx.mapped_fields:
            return canonical

        canonical["targeted"] = None
        canonical["opened"] = None
        ctx.report.add_issue(
            "warning",
            "ATTENDANCE_ONLY_REPORT",
            "this Teams export lists attendees only — it has no registration "
            "column and no invite list. Targeted and Registered are reported as "
            "'not measured' rather than as the attendee count, so attendance "
            "rate is not shown as a misleading 100%. Export the event's "
            "registration report as well to measure those stages.",
        )
        return canonical

    # -- dwell ------------------------------------------------------------- #

    @staticmethod
    def _session_minutes(canonical: pd.DataFrame, ctx: LoadContext) -> pd.Series:
        """Session length, from the export where possible, else the observed span."""
        existing = canonical.get("session_minutes")
        if existing is not None and existing.notna().any():
            return existing

        joined = pd.to_datetime(canonical.get("engaged_at"), errors="coerce")
        left = pd.to_datetime(canonical.get("left_at"), errors="coerce")
        if joined is None or left is None or joined.isna().all() or left.isna().all():
            return pd.Series([None] * len(canonical), index=canonical.index, dtype="object")

        span = (left.max() - joined.min()).total_seconds() / 60.0
        if not span or span <= 0:
            return pd.Series([None] * len(canonical), index=canonical.index, dtype="object")

        ctx.report.add_issue(
            "info",
            "SESSION_LENGTH_DERIVED",
            f"no scheduled-duration column, so the session length was derived "
            f"from the observed span (first join to last leave) as "
            f"{span:.0f} minutes. That span is an upper bound on the real "
            f"session, so the completion figure reads conservatively.",
        )
        return pd.Series([span] * len(canonical), index=canonical.index, dtype="float64")

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

    # -- segments and interactions ----------------------------------------- #

    @staticmethod
    def _location_from_name(canonical: pd.DataFrame, ctx: LoadContext) -> pd.DataFrame:
        """Pull the location out of a participant name, when nothing better exists."""
        names = canonical.get("participant_name")
        if names is None:
            return canonical
        existing = canonical.get("department")
        if existing is not None and existing.notna().any():
            return canonical

        def location(value: object) -> str | None:
            if value is None or pd.isna(value):
                return None
            match = _LOCATION_IN_NAME.search(str(value))
            if not match:
                return None
            # "(Chennai - Agency)" -> "Chennai"; a bare "(Chennai)" also works.
            return match.group(1).split("-")[0].strip() or None

        derived = names.map(location)
        if derived.notna().any():
            canonical["department"] = derived
            ctx.report.add_issue(
                "info",
                "LOCATION_FROM_NAME",
                "no department column, so the location was read from the "
                "participant name for use as the segment dimension.",
            )
        return canonical

    @staticmethod
    def _sum_interactions(canonical: pd.DataFrame) -> pd.DataFrame:
        """Roll the in-meeting engagement counters into one interaction count."""
        present = [c for c in _INTERACTION_COLUMNS if c in canonical.columns]
        if present:
            canonical["interactions"] = (
                canonical[present].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
            )
        return canonical
