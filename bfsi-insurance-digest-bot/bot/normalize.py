"""Turning raw feed entries into clean, comparable `Article` objects.

Feeds are inconsistent in every dimension that matters: dates come in three
formats, summaries arrive as HTML fragments, and the same story reaches us under
four URLs that differ only in campaign tracking. Everything downstream assumes
this module has already dealt with that.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping
from urllib.parse import parse_qsl, urlsplit, urlunsplit, urlencode

from .config import Source
from .dedupe import title_tokens
from .model import Article, RawEntry

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_TRAILING_ATTRIBUTION = re.compile(r"\s+[-–—|]\s+[^-–—|]{2,40}$")
# A publisher that is really a hostname: "livemint.com", "scanx.trade".
_HOSTNAME = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$", re.IGNORECASE)

# Campaign and session parameters. Two URLs that differ only by these are the
# same story, and keeping them makes the seen-store leak duplicates.
_JUNK_PARAM_PREFIXES = ("utm_", "pk_", "mc_", "hsa_", "ito_")
_JUNK_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "ref", "referrer",
    "cmpid", "campaign_id", "source", "src", "from", "feature", "share",
    "amp", "output", "ncid", "smid", "cid", "utm", "sh",
}

# Dates that neither RFC 822 nor ISO 8601 covers, seen on Indian news feeds.
_EXTRA_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d %b %Y %H:%M:%S",
    "%d %B %Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
)


def strip_html(value: str) -> str:
    """Flatten an HTML fragment to a single line of readable text."""
    if not value:
        return ""
    # Unescape first so that "&lt;p&gt;" encoded markup is stripped too.
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>", " ", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    return _WHITESPACE.sub(" ", text).strip()


def truncate(value: str, limit: int) -> str:
    """Cut to `limit` characters on a word boundary, with an ellipsis."""
    if limit <= 0 or len(value) <= limit:
        return value
    cut = value[: limit + 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    else:
        cut = cut[:limit]
    return cut.rstrip(" ,;:.-–—") + "…"


def parse_date(value: str) -> datetime | None:
    """Parse a feed date into an aware UTC datetime, or None if unreadable."""
    text = (value or "").strip()
    if not text:
        return None

    # RFC 822 / RFC 2822 — the RSS default, e.g. "Tue, 19 Aug 2025 06:30:00 +0530".
    if "," in text or text[:3].isalpha():
        try:
            parsed = parsedate_to_datetime(text)
            if parsed is not None:
                return _to_utc(parsed)
        except (TypeError, ValueError, IndexError):
            pass

    # ISO 8601 — the Atom default.
    iso = text.replace("Z", "+00:00")
    iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", iso)
    try:
        return _to_utc(datetime.fromisoformat(iso))
    except ValueError:
        pass

    for fmt in _EXTRA_DATE_FORMATS:
        try:
            return _to_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def _to_utc(value: datetime) -> datetime:
    """A naive timestamp is treated as UTC — feeds that omit an offset are
    almost always publishing UTC, and guessing IST would shift stories a day."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def canonical_url(url: str) -> str:
    """Strip tracking noise so the same story compares equal across feeds."""
    text = (url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if not parts.scheme or not parts.netloc:
        return text

    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(":80") or host.endswith(":443"):
        host = host.rsplit(":", 1)[0]

    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if key.lower() not in _JUNK_PARAMS
        and not key.lower().startswith(_JUNK_PARAM_PREFIXES)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(kept), ""))


def clean_title(title: str, publisher: str) -> str:
    """Drop the ' - Publisher' suffix that search feeds append to headlines."""
    text = strip_html(title)
    if publisher:
        suffix = f" - {publisher}"
        if text.lower().endswith(suffix.lower()):
            return text[: -len(suffix)].strip()
        # Some feeds use an en dash or a pipe instead of a hyphen.
        match = _TRAILING_ATTRIBUTION.search(text)
        if match and match.group(0).strip(" -–—|").lower() == publisher.lower():
            return text[: match.start()].strip()
    return text


def echoes_title(summary: str, title: str, publisher: str = "") -> bool:
    """Whether a summary says nothing the headline has not already said.

    Search feeds set an item's description to its own headline with the outlet
    appended, so printing both shows the reader the same sentence twice. The
    test is a strict subset of meaningful words — a summary survives the moment
    it contributes one word of its own — which is what keeps a genuinely short
    summary from being mistaken for an echo.
    """
    if not summary:
        return False
    words = title_tokens(summary)
    if not words:
        # Nothing but stopwords and short fragments; no information either way.
        return True
    return words <= (title_tokens(title) | title_tokens(publisher))


def clean_publisher(publisher: str, aliases: Mapping[str, str] | None = None) -> str:
    """Tidy the outlet name a feed reports.

    Google News sometimes names the outlet by hostname rather than masthead.
    A hostname is resolved through the configured alias map, and otherwise left
    alone: no rule turns "bfsi.economictimes.indiatimes.com" into "ET BFSI"
    without guessing, and a confidently wrong byline is worse than an ugly one.
    """
    name = strip_html(publisher)
    if not name or not _HOSTNAME.match(name):
        return name
    host = name.lower().removeprefix("www.")
    return (aliases or {}).get(host, host)


def to_article(
    entry: RawEntry,
    source: Source,
    summary_limit: int = 320,
    publisher_aliases: Mapping[str, str] | None = None,
) -> Article | None:
    """Build an `Article`, or None when the entry lacks a title or a link."""
    publisher = clean_publisher(entry.publisher, publisher_aliases)
    title = clean_title(entry.title, publisher)
    url = canonical_url(entry.link)
    if not title or not url:
        return None

    # Tested before truncation, so a long summary cannot be cut down into
    # something that merely looks like an echo of the headline.
    summary = strip_html(entry.summary)
    if echoes_title(summary, title, publisher):
        summary = ""

    return Article(
        source_id=source.id,
        source_name=source.name,
        title=truncate(title, 200),
        url=url,
        summary=truncate(summary, summary_limit),
        published=parse_date(entry.published_raw),
        publisher=publisher,
        guid=(entry.guid or "").strip(),
    )
