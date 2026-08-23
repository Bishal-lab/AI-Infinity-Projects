"""Relevance: what gets in, what stays out, and under which heading.

These run against the shipped topics.yaml, so a careless edit to the taxonomy
shows up here rather than in the 08:00 digest.
"""

from __future__ import annotations

import pytest

from bot.model import Article
from bot.relevance import KeywordMatcher, Scorer


def article(title: str, summary: str = "", source: str = "mint-money") -> Article:
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


def test_quote_pages_are_not_journalism(scorer):
    """A search feed indexes a live ticker page as an article, and its headline
    is dense with exactly the company names the taxonomy hunts for — so nothing
    but an explicit exclusion keeps it out."""
    verdict = scorer.score(
        article("HDFC Life Insurance Share Price - Live NSE: HDFCLIFE Stock Price & Chart",
                source="gnews-life-insurers")
    )
    assert not verdict.accepted
    assert "excluded" in verdict.reason


def test_a_real_share_price_story_still_gets_through(scorer):
    """The quote-page rule has to be narrow enough to leave the news alone:
    a story about a share price moving is a story."""
    assert scorer.score(
        article("HDFC Life shares jump 5% after strong Q1 results",
                "Brokerages raised targets on the life insurer.", source="gnews-life-insurers")
    ).accepted


def test_an_ambiguous_keyword_alone_does_not_prove_the_domain(scorer):
    """Both of these reached a life-insurance brief on the letters LIC alone —
    one an Australian property developer's ASX ticker, one a road junction in
    Mysore. Neither is named anywhere in the config: `LIC` is marked ambiguous,
    so it needs corroboration before it opens the gate."""
    for headline in [
        "Lifestyle Communities (ASX:LIC) Outlook: Sales Recovery and Debt Reduction",
        "Traffic diverted at LIC Circle",
    ]:
        verdict = scorer.score(article(headline, source="gnews-life-insurers"))
        assert not verdict.accepted, headline
        # Structural, not a blocklist entry — that distinction is the point.
        assert verdict.reason == "outside the BFSI domain", headline


def test_one_corroborating_word_is_enough_to_admit_it(scorer):
    """Every real LIC story carries another life-insurance word; that is exactly
    what separates them from the collisions above."""
    for headline in [
        "LIC ordered to pay Rs 70 lakh to nominee over technical claim rejection",
        "LIC first-year premium rises 12% in July",
        "LIC settles death claim in record time",
    ]:
        assert scorer.score(article(headline, source="gnews-life-insurance")).accepted, headline


def test_search_feeds_must_not_bypass_the_domain_gate(config):
    """The bypass is for curated desks, where an editor already decided the
    story is BFSI. A Google News query is not that — a query for LIC returns
    road junctions — so every search source has to sit below the threshold.
    Raising one back to 2.0 reopens the hole this test exists to guard."""
    bypass = config.scoring.gate_bypass_weight
    for source in config.sources:
        if source.id.startswith("gnews-"):
            assert source.weight < bypass, f"{source.id} would skip the domain gate"


def test_a_phrase_matches_across_a_hyphen(scorer):
    """Feeds write "first-year premium" where the taxonomy says "first year
    premium"; before this the phrase silently failed to match."""
    from bot.relevance import KeywordMatcher

    matcher = KeywordMatcher(["first year premium"])
    assert matcher.find("first-year premium rises") == {"first year premium"}
    assert matcher.find("first year premium rises") == {"first year premium"}


def test_sports_sponsorship_is_not_insurance_news(scorer):
    """An insurer's name in a title-rights headline is enough to carry a cricket
    story into the brief; the board is named, the sport often is not."""
    verdict = scorer.score(
        article("Google Gemini, Spinny and SBI Life in race for BCCI home season title rights",
                source="gnews-life-insurers")
    )
    assert not verdict.accepted
    assert "excluded" in verdict.reason


def test_an_insurer_appointment_survives_the_sports_rule(scorer):
    """The People section is exactly where a senior insurer hire belongs, and
    the exclusions above must not reach it."""
    assert scorer.score(
        article("Aviva India appoints Harshit Agrawal as Head of Marketing",
                "The life insurer said the appointment strengthens brand and customer engagement.",
                source="gnews-life-insurers")
    ).accepted


def test_a_generic_corporate_event_does_not_prove_the_domain(scorer):
    """"IPO" and "appoints" happen in every industry. Before the corporate
    section stopped certifying the domain, this small-cap listing reached a
    BFSI brief on the word IPO alone."""
    verdict = scorer.score(
        article("Mopshop Distribution Limited IPO - Check IPO Date, Price & Allotment",
                source="gnews-bfsi")
    )
    assert not verdict.accepted
    assert verdict.reason == "outside the BFSI domain"


def test_the_same_corporate_event_passes_once_it_names_a_financial_firm(scorer):
    verdict = scorer.score(
        article("HDFC Life board approves dividend of Rs 2 per share", source="gnews-bfsi")
    )
    assert verdict.accepted
    assert verdict.section_id == "corporate"


def test_a_non_certifying_section_still_admits_a_real_bfsi_story(scorer):
    """Technology does not certify the domain, so its genuine terms — fintech,
    UPI, account aggregator — have to be in the gate list for stories like this
    to survive. This test is what catches their removal."""
    assert scorer.score(
        article("Payments and AI drive $361m in FinTech funding this week")
    ).accepted
    assert scorer.score(
        article("UPI transaction volumes cross a new monthly high")
    ).accepted
    assert scorer.score(
        article("RBI holds repo rate as the MPC flags inflation risks")
    ).accepted


def test_a_strong_keyword_is_its_own_proof_of_domain(scorer):
    """"HDFC Life" and "VNB margin" never say the word insurance, and a brief
    that dropped them for it would be missing its own headline stories."""
    verdict = scorer.score(article("HDFC Life Q1 profit rises", "VNB margin improved."))
    assert verdict.accepted
    assert verdict.section_id == "life_insurance"


def test_a_specialist_desk_bypasses_the_domain_gate(scorer):
    """Everything a BFSI desk prints is BFSI, even when the headline is written
    in language the keyword lists barely cover. The same words from a general
    desk have not earned their place — that contrast is the whole point of the
    bypass, so both halves are asserted here."""
    headline = "Mortality tables under review"
    assert scorer.score(article(headline, source="et-bfsi-top")).accepted
    passed_over = scorer.score(article(headline, source="mint-money"))
    assert not passed_over.accepted
    assert passed_over.reason == "outside the BFSI domain"


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
        article("Insurance sector sees steady premium growth", source="gnews-life-insurance")
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
