"""Shared fixtures.

Nothing in this suite touches the network. Sources are injected as fetch
results, which is the seam `build_digest(results=...)` exists for.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from radar.config import Source, load_config  # noqa: E402
from radar.model import Opening, RawPosting  # noqa: E402
from radar.sources.base import FetchResult  # noqa: E402

NOW = datetime(2026, 8, 25, 2, 40, tzinfo=timezone.utc)  # a Tuesday, 08:10 IST


@pytest.fixture(scope="session")
def config():
    """The real shipped configuration — the tests check what actually runs."""
    return load_config(PROJECT_ROOT / "config")


@pytest.fixture
def profile(config):
    return config.profile


@pytest.fixture
def source():
    return Source(
        id="test-board",
        name="Test Board",
        kind="rss",
        url="https://example.test/jobs.rss",
        company="",
    )


def make_posting(**kwargs) -> RawPosting:
    defaults = dict(
        title="Vice President - Key Account Management (Bancassurance)",
        company="HDFC Life Insurance",
        location="Mumbai, India",
        url="https://example.test/jobs/1",
        summary=(
            "Own and grow strategic bank partnerships for the life insurance "
            "business. Accountable for premium delivery, persistency and joint "
            "business plans with key accounts. 15-20 years of experience."
        ),
        posted_raw="2026-08-24T09:00:00Z",
        guid="1",
        experience_raw="",
    )
    defaults.update(kwargs)
    return RawPosting(**defaults)


def make_opening(**kwargs) -> Opening:
    defaults = dict(
        source_id="test-board",
        source_name="Test Board",
        title="Vice President - Key Account Management",
        url="https://example.test/jobs/1",
        company="HDFC Life Insurance",
        location="Mumbai, India",
        summary="Bancassurance key accounts for a life insurance business.",
        posted=NOW - timedelta(days=1),
    )
    defaults.update(kwargs)
    return Opening(**defaults)


def make_result(source: Source, *postings: RawPosting, **kwargs) -> FetchResult:
    return FetchResult(source=source, postings=list(postings), **kwargs)
