"""Shared fixtures. Everything here runs offline: no test touches the network."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.config import Config, FetchSettings, Source, load_config  # noqa: E402
from bot.feeds import FetchResult, parse_feed  # noqa: E402
from bot.normalize import parse_date  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: The fixture feeds are all dated 19 August 2026; freeze "now" just after them
#: so window arithmetic in the tests is deterministic.
NOW = datetime(2026, 8, 19, 7, 0, tzinfo=timezone.utc)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def config() -> Config:
    """The real shipped configuration — the tests check what actually runs."""
    return load_config(ROOT / "config")


@pytest.fixture
def fixture_sources() -> tuple[Source, ...]:
    return (
        Source(id="et-bfsi-insurance", name="ET BFSI · Insurance", url="https://x/rss2",
               weight=3.0, section_hint="life_insurance"),
        Source(id="bs-finance", name="Business Standard · Finance", url="https://x/atom",
               weight=0.0),
        Source(id="rbi-press", name="RBI Press Releases", url="https://x/rdf",
               weight=3.0, section_hint="regulation"),
        Source(id="gnews-irdai", name="News · IRDAI", url="https://x/gnews",
               weight=2.0, section_hint="regulation"),
    )


@pytest.fixture
def fixture_fetch(fixture_sources):
    """A drop-in replacement for `fetch_all` that reads the fixture files."""
    files = {
        "et-bfsi-insurance": "rss2.xml",
        "bs-finance": "atom.xml",
        "rbi-press": "rdf.xml",
        "gnews-irdai": "gnews.xml",
    }

    def fetch(sources: Sequence[Source], settings: FetchSettings) -> list[FetchResult]:
        results = []
        for source in sources:
            name = files.get(source.id)
            if name is None:
                results.append(FetchResult(source=source, error="no fixture for this source"))
                continue
            results.append(
                FetchResult(
                    source=source,
                    entries=parse_feed(fixture_bytes(name)),
                    status_code=200,
                )
            )
        return results

    return fetch


@pytest.fixture
def recent_fetch(fixture_fetch):
    """The same fixture feeds, re-stamped as if published in the last few hours.

    Tests that exercise the real clock (the CLI ones) need the fixture stories
    to fall inside the lookback window whatever today's date is.
    """
    from email.utils import format_datetime
    from datetime import timedelta

    def fetch(sources, settings):
        results = fixture_fetch(sources, settings)
        dates = [
            parsed
            for result in results
            for entry in result.entries
            if (parsed := parse_date(entry.published_raw))
        ]
        if not dates:
            return results
        shift = datetime.now(timezone.utc) - max(dates) - timedelta(hours=1)
        for result in results:
            for entry in result.entries:
                parsed = parse_date(entry.published_raw)
                if parsed:
                    entry.published_raw = format_datetime(parsed + shift)
        return results

    return fetch


@pytest.fixture(autouse=True)
def no_real_quotes(monkeypatch):
    """No test may reach the quote service.

    Autouse rather than opt-in because the failure mode is silent and slow: the
    shipped settings.yaml carries a watchlist, so any test that builds a digest
    would otherwise make five real HTTP calls, each retried with a backoff. A
    test that hangs for a minute reads as a hang, not as a network call.

    Returning no quotes is not a fudge — it is exactly what an empty watchlist
    does, which is a supported configuration and the one every test here
    predates. Tests that want quotes supply them explicitly, via
    build_digest(quote_fn=…) or by constructing Quote objects directly, and a
    test that forgets still fails loudly on its own assertions.
    """
    monkeypatch.setattr("bot.quotes.fetch_all", lambda entries, settings: [])
