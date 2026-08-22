"""Rendering: what actually lands in Telegram and in the inbox."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta

import pytest

from bot.digest import Digest, DigestSection, Stats, local_zone
from bot.model import Article
from bot.render import (
    render_email_html,
    render_email_text,
    render_telegram,
    subject_line,
)
from tests.conftest import NOW

ZONE = local_zone("Asia/Kolkata")


def _article(title: str, summary: str = "A summary.", **kwargs) -> Article:
    return Article(
        source_id="s", source_name="ET BFSI", title=title,
        url=kwargs.pop("url", "https://example.test/story"),
        summary=summary, published=NOW - timedelta(hours=2),
        publisher=kwargs.pop("publisher", "Mint"), score=9.0, **kwargs,
    )


def _digest(config, sections=None, failures=()) -> Digest:
    local_now = NOW.astimezone(ZONE)
    if sections is None:
        sections = [
            DigestSection(
                config.section("life_insurance"),
                [
                    _article("IRDAI eases surrender value norms", duplicate_count=2),
                    _article("HDFC Life posts a 15% rise in VNB margin",
                             url="https://example.test/2"),
                ],
            ),
            DigestSection(
                config.section("banking_nbfc"),
                [_article("Bank credit growth slows to 11%", url="https://example.test/3")],
            ),
        ]
    return Digest(
        generated_at=local_now,
        window_start=(NOW - timedelta(hours=26)).astimezone(ZONE),
        window_end=local_now,
        sections=list(sections),
        failures=list(failures),
        sources_total=17,
        stats=Stats(),
    )


# ------------------------------------------------------------------- telegram

def test_telegram_message_carries_every_headline_as_a_link(config):
    messages = render_telegram(_digest(config), config)
    body = "\n".join(messages)
    assert '<a href="https://example.test/story">IRDAI eases surrender value norms</a>' in body
    assert "LIFE INSURANCE" in body and "BANKING &amp; NBFC" in body


def test_telegram_escapes_markup_in_source_text(config):
    """A headline containing < or & must not break Telegram's HTML parser."""
    sections = [
        DigestSection(
            config.section("life_insurance"),
            [_article("Insurers face <new> rules & levies", url="https://example.test/x")],
        )
    ]
    body = render_telegram(_digest(config, sections), config)[0]
    assert "&lt;new&gt; rules &amp; levies" in body
    assert "<new>" not in body


def test_telegram_splits_long_briefs_on_item_boundaries(config):
    many = [
        DigestSection(
            config.section("life_insurance"),
            [
                _article(f"Life insurance headline number {i}", url=f"https://example.test/{i}")
                for i in range(40)
            ],
        )
    ]
    small = replace(config, telegram=replace(config.telegram, max_message_chars=900))
    messages = render_telegram(_digest(small, many), small)
    assert len(messages) > 1
    assert all(len(message) <= 900 for message in messages)
    # No message may end mid-item: every one closes a complete anchor tag.
    for message in messages:
        assert message.count("<a href=") == message.count("</a>")


def test_a_section_spanning_two_messages_repeats_its_heading(config):
    many = [
        DigestSection(
            config.section("life_insurance"),
            [_article(f"Headline {i}", url=f"https://example.test/{i}") for i in range(30)],
        )
    ]
    small = replace(config, telegram=replace(config.telegram, max_message_chars=800))
    messages = render_telegram(_digest(small, many), small)
    assert len(messages) > 1
    assert "LIFE INSURANCE" in messages[1]


def test_summaries_can_be_turned_off(config):
    bare = replace(config, telegram=replace(config.telegram, include_summaries=False))
    body = render_telegram(_digest(bare), bare)[0]
    assert "A summary." not in body


def test_an_empty_brief_says_so_rather_than_sending_a_blank(config):
    body = render_telegram(_digest(config, sections=[]), config)[0]
    assert "No BFSI or life insurance stories" in body


def test_the_footer_reports_source_health(config):
    body = "\n".join(
        render_telegram(_digest(config, failures=[("Moneycontrol", "HTTP 503")]), config)
    )
    assert "16/17 sources responded" in body
    assert "Moneycontrol" in body


# ---------------------------------------------------------------------- email

def test_email_html_is_a_complete_document_with_links(config):
    html = render_email_html(_digest(config), config)
    assert html.startswith("<!doctype html>") and html.rstrip().endswith("</html>")
    assert 'href="https://example.test/story"' in html
    assert "IRDAI eases surrender value norms" in html


def test_email_html_escapes_source_text(config):
    sections = [
        DigestSection(
            config.section("life_insurance"),
            [_article("Rules & <levies>", url="https://example.test/x")],
        )
    ]
    html = render_email_html(_digest(config, sections), config)
    assert "Rules &amp; &lt;levies&gt;" in html


def test_email_html_carries_no_external_assets(config):
    """Remote images and stylesheets get blocked or stripped by mail clients."""
    html = render_email_html(_digest(config), config)
    assert "<img" not in html and "<link" not in html and "<style" not in html


def test_email_text_lists_headlines_with_their_urls(config):
    text = render_email_text(_digest(config), config)
    assert "LIFE INSURANCE" in text
    assert "1. IRDAI eases surrender value norms" in text
    assert "https://example.test/story" in text
    assert "<" not in text  # no markup leaks into the plain-text part


def test_corroboration_is_shown_once_a_story_arrives_from_several_feeds(config):
    text = render_email_text(_digest(config), config)
    assert "+2 more" in text


def test_subject_uses_the_configured_template(config):
    subject = subject_line(_digest(config), config)
    assert subject.startswith("BFSI & Life Insurance Daily")
    assert re.search(r"\d{1,2} \w{3} \d{4}", subject)


def test_subject_supports_richer_placeholders(config):
    custom = replace(
        config, email=replace(config.email, subject_template="{count} updates · {top_section}")
    )
    assert subject_line(_digest(custom), custom) == "3 updates · Life Insurance"


def test_an_unknown_placeholder_falls_back_instead_of_crashing(config):
    broken = replace(config, email=replace(config.email, subject_template="{nonsense}"))
    assert "BFSI" in subject_line(_digest(broken), broken)


def test_a_total_source_failure_says_so_instead_of_claiming_a_quiet_day(config):
    """An empty inbox for the wrong reason is the failure mode that hides
    itself; the brief has to name it."""
    broken = _digest(config, sections=[], failures=[(f"Feed {i}", "HTTP 503") for i in range(17)])
    text = render_email_text(broken, config)
    assert "None of the 17 news sources could be reached" in text
    assert "check-sources" in text


def test_an_empty_brief_renders_in_both_email_parts(config):
    empty = _digest(config, sections=[])
    assert "No BFSI or life insurance stories" in render_email_text(empty, config)
    assert "No BFSI or life insurance stories" in render_email_html(empty, config)
