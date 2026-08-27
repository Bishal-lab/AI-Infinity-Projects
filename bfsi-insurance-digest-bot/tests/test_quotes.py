"""The watchlist: parsing a quote, pairing it with the news, and failing safely.

The guiding rule here is that a price is a garnish. Every test that involves a
failure asserts the brief survived it.
"""

from __future__ import annotations

import pytest

from bot.config import FetchSettings, WatchlistEntry
from bot.model import Article
from bot.quotes import Quote, _parse, attach_stories
from bot.render import render_email_html, render_email_text, render_telegram

ENTRY = WatchlistEntry(symbol="MFSL.NS", name="Max Financial Services",
                       match=("Max Financial", "Max Life"))


def envelope(price=1200.0, previous=1150.0, closes=(1000.0, 1100.0, 1200.0)):
    return {
        "chart": {
            "error": None,
            "result": [{
                "meta": {
                    "symbol": "MFSL.NS", "currency": "INR",
                    "regularMarketPrice": price, "chartPreviousClose": previous,
                },
                "indicators": {"quote": [{"close": list(closes)}]},
            }],
        }
    }


def article(title, summary="", url="https://example.test/a"):
    return Article(source_id="s", source_name="S", title=title, url=url, summary=summary)


# ------------------------------------------------------------------- parsing

def test_a_quote_carries_the_day_move_and_the_month_trend():
    quote = _parse(envelope(), ENTRY)
    assert quote.ok
    assert quote.price == 1200.0
    assert quote.day_change == pytest.approx(50.0)
    assert quote.day_change_pct == pytest.approx(50 / 1150 * 100)
    assert quote.month_change_pct == pytest.approx(20.0)
    assert quote.direction == "up"


def test_the_oldest_real_close_is_used_for_the_month(   ):
    """Yahoo leaves nulls on non-trading days; the first one is often None."""
    quote = _parse(envelope(closes=(None, None, 1000.0, 1200.0)), ENTRY)
    assert quote.month_change_pct == pytest.approx(20.0)


def test_direction_has_a_flat_band():
    """A 0.01% drift is not a fall, and colouring it red would say it was."""
    assert _parse(envelope(price=1000.0, previous=1000.0), ENTRY).direction == "flat"
    assert _parse(envelope(price=900.0, previous=1000.0), ENTRY).direction == "down"


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"chart": {"error": {"description": "Not Found"}}}, "Not Found"),
        ({"chart": {"result": []}}, "no data"),
        ({"chart": {"result": [{"meta": {}}]}}, "no price"),
        ({}, "no data"),
    ],
)
def test_a_broken_response_becomes_a_value_not_an_exception(payload, reason):
    """One dead symbol must never take the morning brief down with it."""
    quote = _parse(payload, ENTRY)
    assert not quote.ok
    assert reason in quote.error


def test_a_missing_previous_close_still_yields_a_price():
    """Partial data is worth printing; the change is simply omitted."""
    quote = _parse(envelope(previous=None), ENTRY)
    assert quote.ok and quote.price == 1200.0
    assert quote.day_change_pct is None
    assert quote.direction == "flat"


# ------------------------------------------------------- pairing with the news

def test_a_quote_is_paired_with_the_stories_that_name_it():
    """A number alone invites a guess about why it moved. This is the answer."""
    quotes = [Quote(entry=ENTRY, price=1.0, previous_close=1.0)]
    articles = [
        article("SEBI drops fraud case against Max Financial, Axis Bank"),
        article("Max Life first-year premium climbs"),
        article("HDFC Bank deposit franchise thins"),
    ]
    attach_stories(quotes, articles)
    assert [a.title for a in quotes[0].stories] == [
        "SEBI drops fraud case against Max Financial, Axis Bank",
        "Max Life first-year premium climbs",
    ]


