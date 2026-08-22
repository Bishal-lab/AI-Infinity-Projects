"""Collapsing the same story arriving from several sources."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bot.dedupe import article_keys, dedupe, period_markers, same_story, similarity
from bot.model import Article

NOW = datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc)


def article(title: str, url: str, score: float = 5.0, minutes_old: int = 0) -> Article:
    return Article(
        source_id="s", source_name="S", title=title, url=url, score=score,
        published=NOW - timedelta(minutes=minutes_old),
    )


def test_identical_urls_collapse():
    kept = dedupe([article("A story", "https://x.test/1"), article("A story", "https://x.test/1")])
    assert len(kept) == 1


def test_a_rewritten_headline_is_recognised_as_the_same_story():
    kept = dedupe([
        article("IRDAI eases surrender value norms for life insurers", "https://a.test/1"),
        article("Irdai relaxes surrender value norms for life insurers", "https://b.test/2"),
    ])
    assert len(kept) == 1


def test_two_desks_rewriting_one_story_still_merge():
    """Real wording drifts further than a synonym: "life insurers" in one desk's
    headline is "life insurance companies" in another's."""
    kept = dedupe([
        article("IRDAI eases surrender value norms for life insurers", "https://a.test/1"),
        article("IRDAI relaxes surrender value norms for life insurance companies",
                "https://b.test/2"),
    ])
    assert len(kept) == 1


def test_opposite_outcomes_are_not_merged():
    kept = dedupe([
        article("LIC first-year premium rises in July", "https://a.test/1"),
        article("SBI Life first-year premium falls in July", "https://b.test/2"),
    ])
    assert len(kept) == 2


def test_different_reporting_periods_stay_separate():
    """Q1 and Q2 results read almost identically; merging them loses a story."""
    kept = dedupe([
        article("HDFC Life Q1 profit rises 15%", "https://a.test/1"),
        article("HDFC Life Q2 profit rises 15%", "https://b.test/2"),
    ])
    assert len(kept) == 2


def test_unrelated_stories_are_not_merged():
    kept = dedupe([
        article("SBI Life expands bancassurance tie-up", "https://a.test/1"),
        article("RBI holds the repo rate steady", "https://b.test/2"),
    ])
    assert len(kept) == 2


def test_the_best_copy_survives_and_counts_the_rest():
    kept = dedupe([
        article("IRDAI eases surrender value norms", "https://a.test/1", score=4.0),
        article("IRDAI eases surrender value norms", "https://b.test/2", score=9.0),
        article("Irdai eases surrender value norms", "https://c.test/3", score=6.0),
    ])
    assert len(kept) == 1
    assert kept[0].url == "https://b.test/2"
    assert kept[0].duplicate_count == 2


def test_a_tie_on_score_goes_to_the_newer_copy():
    kept = dedupe([
        article("Bank credit growth slows", "https://a.test/1", score=5.0, minutes_old=120),
        article("Bank credit growth slows", "https://b.test/2", score=5.0, minutes_old=10),
    ])
    assert kept[0].url == "https://b.test/2"


def test_input_order_is_preserved():
    kept = dedupe([
        article("First story about annuity sales", "https://a.test/1"),
        article("Second story about repo rate", "https://b.test/2"),
        article("Third story about gross NPA", "https://c.test/3"),
    ])
    assert [a.url for a in kept] == ["https://a.test/1", "https://b.test/2", "https://c.test/3"]


def test_similarity_and_period_helpers():
    assert similarity("bank credit growth slows", "bank credit growth slows") == 1.0
    assert similarity("annuity sales rise", "repo rate held") == 0.0
    assert period_markers("HDFC Life Q1 FY26 results") == {"q1", "fy26"}
    assert not same_story("HDFC Life Q1 results", "HDFC Life Q2 results")


def test_keys_cover_both_the_url_and_the_headline():
    url_key, title_key = article_keys(article("A story", "https://x.test/1"))
    assert url_key.startswith("u:") and title_key.startswith("t:")
    # The same headline under a different link shares the title key, which is
    # what stops a story reappearing tomorrow through a different feed.
    other_url, other_title = article_keys(article("A story", "https://y.test/2"))
    assert url_key != other_url and title_key == other_title
