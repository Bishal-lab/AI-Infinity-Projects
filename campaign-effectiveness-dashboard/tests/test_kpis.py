"""KPI arithmetic, and the rules about when there is no number to give.

The n/a tests here are the ones that matter most. A funnel that silently
zero-fills an unmeasurable stage reports that nobody completed a campaign which
had no completion step — a confident, plausible, wrong answer.
"""

from __future__ import annotations

import pandas as pd
import pytest

from comms_dashboard.metrics import FilterState
from comms_dashboard.metrics.kpis import (
    KPI_BY_KEY,
    channel_effectiveness_index,
    channel_kpis,
    combined_funnel,
    cross_channel_rate,
    dedupe_availability,
    funnel_by_channel,
    headline_kpis,
    rate,
)
from comms_dashboard.models import MeasureUnit, SupportStatus


class TestRatePrimitive:
    def test_a_plain_rate(self):
        value = rate("k", "K", 50, 200)
        assert value.value == pytest.approx(0.25)
        assert value.display() == "25.0%"

    def test_not_applicable_beats_everything(self):
        value = rate("k", "K", 0, 100, numerator_status=SupportStatus.NOT_APPLICABLE)
        assert value.value is None
        assert value.display() == "n/a"

    def test_not_measured_is_distinct_from_not_applicable(self):
        value = rate("k", "K", 0, 100, numerator_status=SupportStatus.NOT_MEASURED)
        assert value.display() == "not measured"

    def test_events_over_people_is_refused(self):
        """One person can react and comment on the same post."""
        value = rate("k", "K", 500, 1200, numerator_unit=MeasureUnit.EVENTS)
        assert value.value is None
        assert value.status is SupportStatus.NOT_APPLICABLE
        assert any("different things" in c for c in value.caveats)

    def test_zero_denominator_yields_no_number_not_an_error(self):
        assert rate("k", "K", 5, 0).value is None

    def test_small_groups_are_suppressed_not_shown(self):
        value = rate("k", "K", 1, 3, min_group_size=5)
        assert value.suppressed
        assert value.display() == "suppressed"

    def test_zero_numerator_is_a_real_result(self):
        """Nobody engaging is a finding; it must not be hidden as n/a."""
        value = rate("k", "K", 0, 200)
        assert value.value == 0.0
        assert value.display() == "0.0%"


class TestNaSemantics:
    def test_completion_is_na_for_channels_without_a_completion_step(self, loaded):
        per_channel = channel_kpis(loaded, FilterState())
        for channel in ("email", "whatsapp"):
            kpi = per_channel[channel].get("completion_rate")
            # These channels are excluded from the definition entirely...
            if kpi is not None:
                assert kpi.display() == "n/a"

    def test_delivery_is_na_for_lms_and_teams(self, loaded):
        per_channel = channel_kpis(loaded, FilterState())
        for channel in ("lms", "teams_webinar"):
            assert per_channel[channel]["delivery_rate"].display() == "n/a"

    def test_an_unmeasured_stage_is_null_not_zero_in_the_warehouse(self, loaded):
        rows = loaded.execute(
            "SELECT metric_value FROM fact_stage_metric "
            "WHERE channel = 'email' AND stage = 'COMPLETED'"
        ).fetchall()
        assert rows
        assert all(value is None for (value,) in rows)

    def test_viva_engagement_never_becomes_a_percentage(self, loaded):
        per_channel = channel_kpis(loaded, FilterState())
        for kpi in per_channel["viva_engage"].values():
            assert not kpi.is_renderable, kpi.key

    def test_the_funnel_reports_which_channels_it_could_not_include(self, loaded):
        funnel = combined_funnel(loaded, FilterState())
        completed = funnel[funnel["stage"] == "COMPLETED"].iloc[0]
        assert "Email" in completed["excluded_channels"]
        assert "Learning Management" in completed["contributing_channels"]


class TestCrossChannelBasis:
    def test_both_sides_of_a_rate_use_the_same_channels(self, loaded):
        """Mixing populations is how you get an open rate above 100%."""
        per_channel = funnel_by_channel(loaded, FilterState())
        for key in ("delivery_rate", "open_rate", "engagement_rate", "completion_rate"):
            value = cross_channel_rate(KPI_BY_KEY[key], per_channel, _settings())
            if value.is_renderable:
                assert 0.0 <= value.value <= 1.0, key

    def test_the_basis_is_stated_on_every_headline_number(self, loaded):
        for kpi in headline_kpis(loaded, FilterState()).values():
            assert kpi.caveats, kpi.key

    def test_completion_is_based_only_on_channels_that_measure_it(self, loaded):
        per_channel = funnel_by_channel(loaded, FilterState())
        value = cross_channel_rate(KPI_BY_KEY["completion_rate"], per_channel, _settings())
        basis = " ".join(value.caveats)
        assert "Learning Management" in basis and "MS Teams webinar" in basis
        assert "Email" in basis.split("Excluded")[-1]


class TestEffectivenessIndex:
    def test_a_channel_is_not_punished_for_a_stage_it_cannot_measure(self, loaded):
        """Renormalised across supported rates, so the weights always sum to 1."""
        index = channel_effectiveness_index(loaded, FilterState())
        scored = index.dropna(subset=["index"])
        assert not scored.empty
        assert (scored["index"] >= 0).all() and (scored["index"] <= 100).all()

    def test_a_channel_with_no_comparable_rate_scores_nothing_not_zero(self, loaded):
        index = channel_effectiveness_index(loaded, FilterState())
        viva = index[index["channel"] == "viva_engage"].iloc[0]
        assert pd.isna(viva["index"])
        assert viva["components"] == 0


class TestDedupe:
    def test_available_when_the_roster_matches_well(self, loaded):
        ok, reason = dedupe_availability(loaded, FilterState())
        assert ok
        assert "roster" in reason

    def test_unavailable_without_a_roster_and_says_why(self, con, staged, settings, ladder):
        from comms_dashboard.ingest.loader import ingest_file

        ingest_file(con, staged("email_campaigns_2026-01.csv"),
                    settings=settings, ladder=ladder, archive=False)
        ok, reason = dedupe_availability(con, FilterState())
        assert not ok
        assert "roster" in reason

    def test_the_deduplicated_funnel_never_exceeds_the_summed_one(self, loaded):
        summed = combined_funnel(loaded, FilterState(funnel_basis="sum"))
        deduped = combined_funnel(loaded, FilterState(funnel_basis="dedup"))
        merged = summed.merge(deduped, on="stage", suffixes=("_sum", "_dedup"))
        both = merged.dropna(subset=["metric_value_sum", "metric_value_dedup"])
        assert not both.empty
        assert (both["metric_value_dedup"] <= both["metric_value_sum"]).all()


class TestFilters:
    def test_filtering_to_one_channel_narrows_the_funnel(self, loaded):
        everything = funnel_by_channel(loaded, FilterState())
        just_email = funnel_by_channel(loaded, FilterState(channels=("email",)))
        assert set(just_email["channel"]) == {"email"}
        assert len(just_email) < len(everything)

    def test_an_empty_filter_matches_everything(self, loaded):
        assert FilterState().where("f")[0] == "1=1"


def _settings():
    from comms_dashboard.config import get_settings

    return get_settings()
