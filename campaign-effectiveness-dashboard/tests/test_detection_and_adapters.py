"""Source detection and per-channel parsing."""

from __future__ import annotations

import pytest

from comms_dashboard.ingest.base import LoadContext
from comms_dashboard.ingest.reader import read_file
from comms_dashboard.ingest.registry import build_adapters, detect_source
from comms_dashboard.models import STAGE_COLUMNS, LoadReport, SupportStatus

from .conftest import EDGE_CASES, SAMPLES

EXPECTED_SOURCE = {
    "employee_roster.csv": "roster",
    "campaign_registry.csv": "campaign_registry",
    "email_campaigns_2026-01.csv": "email",
    "whatsapp_broadcast_2026-03.xlsx": "whatsapp",
    "lms_enrolments_2026-02.csv": "lms",
    "teams_webinar_attendance_2026-04.xlsx": "teams_webinar",
    "viva_engage_posts_2026-05.csv": "viva_engage",
}


@pytest.mark.parametrize("filename, source", sorted(EXPECTED_SOURCE.items()))
def test_every_sample_is_detected_as_its_own_source(filename, source):
    assert detect_source(SAMPLES / filename).source == source


def test_ambiguous_file_is_refused_rather_than_guessed():
    """Guessing wrong here files one channel's numbers under another, silently."""
    result = detect_source(EDGE_CASES / "ambiguous_source.csv")
    assert result.source is None
    assert "nothing matched confidently" in result.reason
    # The admin needs the scoring to decide, so it must be reportable.
    assert len(result.as_table()) > 1


def test_every_configured_mapping_gets_an_adapter(mappings):
    adapters = build_adapters(mappings)
    assert set(adapters) == set(mappings)


def _parse(filename, source, settings, ladder, mappings):
    adapter = build_adapters(mappings)[source]
    path = SAMPLES / filename
    df = read_file(path, adapter.mapping.read)
    report = LoadReport(file_name=filename)
    ctx = LoadContext(
        settings=settings, ladder=ladder, mapping=adapter.mapping,
        path=path, report=report,
    )
    return adapter, adapter.parse(df, ctx), report


class TestEmail:
    def test_stage_columns_are_populated(self, settings, ladder, mappings):
        _, parsed, _ = _parse(
            "email_campaigns_2026-01.csv", "email", settings, ladder, mappings
        )
        frame = parsed.canonical
        for column in ("targeted", "delivered", "opened", "engaged"):
            assert column in frame.columns
        assert frame["delivered"].notna().all()

    def test_an_open_implies_delivery_but_not_the_reverse(
        self, settings, ladder, mappings
    ):
        _, parsed, _ = _parse(
            "email_campaigns_2026-01.csv", "email", settings, ladder, mappings
        )
        opened = parsed.canonical[parsed.canonical["opened"] == True]  # noqa: E712
        assert (opened["delivered"] == True).all()  # noqa: E712


class TestWhatsApp:
    def test_read_receipt_timestamp_becomes_the_opened_flag(
        self, settings, ladder, mappings
    ):
        """Most providers report reads only as a timestamp, never as a flag."""
        _, parsed, report = _parse(
            "whatsapp_broadcast_2026-03.xlsx", "whatsapp", settings, ladder, mappings
        )
        frame = parsed.canonical
        assert frame["opened"].notna().any()
        assert (frame["opened"] == frame["opened_at"].notna()).all()
        # And the stage must not then be reported as unmeasured.
        assert "OPENED" not in parsed.stages_not_measured

    def test_engagement_combines_replies_and_button_taps(
        self, settings, ladder, mappings
    ):
        _, parsed, _ = _parse(
            "whatsapp_broadcast_2026-03.xlsx", "whatsapp", settings, ladder, mappings
        )
        frame = parsed.canonical
        expected = (frame["replied"] == True) | (frame["button_clicked"] == True)  # noqa: E712
        assert ((frame["engaged"] == True) == expected).all()  # noqa: E712


class TestLms:
    def test_completion_requires_passing_the_assessment(
        self, settings, ladder, mappings
    ):
        _, parsed, _ = _parse(
            "lms_enrolments_2026-02.csv", "lms", settings, ladder, mappings
        )
        frame = parsed.canonical
        failed = frame[frame["assessment_passed"] == False]  # noqa: E712
        assert (failed["completed"] != True).all()  # noqa: E712

    def test_delivered_is_absent_because_the_channel_has_no_such_stage(
        self, settings, ladder, mappings
    ):
        adapter, parsed, _ = _parse(
            "lms_enrolments_2026-02.csv", "lms", settings, ladder, mappings
        )
        ctx = LoadContext(
            settings=settings, ladder=ladder, mapping=adapter.mapping,
            path=SAMPLES / "lms_enrolments_2026-02.csv", report=LoadReport(file_name="x"),
        )
        statuses = adapter.stage_availability(parsed.canonical, ctx)
        assert statuses["DELIVERED"] is SupportStatus.NOT_APPLICABLE
        assert statuses["COMPLETED"] is SupportStatus.MEASURED


