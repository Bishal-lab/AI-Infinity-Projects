"""What actually lands in the inbox, and in the state branch."""

from __future__ import annotations

from radar.digest import build_digest
from radar.render import (
    render_email_html,
    render_email_text,
    render_markdown,
    subject_line,
)
from tests.conftest import NOW, make_posting, make_result


def _digest(config, source, *postings):
    return build_digest(
        config, now=NOW, results=[make_result(source, *(postings or (make_posting(),)))]
    )


def test_subject_counts_the_openings(config, source):
    subject = subject_line(_digest(config, source), config)
    assert "1 new opening" in subject
    assert "1 strong" in subject


def test_subject_says_so_when_there_is_nothing(config):
    digest = build_digest(config, now=NOW, results=[])
    assert "nothing new" in subject_line(digest, config)


def test_html_carries_the_link_the_score_and_the_reasons(config, source):
    html = render_email_html(_digest(config, source), config)
    assert "https://example.test/jobs/1" in html
    assert "Why it fits:" in html
    assert "/100" in html
    assert "View the role" in html


def test_html_always_offers_the_saved_searches(config):
    """The boards the radar cannot read are the point of this block, so it has
    to survive an empty digest."""
    html = render_email_html(build_digest(config, now=NOW, results=[]), config)
    assert "Search these yourself" in html
    assert "iimjobs" in html


def test_text_alternative_is_readable(config, source):
    text = render_email_text(_digest(config, source), config)
    assert "VP ROLE RADAR" in text
    assert "STRONG FIT (1)" in text
    assert "https://example.test/jobs/1" in text


def test_markdown_labels_every_field_for_its_reader(config, source):
    markdown = render_markdown(_digest(config, source), config)
    assert markdown.startswith("# VP Role Radar")
    for label in ("**Fit:**", "**Where:**", "**Why it fits:**", "**Apply:**"):
        assert label in markdown


def test_an_empty_markdown_digest_says_so_plainly(config):
    markdown = render_markdown(build_digest(config, now=NOW, results=[]), config)
    assert "**New openings:** 0" in markdown
    assert "No new openings" in markdown


def test_the_footer_names_a_broken_source(config, source):
    from radar.sources.base import FetchResult

    digest = build_digest(config, now=NOW, results=[FetchResult(source=source, error="HTTP 503")])
    text = render_email_text(digest, config)
    assert "Could not be read: Test Board (HTTP 503)" in text


def test_an_empty_brief_distinguishes_quiet_from_broken(config, source):
    from radar.sources.base import FetchResult

    quiet = render_email_text(build_digest(config, now=NOW, results=[]), config)
    broken = render_email_text(
        build_digest(config, now=NOW, results=[FetchResult(source=source, error="HTTP 503")]),
        config,
    )
    assert "No new openings cleared the fit bar" in quiet
    assert "broken run" in broken
