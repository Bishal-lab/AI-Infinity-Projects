"""Behaviours that only real exports exposed.

These lock the *shapes* three genuine platform exports arrive in, using
fixtures with the same headers and invented values. Every assertion here
corresponds to something that would otherwise have been silently wrong:

* an LMS exporting MM/DD/YYYY while other sources export DD/MM/YYYY
* a Teams attendance report being read as if it described the whole audience
* daily community reach being added up across two hundred days
"""

from __future__ import annotations

import pandas as pd
import pytest

from comms_dashboard.ingest.loader import ingest_file
from comms_dashboard.metrics import FilterState, channel_kpis
from comms_dashboard.metrics.kpis import funnel_by_channel, workforce_coverage
from comms_dashboard.models import SupportStatus

from .conftest import SAMPLES

FORMATS = SAMPLES / "formats"


@pytest.fixture
def formats(con, inbox, settings, ladder):
    """Load all three real-shape fixtures into one campaign."""
    import shutil

    for name, campaign in [
        ("lms_agency_format.csv", None),
        ("teams_attendance_format.xlsx", "sample-course-jun-26"),
        ("viva_daily_format.csv", "sample-community"),
    ]:
        target = inbox / name
        shutil.copy2(FORMATS / name, target)
        report = ingest_file(
            con, target, campaign_override=campaign,
            settings=settings, ladder=ladder, archive=False,
        )
        assert report.status.startswith("loaded"), (name, report.issues)
    return con


def _stage(con, channel: str, stage: str) -> dict:
    frame = funnel_by_channel(con, FilterState())
    row = frame[(frame["channel"] == channel) & (frame["stage"] == stage)]
    assert not row.empty, (channel, stage)
    return row.iloc[0].to_dict()


class TestLmsDateOrder:
    def test_the_mapping_overrides_the_global_day_order(self, mappings, settings):
        """The LMS exports MDY; the global default is DMY. Both must hold."""
        assert settings.date_order == "DMY"
        assert mappings["lms"].date_order == "MDY"
        assert mappings["email"].date_order is None  # inherits the global

    def test_dates_parse_as_month_first(self, formats):
        """A DMY reading would put completions before the assignment date."""
        rows = formats.execute(
            "SELECT targeted_at, completed_at FROM fact_recipient_stage "
            "WHERE channel = 'lms' AND completed_at IS NOT NULL"
        ).df()
        assert not rows.empty
        assert (rows["completed_at"] >= rows["targeted_at"]).all()

    def test_a_bad_per_source_order_is_rejected(self, tmp_path, mappings):
        from comms_dashboard.config import load_source_mapping
        from comms_dashboard.models import ConfigError
        import yaml

        raw = {"source": "x", "read": {"date_order": "YMD"}, "fields": {}}
        path = tmp_path / "x.yaml"
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        with pytest.raises(ConfigError, match="date_order"):
            load_source_mapping(path).date_order


class TestLmsOutcomes:
    def test_tri_state_status_maps_to_completion(self, formats):
        completed = formats.execute(
            "SELECT COUNT(*) FROM fact_recipient_stage "
            "WHERE channel = 'lms' AND completed IS TRUE"
        ).fetchone()[0]
        assert completed == 36  # 'Completed and Passed'; the rest are unfinalised

    def test_a_failed_attempt_still_counts_as_engagement(self, formats):
        """Sitting the assessment seven times and failing is not disengagement.

        Those rows carry 0% progress and no pass/fail, so progress alone would
        miss them entirely — only the attempt count reveals them.
        """
        engaged = formats.execute(
            "SELECT COUNT(*) FROM fact_recipient_stage "
            "WHERE channel = 'lms' AND engaged IS TRUE"
        ).fetchone()[0]
        assert engaged == 40  # every learner, including the four who never passed

    def test_job_role_becomes_the_segment(self, formats):
        roles = formats.execute(
            "SELECT COUNT(DISTINCT department) FROM fact_recipient_stage "
            "WHERE channel = 'lms'"
        ).fetchone()[0]
        assert roles == 6


