"""Cleaning what the boards hand over."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from radar.normalize import (
    canonical_url,
    clean_location,
    parse_date,
    parse_experience,
    strip_html,
    to_opening,
    truncate,
)
from tests.conftest import NOW, make_posting


def test_strip_html_flattens_a_description():
    text = strip_html("<p>Own <b>key accounts</b>.</p><ul><li>Banca</li></ul>")
    assert text == "Own key accounts. Banca"


def test_truncate_cuts_on_a_word_boundary():
    assert truncate("Vice President Key Account Management", 20).endswith("…")
    assert truncate("short", 20) == "short"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("8-12 years", (8, 12)),
        ("10 to 15 yrs", (10, 15)),
        ("12 – 18 years of experience", (12, 18)),
        ("15+ years", (15, None)),
        ("minimum 12 years", (12, None)),
        ("at least 10 years of experience", (10, None)),
        ("no band mentioned", (None, None)),
    ],
)
def test_parse_experience(text, expected):
    assert parse_experience(text) == expected


def test_parse_experience_orders_a_reversed_band():
    assert parse_experience("12-8 years") == (8, 12)


@pytest.mark.parametrize(
    "text",
    ["2026-08-24T09:00:00Z", "Mon, 24 Aug 2026 09:00:00 +0000", "2026-08-24", "24 Aug 2026"],
)
def test_parse_date_reads_the_usual_formats(text):
    parsed = parse_date(text)
    assert parsed is not None and parsed.year == 2026 and parsed.month == 8


def test_parse_date_reads_the_relative_phrasing_boards_use():
    """Several boards date a posting only as 'Posted 3 Days Ago'."""
    assert parse_date("Posted Today", now=NOW) == NOW
    assert parse_date("Posted Yesterday", now=NOW) == NOW - timedelta(days=1)
    assert parse_date("Posted 3 Days Ago", now=NOW) == NOW - timedelta(days=3)
    assert parse_date("Posted 30+ Days Ago", now=NOW) == NOW - timedelta(days=30)


def test_parse_date_gives_up_cleanly():
    assert parse_date("sometime soon") is None
    assert parse_date("") is None


def test_canonical_url_strips_tracking():
    assert canonical_url(
        "https://www.example.test/jobs/1?utm_source=x&trk=y&id=7"
    ) == "https://www.example.test/jobs/1?id=7"


def test_clean_location_drops_workdays_placeholder():
    assert clean_location("3 Locations") == ""
    assert clean_location("Gurugram, India") == "Gurugram, India"
    assert "+1 more" in clean_location("Mumbai, India; Pune, India")


def test_to_opening_reads_the_band_out_of_the_description(source):
    opening = to_opening(make_posting(), source, now=NOW)
    assert opening is not None
    assert (opening.experience_min, opening.experience_max) == (15, 20)
    assert opening.experience_band == "15-20 yrs"


def test_to_opening_needs_a_title_and_a_link(source):
    assert to_opening(make_posting(title=""), source) is None
    assert to_opening(make_posting(url=""), source) is None


def test_to_opening_falls_back_to_the_sources_company(source):
    from dataclasses import replace

    branded = replace(source, company="AIA")
    opening = to_opening(make_posting(company=""), branded, now=NOW)
    assert opening.company == "AIA"


def test_canonical_url_keeps_www_because_the_reader_clicks_it():
    """Some hosts answer only on the www name, and this URL is the apply link.
    Collapsing www is a dedupe concern, handled in dedupe.url_key."""
    assert canonical_url("https://www.iimjobs.com/j/vp-key-accounts-1712778") == (
        "https://www.iimjobs.com/j/vp-key-accounts-1712778"
    )
