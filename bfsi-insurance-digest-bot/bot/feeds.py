"""Fetching and parsing RSS/Atom feeds using nothing but the standard library
plus ``requests``.

Why not feedparser: the digest only needs title, link, summary and date from
RSS 2.0, RSS 1.0/RDF and Atom. Doing that with ``xml.etree`` keeps the
dependency list to two well-known packages, which matters for a job that has to
install cleanly on a schedule for years without anyone watching it.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Iterable, Sequence
from xml.etree import ElementTree

import requests

from .config import FetchSettings, Source
from .model import RawEntry

log = logging.getLogger(__name__)

# Feeds in the wild carry entities that are legal in HTML but undeclared in XML.
# Rather than fail the whole source on one stray &nbsp;, swap the common ones
# for their numeric equivalents and re-parse.
_HTML_ENTITIES = {
    "&nbsp;": "&#160;", "&ndash;": "&#8211;", "&mdash;": "&#8212;",
    "&rsquo;": "&#8217;", "&lsquo;": "&#8216;", "&rdquo;": "&#8221;",
    "&ldquo;": "&#8220;", "&hellip;": "&#8230;", "&eacute;": "&#233;",
    "&pound;": "&#163;", "&euro;": "&#8364;", "&trade;": "&#8482;",
    "&copy;": "&#169;", "&reg;": "&#174;", "&deg;": "&#176;", "&bull;": "&#8226;",
}
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BARE_AMP = re.compile(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[a-zA-Z][a-zA-Z0-9]{1,31};)")


@dataclass
class FetchResult:
    """The outcome of one source, successful or not.

    Failures are values rather than exceptions: one dead feed must never take
    the morning digest down with it.
    """

    source: Source
    entries: list[RawEntry] = field(default_factory=list)
    status_code: int | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


def _strip_ns(tag: str) -> str:
    """'{http://www.w3.org/2005/Atom}entry' -> 'entry'."""
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _first_text(element: ElementTree.Element, *names: str) -> str:
    """First non-empty text among the named direct children."""
    for name in names:
        for child in element:
            if _strip_ns(child.tag) != name:
                continue
            text = (child.text or "").strip()
            if text:
                return text
            # Atom allows <content type="xhtml"> with nested markup.
            inner = "".join(ElementTree.tostring(g, encoding="unicode") for g in child)
            if inner.strip():
                return inner.strip()
    return ""


def _extract_link(element: ElementTree.Element) -> str:
    """RSS puts the URL in <link> text; Atom puts it in a href attribute."""
    fallback = ""
    for child in element:
        if _strip_ns(child.tag) != "link":
            continue
        href = (child.get("href") or "").strip()
        rel = (child.get("rel") or "alternate").strip().lower()
        if href:
            if rel == "alternate":
                return href
            fallback = fallback or href
            continue
        text = (child.text or "").strip()
        if text:
            return text
    if fallback:
        return fallback
    # RSS 1.0 sometimes carries only a guid that happens to be the permalink.
    guid = _first_text(element, "guid", "id")
    return guid if guid.lower().startswith("http") else ""


def _extract_publisher(element: ElementTree.Element) -> str:
    """The outlet that published the story, when the feed is not itself it.

    Search feeds (Google News) name the originating outlet in <source>; that is
    the only element that means "publication". <dc:creator> and <author> are a
    journalist's byline, which would be wrong as the attribution on an item.
    """
    for child in element:
        if _strip_ns(child.tag) == "source":
            text = (child.text or "").strip()
            if text:
                return text
    return ""


def _repair(text: str) -> str:
    for entity, numeric in _HTML_ENTITIES.items():
        text = text.replace(entity, numeric)
    text = _BARE_AMP.sub("&amp;", text)
    return _CONTROL_CHARS.sub("", text)


def parse_feed(payload: bytes | str) -> list[RawEntry]:
    """Parse RSS 2.0, RSS 1.0/RDF or Atom into `RawEntry` objects.

    Raises ``ElementTree.ParseError`` only when the document is unsalvageable.
    """
    if isinstance(payload, bytes):
        # Let ElementTree honour the XML declaration's encoding.
        text = payload.decode("utf-8", errors="replace") if b"encoding" not in payload[:120] else None
        if text is None:
            try:
                root = ElementTree.fromstring(payload)
            except ElementTree.ParseError:
                text = payload.decode("utf-8", errors="replace")
            else:
                return _entries_from_root(root)
    else:
        text = payload

    text = text.lstrip("﻿ \t\r\n")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        root = ElementTree.fromstring(_repair(text))
    return _entries_from_root(root)


def _entries_from_root(root: ElementTree.Element) -> list[RawEntry]:
    items = [el for el in root.iter() if _strip_ns(el.tag) in {"item", "entry"}]
    entries: list[RawEntry] = []
    for item in items:
        entries.append(
            RawEntry(
                title=_first_text(item, "title"),
                link=_extract_link(item),
                summary=_first_text(item, "description", "summary", "content", "encoded"),
                published_raw=_first_text(
                    item, "pubdate", "published", "date", "updated", "modified", "created"
                ),
                guid=_first_text(item, "guid", "id"),
                publisher=_extract_publisher(item),
            )
        )
    return entries


def fetch_source(
    source: Source,
    settings: FetchSettings,
    session: requests.Session | None = None,
) -> FetchResult:
    """Fetch one feed, retrying transient failures with a linear backoff."""
    owns_session = session is None
    session = session or requests.Session()
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    }
    started = time.monotonic()
    last_error = "unknown error"
    status: int | None = None

    try:
        for attempt in range(settings.retries + 1):
            try:
                response = session.get(
                    source.url,
                    headers=headers,
                    timeout=settings.timeout_seconds,
                    allow_redirects=True,
                )
                status = response.status_code
                if status >= 400:
                    last_error = f"HTTP {status}"
                    # 4xx other than rate-limiting will not fix themselves.
                    if status != 429 and status < 500:
                        break
                else:
                    entries = parse_feed(response.content)
                    if not entries:
                        last_error = "feed parsed but contained no items"
                        break
                    return FetchResult(
                        source=source,
                        entries=entries,
                        status_code=status,
                        elapsed_seconds=time.monotonic() - started,
                    )
            except ElementTree.ParseError as exc:
                last_error = f"could not parse feed XML ({exc})"
                break
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < settings.retries:
                time.sleep(settings.backoff_seconds * (attempt + 1))
    finally:
        if owns_session:
            session.close()

    log.warning("source %s failed: %s", source.id, last_error)
    return FetchResult(
        source=source,
        status_code=status,
        error=last_error,
        elapsed_seconds=time.monotonic() - started,
    )


def fetch_all(
    sources: Sequence[Source],
    settings: FetchSettings,
) -> list[FetchResult]:
    """Fetch every source concurrently, preserving the configured order."""
    if not sources:
        return []
    workers = min(settings.max_workers, len(sources))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # A session per worker thread; requests.Session is not thread-safe.
        local_sessions: dict[int, requests.Session] = {}

        def run(source: Source) -> FetchResult:
            import threading

            key = threading.get_ident()
            session = local_sessions.get(key)
            if session is None:
                session = requests.Session()
                local_sessions[key] = session
            return fetch_source(source, settings, session=session)

        try:
            return list(pool.map(run, sources))
        finally:
            for session in local_sessions.values():
                session.close()
