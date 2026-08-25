"""Collapsing one job listed in several places — without merging two jobs."""

from __future__ import annotations

from radar.dedupe import dedupe, normalise_company, normalise_title, same_opening
from tests.conftest import make_opening


def test_grade_synonyms_flatten():
    assert normalise_title("AVP - Key Accounts") == normalise_title(
        "Assistant Vice President - Key Account"
    )


def test_company_suffixes_are_ignored():
    assert normalise_company("HDFC Life Insurance Company Ltd") == normalise_company("HDFC Life")


def test_the_same_job_from_two_boards_collapses():
    first = make_opening(source_id="careerjet-india", url="https://a.test/1", fit=70)
    second = make_opening(
        source_id="jooble-india",
        url="https://b.test/2",
        title="VP - Key Account Management",
        fit=64,
    )
    kept = dedupe([first, second])
    assert len(kept) == 1
    assert kept[0].duplicate_count == 1
    # The better-scoring copy survives.
    assert kept[0].fit == 70


def test_regional_variants_of_one_title_stay_separate():
    """'VP - Key Accounts, West' and '… South' are two jobs, not one listing."""
    west = make_opening(title="VP - Key Accounts, West", url="https://a.test/w")
    south = make_opening(title="VP - Key Accounts, South", url="https://a.test/s")
    assert not same_opening(west, south)
    assert len(dedupe([west, south])) == 2


def test_two_employers_with_one_title_stay_separate():
    hdfc = make_opening(company="HDFC Life", url="https://a.test/1")
    sbi = make_opening(company="SBI Life", url="https://b.test/1")
    assert len(dedupe([hdfc, sbi])) == 2


def test_the_same_url_twice_collapses():
    assert len(dedupe([make_opening(), make_opening()])) == 1


def test_www_and_bare_hosts_are_one_listing():
    from radar.dedupe import url_key

    assert url_key(make_opening(url="https://www.iimjobs.com/j/1")) == url_key(
        make_opening(url="https://iimjobs.com/j/1")
    )
