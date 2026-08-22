"""Feed parsing: the three formats in the wild, plus the ways they misbehave."""

from __future__ import annotations

import pytest
import requests

from bot.config import FetchSettings, Source
from bot.feeds import fetch_source, parse_feed
from tests.conftest import fixture_bytes


def test_parses_rss2_items():
    entries = parse_feed(fixture_bytes("rss2.xml"))
    assert len(entries) == 3
    first = entries[0]
    assert first.title.startswith("IRDAI eases surrender value")
    assert first.link.startswith("https://bfsi.example.com/insurance/irdai-surrender-value")
    assert "surrender value floor" in first.summary
    assert first.published_raw == "Wed, 19 Aug 2026 06:30:00 +0530"


def test_parses_atom_and_prefers_the_alternate_link():
    entries = parse_feed(fixture_bytes("atom.xml"))
    assert len(entries) == 2
    # The <link rel="edit"> comes first in the document and must not win.
    assert entries[0].link == "https://www.bs.example.com/finance/bank-credit-growth/"
    assert entries[0].published_raw == "2026-08-19T02:45:00Z"


def test_parses_rss1_rdf():
    entries = parse_feed(fixture_bytes("rdf.xml"))
    assert len(entries) == 1
    assert entries[0].title.startswith("RBI issues master direction")
    assert entries[0].published_raw == "2026-08-19T03:00:00+05:30"


def test_repairs_undeclared_entities_and_bare_ampersands():
    """A stray &nbsp; or a raw & in a URL is not a reason to lose a source."""
    entries = parse_feed(fixture_bytes("messy.xml"))
    assert len(entries) == 2
    assert "Life insurance premiums grow 9%" in entries[0].title
    assert "cat=2" in entries[0].link


def test_reads_the_publisher_from_a_search_feed():
    entries = parse_feed(fixture_bytes("gnews.xml"))
    assert entries[0].publisher == "Mint"


def test_a_byline_is_not_mistaken_for_the_publication():
    """The rss2 fixture carries <dc:creator>Staff Reporter</dc:creator>, which
    must not end up as the attribution under a headline."""
    entries = parse_feed(fixture_bytes("rss2.xml"))
    assert entries[0].publisher == ""


def test_unsalvageable_xml_raises():
    from xml.etree.ElementTree import ParseError

    with pytest.raises(ParseError):
        parse_feed(b"<rss><channel><item><title>unclosed")


class _Response:
    def __init__(self, status_code=200, content=b"", ok=None):
        self.status_code = status_code
        self.content = content
        self.ok = (status_code < 400) if ok is None else ok


class _Session:
    """A stand-in for requests.Session that replays a scripted set of outcomes."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else self.outcomes
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


SOURCE = Source(id="s", name="S", url="https://feed.test/rss")
FAST = FetchSettings(retries=2, backoff_seconds=0.0, timeout_seconds=1.0)


def test_fetch_returns_entries_on_success():
    session = _Session(_Response(200, fixture_bytes("rss2.xml")))
    result = fetch_source(SOURCE, FAST, session=session)
    assert result.ok and len(result.entries) == 3


def test_fetch_retries_a_transient_error_then_succeeds():
    session = _Session(
        requests.ConnectionError("reset"),
        _Response(200, fixture_bytes("rss2.xml")),
    )
    result = fetch_source(SOURCE, FAST, session=session)
    assert result.ok
    assert session.calls == 2


def test_fetch_gives_up_on_a_client_error_without_retrying():
    """A 404 will not fix itself; retrying only delays the digest."""
    session = _Session(_Response(404, b""))
    result = fetch_source(SOURCE, FAST, session=session)
    assert not result.ok
    assert "404" in result.error
    assert session.calls == 1


def test_fetch_retries_a_server_error_and_reports_failure():
    session = _Session(_Response(503, b""), _Response(503, b""), _Response(503, b""))
    result = fetch_source(SOURCE, FAST, session=session)
    assert not result.ok and session.calls == 3


def test_a_failed_source_is_a_value_not_an_exception():
    session = _Session(requests.Timeout("slow"), requests.Timeout("slow"), requests.Timeout("slow"))
    result = fetch_source(SOURCE, FAST, session=session)
    assert result.ok is False
    assert "Timeout" in result.error
