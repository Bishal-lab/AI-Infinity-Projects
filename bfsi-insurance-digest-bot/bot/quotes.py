"""Share prices for a small watchlist, from Yahoo Finance's chart endpoint.

Why this source: it needs no API key and no paid tier, which is the same
constraint the news sources are held to — a job that has to keep working on a
schedule for years without anyone watching it cannot depend on a key someone
has to remember to renew.

The endpoint is undocumented, so it is treated as unreliable by construction:
every failure is a value, never an exception, and a missing quote renders as a
muted line rather than taking the morning brief down. A brief with a story and
no price is still a brief; a brief that failed to send is not.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

from .config import FetchSettings, WatchlistEntry
from .model import Article

log = logging.getLogger(__name__)

API_ROOT = "https://query1.finance.yahoo.com/v8/finance/chart"

#: A month of daily closes: enough for a day move and a one-month trend, small
#: enough that the response stays a few kilobytes.
_RANGE = "1mo"
_INTERVAL = "1d"


@dataclass
class Quote:
    """One instrument's price, or the reason there isn't one."""

    entry: WatchlistEntry
    price: float | None = None
    previous_close: float | None = None
    currency: str = ""
    month_ago_close: float | None = None
    error: str | None = None
    #: Headlines from today's brief that name this company.
    stories: list[Article] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and self.price is not None

    @property
    def day_change(self) -> float | None:
        if self.price is None or not self.previous_close:
            return None
        return self.price - self.previous_close

    @property
    def day_change_pct(self) -> float | None:
        change = self.day_change
        if change is None or not self.previous_close:
            return None
        return change / self.previous_close * 100.0

    @property
    def month_change_pct(self) -> float | None:
        if self.price is None or not self.month_ago_close:
            return None
        return (self.price - self.month_ago_close) / self.month_ago_close * 100.0

    @property
    def direction(self) -> str:
        """"up", "down" or "flat" — what the renderers colour on."""
        pct = self.day_change_pct
        if pct is None:
            return "flat"
        if pct > 0.05:
            return "up"
        if pct < -0.05:
            return "down"
        return "flat"


def _parse(payload: dict, entry: WatchlistEntry) -> Quote:
    """Pull the four numbers we need out of Yahoo's envelope.

    Written defensively on purpose: this is an undocumented shape that can gain
    or lose keys without notice, and every one of them is optional to us.
    """
    chart = payload.get("chart") or {}
    if chart.get("error"):
        detail = chart["error"]
        message = detail.get("description") if isinstance(detail, dict) else str(detail)
        return Quote(entry=entry, error=message or "the quote service returned an error")

    results = chart.get("result") or []
    if not results:
        return Quote(entry=entry, error="no data for this symbol")

    meta = results[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None:
        return Quote(entry=entry, error="the response carried no price")

    # The daily closes inside the requested range, with the nulls Yahoo leaves
    # on non-trading days removed.
    closes: list[float] = []
    try:
        raw = results[0]["indicators"]["quote"][0]["close"]
        closes = [float(c) for c in raw if c is not None]
    except (KeyError, IndexError, TypeError, ValueError):
        pass

    # `chartPreviousClose` is the close *before the range begins* — a month ago
    # at this range, not yesterday. Using it as the day's reference made every
    # symbol report the same number for its day move and its month move, which
    # is what gave the bug away. Yesterday is `previousClose` when the endpoint
    # sends it, and otherwise the last-but-one close in the series.
    previous = meta.get("previousClose")
    if previous is None and len(closes) >= 2:
        previous = closes[-2]

    # The oldest close in the range, falling back to the pre-range close, which
    # is what that field is actually good for.
    month_ago = closes[0] if closes else meta.get("chartPreviousClose")

    return Quote(
        entry=entry,
        price=float(price),
        previous_close=float(previous) if previous else None,
        currency=str(meta.get("currency") or ""),
        month_ago_close=float(month_ago) if month_ago else None,
    )


def fetch_quote(
    entry: WatchlistEntry,
    settings: FetchSettings,
    session: requests.Session | None = None,
) -> Quote:
    """One symbol, retried like a feed is."""
    owns_session = session is None
    session = session or requests.Session()
    url = f"{API_ROOT}/{entry.symbol}"
    params = {"range": _RANGE, "interval": _INTERVAL}
    headers = {"User-Agent": settings.user_agent, "Accept": "application/json"}
    last_error = "unknown error"

    try:
        for attempt in range(settings.retries + 1):
            try:
                response = session.get(
                    url, params=params, headers=headers,
                    timeout=settings.timeout_seconds,
                )
                if response.status_code >= 400:
                    last_error = f"HTTP {response.status_code}"
                    # As with feeds: a 4xx that is not rate-limiting is a fact
                    # about the symbol, not a transient blip.
                    if response.status_code != 429 and response.status_code < 500:
                        break
                else:
                    return _parse(response.json(), entry)
            except ValueError:
                last_error = "the quote service did not return JSON"
                break
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < settings.retries:
                time.sleep(settings.backoff_seconds * (attempt + 1))
    finally:
        if owns_session:
            session.close()

    log.warning("quote %s failed: %s", entry.symbol, last_error)
    return Quote(entry=entry, error=last_error)


def fetch_all(
    entries: tuple[WatchlistEntry, ...] | list[WatchlistEntry],
    settings: FetchSettings,
) -> list[Quote]:
    """Sequential on purpose: a watchlist is a handful of symbols, and one
    connection is politer to a free endpoint than a thread pool."""
    if not entries:
        return []
    session = requests.Session()
    try:
        return [fetch_quote(entry, settings, session=session) for entry in entries]
    finally:
        session.close()


def attach_stories(quotes: list[Quote], articles: list[Article]) -> None:
    """Pair each quote with the headlines in today's brief that name it.

    This is the whole point of putting a price in a news brief. A number on its
    own invites you to guess why it moved; the number next to the day's stories
    about that company is what makes the move readable. The bot does not write
    the interpretation — it has no model and should not pretend to — it puts
    the two things side by side and leaves the reading to you.
    """
    from .relevance import KeywordMatcher

    for quote in quotes:
        matcher = KeywordMatcher(quote.entry.match)
        quote.stories = [
            article for article in articles
            if matcher.find(f"{article.title} {article.summary}")
        ]
