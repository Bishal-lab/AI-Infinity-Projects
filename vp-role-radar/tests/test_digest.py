"""The pipeline end to end, with sources injected rather than fetched."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from radar.cli import should_send
from radar.digest import build_digest, within_window
from radar.sources.base import FetchResult
from radar.state import SeenStore
from tests.conftest import NOW, make_posting, make_result, make_opening


def test_a_matching_posting_reaches_the_brief(config, source):
    digest = build_digest(config, now=NOW, results=[make_result(source, make_posting())])
    assert not digest.is_empty
    assert digest.tiers[0].id == "strong"
    assert digest.stats.published == 1


def test_openings_are_grouped_by_tier_strongest_first(config, source):
    strong = make_posting(url="https://example.test/jobs/1")
    stretch = make_posting(
        title="Assistant Vice President - Corporate Sales",
        company="Acme Broking",
        location="Colombo, Sri Lanka",
        summary="Corporate sales for an insurance broking business.",
        url="https://example.test/jobs/2",
    )
    digest = build_digest(config, now=NOW, results=[make_result(source, strong, stretch)])
    ids = [tier.id for tier in digest.tiers]
    assert ids == sorted(ids, key=["strong", "possible", "stretch"].index)
    assert digest.openings[0].fit >= digest.openings[-1].fit


def test_roles_that_do_not_match_are_left_out(config, source):
    junk = make_posting(
        title="Senior Manager - Underwriting",
        summary="Underwriting for the life insurance business.",
        url="https://example.test/jobs/9",
    )
    digest = build_digest(config, now=NOW, results=[make_result(source, junk)])
    assert digest.is_empty


def test_the_explain_path_records_why(config, source):
    junk = make_posting(title="Key Account Manager", url="https://example.test/jobs/9")
    digest = build_digest(
        config, now=NOW, keep_rejected=True, results=[make_result(source, junk)]
    )
    assert digest.rejected
    assert "grade" in digest.rejected[0][1].reason


def test_stale_postings_fall_outside_the_window(config, source):
    old = make_posting(posted_raw="2026-06-01T09:00:00Z")
    digest = build_digest(config, now=NOW, results=[make_result(source, old)])
    assert digest.is_empty
    assert digest.stats.in_window == 0


def test_undated_postings_are_trusted_by_default(config, source):
    digest = build_digest(config, now=NOW, results=[make_result(source, make_posting(posted_raw=""))])
    assert digest.stats.in_window == 1


def test_undated_postings_can_be_dropped(config, source):
    strict = replace(config, radar=replace(config.radar, undated_items="drop"))
    digest = build_digest(strict, now=NOW, results=[make_result(source, make_posting(posted_raw=""))])
    assert digest.stats.in_window == 0


def test_the_seen_store_suppresses_a_repeat(config, source, tmp_path):
    store = SeenStore(tmp_path / "seen.json").load()
    results = [make_result(source, make_posting())]

    first = build_digest(config, store=store, now=NOW, results=results)
    assert first.stats.published == 1
    store.mark(first.openings, when=NOW)

    second = build_digest(config, store=store, now=NOW, results=results)
    assert second.is_empty, "a posting still live tomorrow must not be mailed twice"


def test_a_failing_source_is_recorded_not_raised(config, source):
    digest = build_digest(
        config,
        now=NOW,
        results=[FetchResult(source=source, error="HTTP 503")],
    )
    assert digest.failures == [("Test Board", "HTTP 503")]
    assert digest.failure_ratio() == 1.0


def test_a_keyless_source_counts_as_off_not_failed(config, source):
    digest = build_digest(
        config,
        now=NOW,
        results=[FetchResult(source=source, skipped="no CAREERJET_AFFID set")],
    )
    assert digest.skipped and not digest.failures
    assert digest.failure_ratio() == 0.0


def test_caps_hold_the_brief_to_a_readable_length(config, source):
    tight = replace(
        config, radar=replace(config.radar, max_items_total=2, max_items_per_source=99)
    )
    # Distinct employers, so the cap is what trims this and not the deduper.
    postings = [
        make_posting(url=f"https://example.test/jobs/{n}", company=f"Insurer {n}")
        for n in range(6)
    ]
    digest = build_digest(tight, now=NOW, results=[make_result(source, *postings)])
    assert digest.stats.published == 2


# ------------------------------------------------------------ when to send

def test_an_empty_brief_is_not_mailed_on_an_ordinary_day(config, source):
    digest = build_digest(config, now=NOW, results=[])  # NOW is a Tuesday
    send, why = should_send(digest, config)
    assert not send and "proof-of-life" in why


def test_an_empty_brief_is_mailed_on_the_proof_of_life_day(config):
    monday = NOW - timedelta(days=1)
    digest = build_digest(config, now=monday, results=[])
    send, why = should_send(digest, config)
    assert send and "proof-of-life" in why


def test_a_brief_with_openings_always_goes_out(config, source):
    digest = build_digest(config, now=NOW, results=[make_result(source, make_posting())])
    assert should_send(digest, config)[0]


# ------------------------------------------------ the check-sources exit code

def _check_sources(config, monkeypatch, results, all_sources=True):
    import argparse

    import radar.cli as cli

    monkeypatch.setattr(cli, "fetch_all", lambda *a, **k: results)
    monkeypatch.setattr(cli, "_load", lambda args: config)
    return cli.cmd_check_sources(
        argparse.Namespace(all=all_sources, config=None, env=None, verbose=False)
    )


def test_check_sources_is_green_when_every_live_source_answers(config, source, monkeypatch):
    assert _check_sources(config, monkeypatch, [make_result(source, make_posting())]) == 0


def test_check_sources_is_red_when_a_live_source_fails(config, source, monkeypatch):
    results = [FetchResult(source=source, error="HTTP 404")]
    assert _check_sources(config, monkeypatch, results) == 1


def test_a_source_disabled_in_config_does_not_hold_check_sources_red(
    config, source, monkeypatch
):
    """`--all` shows disabled sources so their state stays visible. An employer
    switched off precisely because it is known broken must not keep the command
    red for ever, or red stops meaning anything."""
    disabled = replace(source, enabled=False)
    results = [FetchResult(source=disabled, error="HTTP 422")]
    assert _check_sources(config, monkeypatch, results) == 0


def test_a_keyless_source_does_not_hold_check_sources_red(config, source, monkeypatch):
    results = [FetchResult(source=source, skipped="no CAREERJET_AFFID set")]
    assert _check_sources(config, monkeypatch, results) == 0
