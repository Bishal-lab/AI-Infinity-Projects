"""The pipeline end to end, against fixture feeds — no network involved."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from bot.config import FetchSettings, Source
from bot.digest import (
    apply_caps,
    build_digest,
    group_sections,
    local_zone,
    rank,
    within_window,
)
from bot.feeds import FetchResult
from bot.model import Article
from bot.state import SeenStore
from tests.conftest import NOW


@pytest.fixture
def fixture_config(config, fixture_sources):
    """The shipped config, pointed at the fixture sources."""
    return replace(config, sources=fixture_sources)


# ------------------------------------------------------------------ end to end

def test_build_digest_produces_a_grouped_brief(fixture_config, fixture_fetch):
    digest = build_digest(fixture_config, now=NOW, store=None, fetch_fn=fixture_fetch)
    assert not digest.is_empty
    assert digest.sections
    ids = [section.section.id for section in digest.sections]
    assert "life_insurance" in ids
    # Sections keep the order they were declared in topics.yaml.
    assert ids == sorted(ids, key=lambda i: [s.id for s in fixture_config.sections].index(i))


def test_the_same_story_from_three_feeds_appears_once(fixture_config, fixture_fetch):
    """The surrender-value story is in the RSS, Atom and search fixtures."""
    digest = build_digest(fixture_config, now=NOW, store=None, fetch_fn=fixture_fetch)
    surrender = [a for a in digest.articles if "surrender value" in a.title.lower()]
    assert len(surrender) == 1
    assert surrender[0].duplicate_count >= 1


def test_off_topic_items_never_reach_the_brief(fixture_config, fixture_fetch):
    digest = build_digest(fixture_config, now=NOW, store=None, fetch_fn=fixture_fetch)
    assert not [a for a in digest.articles if "cricket" in a.title.lower()]


def test_a_dead_source_is_reported_but_does_not_stop_the_run(fixture_config, fixture_fetch):
    broken = Source(id="broken", name="Broken Feed", url="https://nope.test/rss")
    config = replace(fixture_config, sources=fixture_config.sources + (broken,))
    digest = build_digest(config, now=NOW, store=None, fetch_fn=fixture_fetch)
    assert not digest.is_empty
    assert ("Broken Feed", "no fixture for this source") in digest.failures
    assert digest.sources_ok == len(config.enabled_sources) - 1


def test_the_seen_store_suppresses_a_second_run(fixture_config, fixture_fetch, tmp_path):
    store = SeenStore(tmp_path / "seen.json").load()
    first = build_digest(fixture_config, now=NOW, store=store, fetch_fn=fixture_fetch)
    assert first.total_items > 0
    store.mark(first.articles)

    second = build_digest(fixture_config, now=NOW, store=store, fetch_fn=fixture_fetch)
    assert second.is_empty


def test_an_empty_brief_still_carries_its_window_and_source_count(fixture_config):
    def nothing(sources, settings):
        return [FetchResult(source=source, entries=[]) for source in sources]

    digest = build_digest(fixture_config, now=NOW, store=None, fetch_fn=nothing)
    assert digest.is_empty
    assert digest.sources_total == len(fixture_config.enabled_sources)
    assert digest.window_end > digest.window_start


def test_stats_trace_every_stage(fixture_config, fixture_fetch):
    digest = build_digest(fixture_config, now=NOW, store=None, fetch_fn=fixture_fetch)
    stats = digest.stats
    assert stats.entries_fetched >= stats.articles_parsed >= stats.in_window
    assert stats.in_window >= stats.accepted >= stats.after_dedupe >= stats.published


def test_rejected_items_are_kept_only_when_asked_for(fixture_config, fixture_fetch):
    quiet = build_digest(fixture_config, now=NOW, store=None, fetch_fn=fixture_fetch)
    loud = build_digest(
        fixture_config, now=NOW, store=None, fetch_fn=fixture_fetch, keep_rejected=True
    )
    assert quiet.rejected == []
    assert loud.rejected


# --------------------------------------------------------------------- stages

def _article(hours_old: float | None, score: float = 5.0, section: str = "life_insurance",
             source: str = "s") -> Article:
    published = None if hours_old is None else NOW - timedelta(hours=hours_old)
    return Article(
        source_id=source, source_name=source, title=f"story {hours_old} {score} {source}",
        url=f"https://x.test/{hours_old}-{score}-{source}", published=published,
        score=score, section_id=section,
    )


def test_within_window_keeps_only_recent_items():
    start = NOW - timedelta(hours=26)
    kept = within_window([_article(1), _article(25), _article(40)], start, NOW)
    assert len(kept) == 2


def test_undated_items_are_dated_as_of_the_run_by_default():
    kept = within_window([_article(None)], NOW - timedelta(hours=26), NOW)
    assert len(kept) == 1 and kept[0].published == NOW


def test_undated_items_can_be_dropped_instead():
    assert within_window([_article(None)], NOW - timedelta(hours=26), NOW, undated="drop") == []


def test_small_clock_skew_is_tolerated():
    """Feeds do publish a little ahead; dropping those loses the freshest news."""
    kept = within_window([_article(-1)], NOW - timedelta(hours=26), NOW)
    assert len(kept) == 1


def test_a_wildly_future_date_is_pulled_back_to_the_run_time():
    """Otherwise one mis-stamped item would sit at the top of every digest."""
    kept = within_window([_article(-72)], NOW - timedelta(hours=26), NOW)
    assert kept and kept[0].published == NOW


def test_rank_orders_by_score_then_recency():
    ordered = rank([_article(1, 4.0), _article(10, 9.0), _article(1, 9.0)])
    assert [a.score for a in ordered] == [9.0, 9.0, 4.0]
    assert ordered[0].published > ordered[1].published


def test_caps_limit_per_source_then_per_section_then_total(fixture_config):
    config = replace(
        fixture_config,
        digest=replace(
            fixture_config.digest,
            max_items_per_source=2, max_items_per_section=3, max_items_total=4,
        ),
    )
    articles = [
        _article(i, 9.0 - i * 0.1, section="life_insurance", source=f"src{i % 3}")
        for i in range(12)
    ]
    kept = apply_caps(articles, config)
    assert len(kept) <= 4
    for source_id in {a.source_id for a in kept}:
        assert len([a for a in kept if a.source_id == source_id]) <= 2


def test_sections_with_no_news_are_left_out(fixture_config):
    sections = group_sections([_article(1, section="life_insurance")], fixture_config)
    assert [s.section.id for s in sections] == ["life_insurance"]


def test_sections_can_be_ordered_by_volume(fixture_config):
    config = replace(
        fixture_config, digest=replace(fixture_config.digest, section_order="volume")
    )
    articles = [
        _article(1, section="banking_nbfc"),
        _article(2, section="banking_nbfc"),
        _article(3, section="life_insurance"),
    ]
    sections = group_sections(articles, config)
    assert sections[0].section.id == "banking_nbfc"


def test_local_zone_falls_back_rather_than_failing():
    assert local_zone("Asia/Kolkata") is not None
    fallback = local_zone("Not/AZone")
    assert fallback is not None
    assert datetime.now(fallback).utcoffset() is not None