class TestTeamsWebinar:
    def test_completion_is_derived_from_dwell_time(self, settings, ladder, mappings):
        """Two minutes of a sixty-minute town hall is not attendance."""
        _, parsed, _ = _parse(
            "teams_webinar_attendance_2026-04.xlsx", "teams_webinar",
            settings, ladder, mappings,
        )
        frame = parsed.canonical.dropna(subset=["attendance_minutes", "session_minutes"])
        fraction = settings.webinar_completion_fraction
        share = frame["attendance_minutes"] / frame["session_minutes"]
        expected = share >= fraction
        assert ((frame["completed"] == True) == expected).all()  # noqa: E712

    def test_a_brief_attendee_engages_without_completing(
        self, settings, ladder, mappings
    ):
        _, parsed, _ = _parse(
            "teams_webinar_attendance_2026-04.xlsx", "teams_webinar",
            settings, ladder, mappings,
        )
        frame = parsed.canonical
        brief = frame[
            (frame["attendance_minutes"] > 0)
            & (frame["attendance_minutes"] / frame["session_minutes"] < 0.5)
        ]
        assert not brief.empty
        assert (brief["engaged"] == True).all()  # noqa: E712
        assert (brief["completed"] == False).all()  # noqa: E712

    def test_mixed_duration_spellings_all_parsed(self, settings, ladder, mappings):
        _, parsed, report = _parse(
            "teams_webinar_attendance_2026-04.xlsx", "teams_webinar",
            settings, ladder, mappings,
        )
        assert parsed.canonical["attendance_minutes"].notna().all()
        assert not [i for i in report.issues if i.code == "COERCION_FAILED"]


class TestVivaEngage:
    def test_engagement_is_counted_in_events_not_people(
        self, settings, ladder, mappings
    ):
        adapter, parsed, report = _parse(
            "viva_engage_posts_2026-05.csv", "viva_engage", settings, ladder, mappings
        )
        ctx = LoadContext(
            settings=settings, ladder=ladder, mapping=adapter.mapping,
            path=SAMPLES / "viva_engage_posts_2026-05.csv", report=report,
        )
        rows = adapter.to_stage_rows(parsed, ctx)
        engaged = rows[rows["stage"] == "ENGAGED"]
        assert (engaged["measure_unit"] == "events").all()
        assert (engaged["aggregation"] == "sum").all()

    def test_audience_and_reach_are_maxed_not_summed(self, settings, ladder, mappings):
        """The same community repeated on every post must not be added up."""
        adapter, parsed, report = _parse(
            "viva_engage_posts_2026-05.csv", "viva_engage", settings, ladder, mappings
        )
        ctx = LoadContext(
            settings=settings, ladder=ladder, mapping=adapter.mapping,
            path=SAMPLES / "viva_engage_posts_2026-05.csv", report=report,
        )
        rows = adapter.to_stage_rows(parsed, ctx)
        for stage in ("TARGETED", "OPENED"):
            block = rows[rows["stage"] == stage]
            assert (block["aggregation"] == "max").all()
            assert (block["measure_unit"] == "persons").all()


class TestValidation:
    def test_missing_required_column_is_an_error_naming_the_field(
        self, settings, ladder, mappings
    ):
        adapter = build_adapters(mappings)["email"]
        path = EDGE_CASES / "email_missing_required_column.csv"
        df = read_file(path, adapter.mapping.read)
        report = LoadReport(file_name=path.name)
        ctx = LoadContext(
            settings=settings, ladder=ladder, mapping=adapter.mapping,
            path=path, report=report,
        )
        adapter.parse(df, ctx)
        errors = [i for i in report.issues if i.code == "MISSING_REQUIRED_COLUMN"]
        assert errors
        assert errors[0].column_name == "delivered"
        # The message must tell the admin exactly where to fix it.
        assert "config/mappings/email.yaml" in errors[0].message

    def test_unmapped_columns_are_reported_but_do_not_block(
        self, settings, ladder, mappings
    ):
        _, _, report = _parse(
            "email_campaigns_2026-01.csv", "email", settings, ladder, mappings
        )
        assert not report.has_errors