def test_the_match_list_is_what_links_a_price_to_a_story():
    """The press writes "Max Life" far more often than the listed entity's
    name, so matching on the display name alone would find almost nothing."""
    narrow = WatchlistEntry(symbol="MFSL.NS", name="Max Financial Services",
                            match=("Max Financial Services",))
    quotes = [Quote(entry=narrow, price=1.0)]
    attach_stories(quotes, [article("Max Life launches a new term plan")])
    assert quotes[0].stories == []


def test_a_quote_with_no_news_is_still_a_quote():
    quotes = [Quote(entry=ENTRY, price=1.0, previous_close=1.0)]
    attach_stories(quotes, [article("Unrelated bank story")])
    assert quotes[0].stories == []
    assert quotes[0].ok


# ------------------------------------------------------------------ rendering

@pytest.fixture
def digest_with_quotes(config):
    from bot.digest import Digest, DigestSection

    import datetime as dt
    story = article("SEBI drops Rs 3,912 cr fraud case against Max Financial, Axis Bank")
    story.section_id = "life_insurance"
    quote = Quote(entry=ENTRY, price=1234.5, previous_close=1200.0,
                  currency="INR", month_ago_close=1000.0, stories=[story])
    broken = Quote(entry=WatchlistEntry(symbol="X.NS", name="Dead Ticker"),
                   error="HTTP 404")
    now = dt.datetime(2026, 8, 27, 9, 43, tzinfo=dt.timezone.utc)
    return Digest(
        generated_at=now, window_start=now, window_end=now,
        sections=[DigestSection(config.section("life_insurance"), [story])],
        sources_total=11, quotes=[quote, broken],
    )


def test_all_three_renderers_carry_the_price_and_its_story(digest_with_quotes, config):
    html = render_email_html(digest_with_quotes, config)
    text = render_email_text(digest_with_quotes, config)
    telegram = "\n".join(render_telegram(digest_with_quotes, config))

    for output in (html, text, telegram):
        assert "Max Financial Services" in output
        assert "1,234.50" in output
        assert "+2.88%" in output          # the day move: 34.5 / 1200
        assert "Max Financial" in output   # the story, under the price


def test_a_dead_symbol_renders_as_a_note_and_nothing_else_breaks(digest_with_quotes, config):
    html = render_email_html(digest_with_quotes, config)
    text = render_email_text(digest_with_quotes, config)
    assert "Dead Ticker" in html and "unavailable" in html
    assert "Dead Ticker" in text and "unavailable" in text
    # The news survived the broken quote, which is the whole point.
    assert "SEBI" in html and "SEBI" in text


def test_the_month_trend_is_shown_when_there_is_one(digest_with_quotes, config):
    assert "+23.45%" in render_email_text(digest_with_quotes, config)


def test_no_watchlist_means_no_watchlist_block(config):
    """The brief predates this feature and must render exactly as before."""
    from bot.digest import Digest, DigestSection

    import datetime as dt
    story = article("IRDAI eases surrender value norms")
    story.section_id = "life_insurance"
    now = dt.datetime(2026, 8, 27, 9, 43, tzinfo=dt.timezone.utc)
    plain = Digest(generated_at=now, window_start=now, window_end=now,
                   sections=[DigestSection(config.section("life_insurance"), [story])],
                   sources_total=11)
    assert "Watchlist" not in render_email_html(plain, config)
    assert "WATCHLIST" not in render_email_text(plain, config)


# --------------------------------------------------------------- shipped config

def test_the_shipped_watchlist_matches_on_names_the_press_actually_uses(config):
    entries = {w.symbol: w for w in config.enabled_watchlist}
    assert "MFSL.NS" in entries, "Max Financial is the one that was asked for"
    assert "Max Life" in entries["MFSL.NS"].match
    for entry in config.enabled_watchlist:
        assert entry.match, f"{entry.symbol} would never link to a story"


def test_every_watchlist_name_is_a_known_life_insurance_keyword(config):
    """A watchlist entry whose company the taxonomy does not track would show a
    price beside a section that can never carry news about it."""
    known = {k.lower() for k in config.section("life_insurance").strong}
    for entry in config.enabled_watchlist:
        assert any(m.lower() in known for m in entry.match), entry.symbol
