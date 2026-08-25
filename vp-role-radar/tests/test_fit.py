"""The scoring rules, checked against the shipped profile.

These are the tests that matter most: they are the difference between a brief
its reader trusts and one they start skimming past.
"""

from __future__ import annotations

import pytest

from radar.fit import KeywordMatcher, Scorer
from tests.conftest import make_opening


@pytest.fixture
def scorer(profile):
    return Scorer(profile)


# --------------------------------------------------------------- the matcher

def test_matcher_respects_word_boundaries():
    matcher = KeywordMatcher(["VP", "banca"])
    assert matcher.matches("VP - Key Accounts") == ["VP"]
    # "VPN" and "bancassurance" must not count as hits for these keywords.
    assert matcher.matches("VPN rollout for bancassurance") == []


def test_matcher_prefers_the_longest_keyword():
    matcher = KeywordMatcher(["vice president", "senior vice president"])
    assert matcher.matches("Senior Vice President, Alliances") == ["senior vice president"]


def test_matcher_handles_punctuated_keywords():
    assert KeywordMatcher(["P&L"]).matches("Owns the P&L for the region") == ["P&L"]


# ------------------------------------------------------------------ accepted

def test_vp_bancassurance_role_is_a_strong_fit(scorer):
    verdict = scorer.score(make_opening())
    assert verdict.accepted
    assert verdict.tier == "strong"
    assert verdict.fit >= 70
    assert verdict.region == "india"


def test_gurgaon_outranks_another_indian_city(scorer):
    mumbai = scorer.score(make_opening(location="Mumbai, India"))
    gurgaon = scorer.score(make_opening(location="Gurgaon, Haryana, India"))
    # Home city, so it scores the geography dimension in full.
    assert gurgaon.fit > mumbai.fit


def test_avp_grade_is_eligible_but_scores_below_a_vp(scorer):
    vp = scorer.score(make_opening(title="Vice President - Key Account Manager"))
    avp = scorer.score(make_opening(title="Assistant Vice President - Key Account Manager"))
    assert avp.accepted
    assert avp.fit < vp.fit


def test_gulf_and_asia_roles_are_in_scope(scorer):
    dubai = scorer.score(
        make_opening(title="VP - Bancassurance", location="Dubai, United Arab Emirates")
    )
    singapore = scorer.score(
        make_opening(title="VP - Partnership Distribution", location="Singapore")
    )
    assert dubai.accepted and dubai.region == "gcc"
    assert singapore.accepted and singapore.region == "asia"


def test_region_is_read_from_the_title_when_no_location_is_given(scorer):
    verdict = scorer.score(
        make_opening(title="Head of Key Accounts, Bancassurance - Dubai", location="")
    )
    assert verdict.accepted
    assert verdict.region == "gcc"


def test_reasons_explain_the_score(scorer):
    verdict = scorer.score(make_opening())
    joined = " | ".join(verdict.reasons)
    assert "VP & above" in joined
    assert "key account" in joined.lower()
    assert "India" in joined


def test_the_candidates_edge_lifts_an_otherwise_equal_role(scorer):
    plain = make_opening(summary="Manage key accounts for the bancassurance channel.")
    edged = make_opening(
        summary=(
            "Manage key accounts for the bancassurance channel. Owns the P&L "
            "across APAC with C-suite stakeholders, driving digital "
            "transformation and go-to-market."
        )
    )
    assert scorer.score(edged).fit > scorer.score(plain).fit


# ------------------------------------------------------------------ rejected

def test_sub_vp_grades_are_rejected(scorer):
    verdict = scorer.score(make_opening(title="Key Account Manager - Bancassurance & Alliances"))
    assert not verdict.accepted
    assert "grade" in verdict.reason


def test_senior_manager_is_still_below_the_bar(scorer):
    assert not scorer.score(make_opening(title="Senior Manager - Key Accounts")).accepted


def test_a_different_function_is_rejected_on_the_title(scorer):
    verdict = scorer.score(make_opening(title="Vice President - Actuarial"))
    assert not verdict.accepted
    assert "different function" in verdict.reason


def test_a_body_mentioning_underwriting_is_not_rejected(scorer):
    """The exclusion list is checked against the title, not the whole posting:
    a genuine key-accounts role routinely names underwriting in its duties."""
    verdict = scorer.score(
        make_opening(
            summary=(
                "Own bancassurance key accounts for the life insurance business; "
                "partner with underwriting and claims to improve turnaround."
            )
        )
    )
    assert verdict.accepted


def test_out_of_region_roles_are_rejected(scorer):
    verdict = scorer.score(make_opening(location="London, United Kingdom"))
    assert not verdict.accepted
    assert "outside" in verdict.reason


def test_an_unrelated_vp_function_is_rejected(scorer):
    verdict = scorer.score(
        make_opening(
            title="Vice President - Operations",
            summary="Lead policy servicing operations for the life insurance business.",
        )
    )
    assert not verdict.accepted
    assert "key accounts" in verdict.reason


def test_a_junior_experience_band_is_rejected(scorer):
    verdict = scorer.score(
        make_opening(
            title="Vice President - Key Accounts",
            summary="Bancassurance key accounts. 2-5 years of experience required.",
            experience_min=2,
            experience_max=5,
        )
    )
    assert not verdict.accepted
    assert "floor" in verdict.reason


def test_volume_hiring_language_is_rejected_anywhere(scorer):
    verdict = scorer.score(
        make_opening(summary="Walk-in drive for freshers. Key accounts, life insurance.")
    )
    assert not verdict.accepted