class TestTeamsAttendanceOnly:
    def test_an_attendance_report_never_claims_reach(self, formats):
        """The fix that matters most.

        Counting each attendee row as 'targeted' would report the attendee count
        as the audience and an attendance rate of exactly 100%.
        """
        claimed = formats.execute(
            "SELECT COUNT(*) FROM fact_recipient_stage "
            "WHERE channel = 'teams_webinar' AND targeted IS TRUE"
        ).fetchone()[0]
        assert claimed == 0

        for stage in ("TARGETED", "OPENED"):
            assert _stage(formats, "teams_webinar", stage)["support_status"] == "not_measured"

    def test_attendance_itself_is_measured(self, formats):
        attended = _stage(formats, "teams_webinar", "ENGAGED")
        assert attended["support_status"] == "measured"
        assert attended["metric_value"] == 40

    def test_rates_needing_an_audience_are_withheld(self, formats):
        kpis = channel_kpis(formats, FilterState())["teams_webinar"]
        for key in ("attendance_rate", "registration_rate", "show_up_rate"):
            assert kpis[key].status is SupportStatus.NOT_MEASURED, key

    def test_the_one_rate_it_can_support_is_offered(self, formats):
        """Completed / attended needs no invite list, so it stays measurable."""
        stayed = channel_kpis(formats, FilterState())["teams_webinar"]["stayed_to_end"]
        assert stayed.is_renderable
        assert 0.0 < stayed.value <= 1.0

    def test_session_length_is_derived_from_the_observed_span(self, formats):
        lengths = formats.execute(
            "SELECT DISTINCT session_minutes FROM fact_recipient_stage "
            "WHERE channel = 'teams_webinar'"
        ).df()["session_minutes"].dropna()
        assert len(lengths) == 1 and lengths.iloc[0] > 0

    def test_location_is_read_out_of_the_participant_name(self, formats):
        cities = formats.execute(
            "SELECT DISTINCT department FROM fact_recipient_stage "
            "WHERE channel = 'teams_webinar' AND department IS NOT NULL"
        ).df()["department"].tolist()
        assert "Chennai" in cities
        # "(Chennai - Agency)" must not leak the suffix into the segment name.
        assert all("Agency" not in c and "(" not in c for c in cities)

    def test_both_duration_spellings_parse(self, formats):
        missing = formats.execute(
            "SELECT COUNT(*) FROM fact_recipient_stage "
            "WHERE channel = 'teams_webinar' AND attendance_minutes IS NULL"
        ).fetchone()[0]
        assert missing == 0


class TestVivaDaily:
    def test_daily_reach_is_never_summed(self, formats):
        """Adding reach across days claims many times the community."""
        raw = pd.read_csv(FORMATS / "viva_daily_format.csv")
        opened = _stage(formats, "viva_engage", "OPENED")["metric_value"]
        assert opened == raw["People reached"].max()
        assert opened < raw["People reached"].sum()

    def test_no_community_size_means_no_denominator(self, formats):
        assert _stage(formats, "viva_engage", "TARGETED")["support_status"] == "not_measured"

    def test_member_and_non_member_columns_are_summed(self, formats):
        raw = pd.read_csv(FORMATS / "viva_daily_format.csv")
        expected = raw[[
            "Members and admins reactions on messages",
            "Non-members reactions on messages",
            "Members and admins messages posted",
            "Non-members messages posted",
            "Total answers", "Total questions",
        ]].sum().sum()
        assert _stage(formats, "viva_engage", "ENGAGED")["metric_value"] == expected

    def test_engagement_stays_in_events(self, formats):
        assert _stage(formats, "viva_engage", "ENGAGED")["measure_unit"] == "events"


class TestCoverageWithoutARoster:
    def test_coverage_is_not_measured_rather_than_zero(self, formats, settings):
        """With no roster, 0 reached over a configured headcount is not 0% —
        it is the absence of a measurement, and a red 0% would be a lie."""
        value = workforce_coverage(formats, FilterState(), settings)
        assert value.value is None
        assert value.status is SupportStatus.NOT_MEASURED
        assert any("roster" in c for c in value.caveats)
