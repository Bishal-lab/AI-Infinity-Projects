"""Value coercion.

The most important assertions here are the ones about blanks. If a blank cell
ever coerces to ``False`` instead of ``None``, the funnel starts reporting
fabricated negatives and nothing downstream can tell.
"""

from __future__ import annotations

import pandas as pd
import pytest

from comms_dashboard.ingest import normalize as nz


@pytest.mark.parametrize(
    "raw, expected",
    [("Unique Opens", "uniqueopens"), ("unique_opens", "uniqueopens"),
     ("UNIQUE-OPENS ", "uniqueopens"), ("Progress %", "progress"),
     ("User Principal Name", "userprincipalname")],
)
def test_header_folding_ignores_case_space_and_punctuation(raw, expected):
    assert nz.normalise_header(raw) == expected


class TestBooleans:
    def test_blank_is_unknown_not_false(self):
        result = nz.to_bool(pd.Series(["Yes", "", None, float("nan")]))
        assert list(result.series) == [True, None, None, None]
        assert result.blanks == 3

    def test_mapping_values_override_the_defaults(self):
        # "registered" is a default TRUE token, but this mapping calls it False.
        result = nz.to_bool(
            pd.Series(["Registered", "Not registered"]),
            true_values=("not registered",),
            false_values=("registered",),
        )
        assert list(result.series) == [False, True]

    def test_tri_state_status_column(self):
        result = nz.to_bool(
            pd.Series(["Completed", "In Progress", "Not Started", ""]),
            true_values=("completed",),
            false_values=("in progress", "not started"),
        )
        assert list(result.series) == [True, False, False, None]

    def test_unreadable_values_are_reported_not_guessed(self):
        result = nz.to_bool(pd.Series(["Yes", "perhaps", "maybe"]))
        assert list(result.series) == [True, None, None]
        assert result.failures == 2
        assert set(result.examples) == {"perhaps", "maybe"}

    def test_counts_become_flags_with_zero_a_real_negative(self):
        result = nz.to_bool_from_count(pd.Series([0, 1, 7, None, ""]))
        assert list(result.series) == [False, True, True, None, None]


class TestDurations:
    @pytest.mark.parametrize(
        "raw, minutes",
        [("01:05:00", 65), ("45 min", 45), ("1h 05m", 65), ("1.5 hours", 90),
         ("PT1H5M", 65), ("65", 65), ("00:00:00", 0), (90, 90)],
    )
    def test_every_spelling_reaches_the_same_number(self, raw, minutes):
        result = nz.to_duration_minutes(pd.Series([raw]))
        assert result.series.iloc[0] == pytest.approx(minutes)

    def test_mixed_spellings_in_one_column(self):
        # Teams attendance reports really do mix these two in one column.
        result = nz.to_duration_minutes(pd.Series(["01:05:00", "45 min", ""]))
        assert list(result.series[:2]) == [65.0, 45.0]
        assert pd.isna(result.series.iloc[2])

    def test_unparseable_duration_is_blank_and_reported(self):
        result = nz.to_duration_minutes(pd.Series(["ages"]))
        assert pd.isna(result.series.iloc[0])
        assert result.failures == 1


class TestPercent:
    @pytest.mark.parametrize(
        "raw, expected",
        [("85%", 0.85), (85, 0.85), (0.85, 0.85), ("100%", 1.0), (0, 0.0)],
    )
    def test_percentages_land_as_fractions(self, raw, expected):
        result = nz.to_percent(pd.Series([raw]))
        assert result.series.iloc[0] == pytest.approx(expected)


class TestDates:
    def test_day_order_is_taken_from_config_not_guessed(self):
        ambiguous = pd.Series(["03/04/2026"])
        dmy = nz.to_datetime(ambiguous, dayfirst=True).series.iloc[0]
        mdy = nz.to_datetime(ambiguous, dayfirst=False).series.iloc[0]
        assert (dmy.day, dmy.month) == (3, 4)
        assert (mdy.day, mdy.month) == (4, 3)

    def test_iso_dates_parse_the_same_under_either_setting(self):
        for dayfirst in (True, False):
            value = nz.to_datetime(pd.Series(["2026-01-14"]), dayfirst).series.iloc[0]
            assert (value.year, value.month, value.day) == (2026, 1, 14)

    def test_excel_serial_dates_are_recognised(self):
        value = nz.to_datetime(pd.Series([45000]), dayfirst=True).series.iloc[0]
        assert value.year == 2023

    def test_blank_dates_stay_blank(self):
        result = nz.to_datetime(pd.Series(["", None]), dayfirst=True)
        assert result.series.isna().all()
        assert result.failures == 0


def test_taxonomy_aliases_fold_spellings_together():
    aliases = {"Fin": "Finance", "IT": "Technology"}
    assert nz.apply_alias_map("Fin", aliases) == "Finance"
    assert nz.apply_alias_map("it", aliases) == "Technology"
    assert nz.apply_alias_map("Legal", aliases) == "Legal"
    assert nz.apply_alias_map("", aliases) is None


def test_unknown_field_type_fails_loudly():
    with pytest.raises(ValueError, match="unknown field type"):
        nz.coerce(pd.Series([1]), "not_a_type")
