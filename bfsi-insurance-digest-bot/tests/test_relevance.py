"""Relevance: what gets in, what stays out, and under which heading.

These run against the shipped topics.yaml, so a careless edit to the taxonomy
shows up here rather than in the 08:00 digest.
"""

from __future__ import annotations

import pytest

from bot.model import Article
from bot.relevance import KeywordMatcher, Scorer


def article(title: str, summary: str = "", source: str = "bs-finance") -> Article:
    return Article(
        source_id=source, source_name=source, title=title,
        url="https://example.test/story", summary=summary,
    )


@pytest.fixture
def scorer(config):
    return Scorer(config)


# ------------------------------------------------------------------ matching

def test_keywords_match_on_word_boundaries():
    matcher = KeywordMatcher(["LIC", "AI"])
    assert matcher.find("LIC posts profit") == {"LIC"}
    assert matcher.find("policy details") == set()
    assert matcher.find("A trip to Mumbai") == set()


def test_longer_phrases_win_over_the_words_inside_them():
    matcher = KeywordMatcher(["SBI", "SBI Life"])
    assert matcher.find("SBI Life reports growth") == {"SBI Life"}


def test_matching_is_case_insensitive_and_tolerates_spacing():
    matcher = KeywordMatcher(["value of new business", "non-par"])
    assert matcher.find("Value  Of New Business rose") == {"value of new business"}
    assert matcher.find("non par savings mix") == {"non-par"}


# ------------------------------------------------------------------- verdicts

def test_a_life_insurance_story_is_kept_and_routed(scorer):
    verdict = scorer.score(
        article(
            "IRDAI eases surrender value norms for life insurers",
            "The regulator relaxed the floor on non-linked savings products.",
        )
    )
    assert verdict.accepted
    assert verdict.section_id == "life_insurance"


def test_excluded_topics_are_dropped_however_they_score(scorer):
    verdict = scorer.score(
        article("IPL final: bank sponsors cheer as insurance ads flood the break",
                "Premium, credit, deposit, IRDAI, RBI.")
    )
    assert not verdict.accepted
    assert "excluded" in verdict.reason


def test_a_story_matching_nothing_is_dropped(scorer):
    verdict = scorer.score(article("Local bakery opens a third outlet", "Bread and cake."))
    assert not verdict.accepted
    assert verdict.reason == "no topic keywords matched"


def test_supporting_words_alone_do_not_open_the_domain_gate(scorer):
    """"market" and "app" appear in the taxonomy, but a story that matches only
    words like those has not shown it is about financial services."""
    verdict = scorer.score(
        article("Village market reopens after the monsoon", "Traders return to the stalls.")
    )
    assert not verdict.accepted
    assert verdict.reason == "outside the BFSI domain"


def test_a_strong_keyword_is_its_own_proof_of_domain(scorer):
    """"HDFC Life" and "VNB margin" never say the word insurance, and a brief
    that dropped them for it would be missing its own headline stories."""
    verdict = scorer.score(article("HDFC Life Q1 profit rises", "VNB margin improved."))
    assert verdict.accepted
    assert verdict.section_id == "life_insurance"


def test_a_specialist_desk_bypasses_the_domain_gate(scorer):
    """Everything ET BFSI Insurance prints is BFSI, even when the headline is
    written in language the keyword lists do not cover."""
    verdict = scorer.score(
        article("Bima Sugam onboarding gathers pace", "Rollout continues.",
                source="et-bfsi-insurance")
    )
    assert verdict.accepted


def test_a_headline_hit_counts_for_more_than_a_summary_hit(scorer):
    in_title = scorer.score(article("Annuity sales climb", "Steady quarter for the sector."))
    in_summary = scorer.score(article("Steady quarter for the sector", "Annuity sales climb."))
    assert in_title.score > in_summary.score


def test_a_thin_mention_falls_below_the_threshold(scorer):
    verdict = scorer.score(article("City council approves budget", "A bank branch will move."))
    assert not verdict.accepted


def test_banking_and_macro_stories_route_to_their_own_sections(scorer):
    assert scorer.score(
        article("Bank credit growth slows to 11%", "Gross NPA steady, CASA ratio slips.")
    ).section_id == "banking_nbfc"
    assert scorer.score(
        article("RBI holds repo rate as MPC flags inflation", "Monetary policy unchanged.")
    ).section_id == "markets_macro"


def test_a_source_hint_breaks_a_tie_towards_its_own_section(scorer):
    plain = scorer.score(article("Insurance sector sees steady premium growth"))
    hinted = scorer.score(
        article("Insurance sector sees steady premium growth", source="et-bfsi-insurance")
    )
    assert hinted.score > plain.score


def test_keyword_stuffing_cannot_outrank_a_real_story(scorer):
    """A roundup that name-drops every insurer is capped, so a single genuine
    circular is not pushed out of the brief by a listicle."""
    stuffed = scorer.score(
        article(
            "Best term plans: LIC, HDFC Life, SBI Life, Max Life, Tata AIA, Kotak Life compared",
            "ULIP, annuity, endowment plan, pension plan, money-back policy, "
            "term insurance, sum assured, surrender value, persistency, bancassurance.",
        )
    )
    assert stuffed.score <= 8 * 3.0 * 1.5 * 1.2 + 0.1


def test_the_verdict_explains_itself(scorer):
    verdict = scorer.score(article("HDFC Life Q1 profit rises", "VNB margin improved."))
    assert verdict.matched
    assert str(verdict.score) in verdict.reason or f"{verdict.score:g}" in verdict.reason


def test_apply_splits_accepted_from_rejected_and_annotates(scorer):
    items = [
        article("IRDAI issues circular on surrender value", "Life insurers affected."),
        article("Cricket match report", "IPL."),
    ]
    accepted, rejected = scorer.apply(items)
    assert len(accepted) == 1 and len(rejected) == 1
    assert accepted[0].section_id and accepted[0].score > 0 and accepted[0].matched
