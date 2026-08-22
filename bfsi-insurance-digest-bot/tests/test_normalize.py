"""Cleaning: dates, URLs, markup, headlines."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.config import Source
from bot.model import RawEntry
from bot.normalize import (
    canonical_url,
    clean_title,
    parse_date,
    strip_html,
    to_article,
    truncate,
)

SOURCE = Source(id="s", name="Source", url="https://feed.test/rss")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Wed, 19 Aug 2026 06:30:00 +0530", datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)),
        ("2026-08-19T02:45:00Z", datetime(2026, 8, 19, 2, 45, tzinfo=timezone.utc)),
        ("2026-08-19T03:00:00+05:30", datetime(2026, 8, 18, 21, 30, tzinfo=timezone.utc)),
        ("2026-08-19 03:30:00", datetime(2026, 8, 19, 3, 30, tzinfo=timezone.utc)),
        ("2026-08-19", datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)),
    ],
)
def test_parse_date_handles_the_formats_feeds_actually_use(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "not a date", "Tomorrow"])
def test_parse_date_returns_none_rather_than_guessing(raw):
    assert parse_date(raw) is None


def test_naive_timestamps_are_read_as_utc():
    """Assuming IST would shift a late-evening story into the wrong day."""
    assert parse_date("2026-08-19 03:30:00").tzinfo == timezone.utc


def test_canonical_url_drops_tracking_and_normalises_host():
    assert canonical_url(
        "https://WWW.Example.com/a/b/?utm_source=rss&id=7&fbclid=zz#top"
    ) == "https://example.com/a/b?id=7"


def test_canonical_url_makes_the_same_story_compare_equal():
    a = canonical_url("https://site.test/news/x?utm_campaign=a")
    b = canonical_url("https://www.site.test/news/x/?utm_medium=b&ref=twitter")
    assert a == b


def test_canonical_url_leaves_odd_input_alone():
    assert canonical_url("") == ""
    assert canonical_url("not a url") == "not a url"


def test_strip_html_removes_markup_scripts_and_entities():
    assert strip_html("<p>Rates &amp; <b>norms</b></p><script>x()</script>") == "Rates & norms"
    assert strip_html("a&nbsp;b") == "a b"


def test_strip_html_handles_double_encoded_markup():
    assert strip_html("&lt;p&gt;Premium up&lt;/p&gt;") == "Premium up"


def test_truncate_cuts_on_a_word_boundary():
    assert truncate("the quick brown fox jumps", 12) == "the quick…"
    assert truncate("short", 40) == "short"


def test_clean_title_drops_the_search_feed_attribution():
    assert clean_title("IRDAI eases norms - Mint", "Mint") == "IRDAI eases norms"
    assert clean_title("IRDAI eases norms | Mint", "Mint") == "IRDAI eases norms"
    # An unrelated publisher name must not truncate the headline.
    assert clean_title("IRDAI eases norms - Part 2", "Mint") == "IRDAI eases norms - Part 2"


def test_to_article_builds_a_clean_article():
    entry = RawEntry(
        title="Life insurers report growth - Mint",
        link="https://site.test/x?utm_source=rss",
        summary="<p>New business premium rose 9%.</p>",
        published_raw="Wed, 19 Aug 2026 06:30:00 +0530",
        publisher="Mint",
    )
    article = to_article(entry, SOURCE)
    assert article.title == "Life insurers report growth"
    assert article.url == "https://site.test/x"
    assert article.summary == "New business premium rose 9%."
    assert article.attribution == "Mint"


def test_to_article_rejects_entries_with_nothing_to_link_to():
    assert to_article(RawEntry(title="No link"), SOURCE) is None
    assert to_article(RawEntry(link="https://x.test/1"), SOURCE) is None


def test_attribution_falls_back_to_the_source_name():
    article = to_article(RawEntry(title="T", link="https://x.test/1"), SOURCE)
    assert article.attribution == "Source"
