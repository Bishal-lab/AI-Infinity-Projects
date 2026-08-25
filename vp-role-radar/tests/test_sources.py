"""Adapters, exercised against recorded payloads — never the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from radar.sources import ADAPTERS
from radar.sources.base import SourceError, SourceSkipped, fetch_source, queries_for
from radar.sources.greenhouse import board_token
from radar.sources.lever import company_slug
from radar.sources.rss import parse_feed
from radar.sources.workday import endpoint

FIXTURES = Path(__file__).parent / "fixtures"


def test_every_configured_kind_has_an_adapter(config):
    for source in config.sources:
        assert source.kind in ADAPTERS, f"{source.id} names an unregistered kind"


# ------------------------------------------------------------------ Workday

@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://aia.wd3.myworkdayjobs.com/en-US/AIACareers",
            "https://aia.wd3.myworkdayjobs.com/wday/cxs/aia/AIACareers/jobs",
        ),
        # No locale segment.
        (
            "https://metlife.wd5.myworkdayjobs.com/MetLife_Careers",
            "https://metlife.wd5.myworkdayjobs.com/wday/cxs/metlife/MetLife_Careers/jobs",
        ),
        # A trailing job path, as copied from an open posting.
        (
            "https://sunlife.wd3.myworkdayjobs.com/en-US/Experienced-Jobs/job/Gurgaon/VP_JR-1",
            "https://sunlife.wd3.myworkdayjobs.com/wday/cxs/sunlife/Experienced-Jobs/jobs",
        ),
    ],
)
def test_workday_endpoint_is_derived_from_a_browser_url(url, expected):
    assert endpoint(url)[0] == expected


def test_workday_rejects_a_url_it_cannot_read():
    with pytest.raises(SourceError):
        endpoint("https://aia.wd3.myworkdayjobs.com")


def test_workday_parses_a_recorded_response(source, profile, config, monkeypatch):
    from dataclasses import replace

    import radar.sources.workday as workday

    payload = json.loads((FIXTURES / "workday.json").read_text())
    monkeypatch.setattr(workday, "request_json", lambda *a, **k: payload)

    wd = replace(
        source,
        kind="workday",
        url="https://aia.wd3.myworkdayjobs.com/en-US/AIACareers",
        company="AIA",
        query="key account",
    )
    postings = workday.fetch(wd, profile, config.fetch, session=None)
    assert len(postings) == 2
    first = postings[0]
    assert first.title == "Vice President, Key Accounts (Bancassurance)"
    assert first.company == "AIA"
    assert first.location == "Singapore"
    assert first.url == (
        "https://aia.wd3.myworkdayjobs.com/en-US/AIACareers"
        "/job/Singapore/VP-Key-Accounts_JR-2201"
    )
    assert first.posted_raw == "Posted 3 Days Ago"


# --------------------------------------------------------- Greenhouse, Lever

@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://boards.greenhouse.io/acme", "acme"),
        ("https://boards-api.greenhouse.io/v1/boards/acme/jobs", "acme"),
        ("acme", "acme"),
    ],
)
def test_greenhouse_board_token(url, expected):
    assert board_token(url) == expected


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://jobs.lever.co/acme", "acme"),
        ("https://api.lever.co/v0/postings/acme", "acme"),
        ("acme", "acme"),
    ],
)
def test_lever_company_slug(url, expected):
    assert company_slug(url) == expected


# ----------------------------------------------------------------------- RSS

def test_rss_feed_becomes_postings():
    postings = parse_feed((FIXTURES / "jobs.xml").read_bytes(), company="Example Insurer")
    assert len(postings) == 2
    assert postings[0].title == "Vice President - Bancassurance Alliances"
    assert postings[0].url == "https://example.test/jobs/1"
    assert postings[0].company == "Example Insurer"


def test_rss_survives_undeclared_html_entities():
    feed = (
        '<?xml version="1.0"?><rss><channel><item>'
        "<title>VP &ndash; Key Accounts &amp; Alliances</title>"
        "<link>https://example.test/1</link></item></channel></rss>"
    )
    postings = parse_feed(feed)
    assert postings[0].title.startswith("VP")


# ----------------------------------------------------------- keys and skips

def test_a_keyed_source_reports_itself_off_rather_than_failing(
    source, profile, config, monkeypatch
):
    """No key is not a fault: check-sources must show it as off, so a missing
    key never looks like a broken board."""
    from dataclasses import replace

    monkeypatch.delenv("CAREERJET_AFFID", raising=False)
    careerjet = replace(
        source, kind="careerjet", url="https://public.api.careerjet.net/search"
    )
    result = fetch_source(careerjet, profile, config.fetch)
    assert result.skipped and "CAREERJET_AFFID" in result.skipped
    assert result.error is None
    assert not result.ok


def test_an_unknown_kind_is_reported_not_raised(source, profile, config):
    from dataclasses import replace

    result = fetch_source(replace(source, kind="nonesuch"), profile, config.fetch)
    assert result.error and "no adapter" in result.error


def test_queries_come_from_the_profile_unless_the_source_pins_one(source, profile):
    from dataclasses import replace

    assert queries_for(source, profile) == list(profile.queries)
    assert queries_for(replace(source, query="banca"), profile) == ["banca"]


def test_a_source_raising_mid_flight_becomes_a_failure(source, profile, config, monkeypatch):
    """One dead board must never take the morning brief down with it."""
    import radar.sources.base as base

    monkeypatch.setitem(
        base.ADAPTERS, "rss", lambda *a, **k: (_ for _ in ()).throw(SourceError("HTTP 404"))
    )
    result = fetch_source(source, profile, config.fetch)
    assert not result.ok and result.error == "HTTP 404"
