"""Any board that publishes a jobs feed.

The parser is the same RSS 2.0 / RSS 1.0 / Atom reader the BFSI digest bot in
this repository uses, kept to the standard library for the same reason: a job
that runs unattended for years should not depend on a feed library's release
cadence. Only the mapping at the end differs — a feed item becomes a posting
rather than an article.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree

import requests

from ..config import FetchSettings, Profile, Source
from ..model import RawPosting
from .base import SourceError, register

# Feeds in the wild carry entities that are legal in HTML but undeclared in
# XML. Rather than fail a whole source on one stray &nbsp;, swap the common
# ones for their numeric equivalents and re-parse.
_HTML_ENTITIES = {
    "&nbsp;": "&#160;", "&ndash;": "&#8211;", "&mdash;": "&#8212;",
    "&rsquo;": "&#8217;", "&lsquo;": "&#8216;", "&rdquo;": "&#8221;",
    "&ldquo;": "&#8220;", "&hellip;": "&#8230;", "&eacute;": "&#233;",
    "&pound;": "&#163;", "&euro;": "&#8364;", "&trade;": "&#8482;",
    "&copy;": "&#169;", "&reg;": "&#174;", "&deg;": "&#176;", "&bull;": "&#8226;",
}
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BARE_AMP = re.compile(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[a-zA-Z][a-zA-Z0-9]{1,31};)")


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _first_text(element: ElementTree.Element, *names: str) -> str:
    for name in names:
        for child in element:
            if _strip_ns(child.tag) != name:
                continue
            text = (child.text or "").strip()
            if text:
                return text
            inner = "".join(ElementTree.tostring(g, encoding="unicode") for g in child)
            if inner.strip():
                return inner.strip()
    return ""


def _extract_link(element: ElementTree.Element) -> str:
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
    guid = _first_text(element, "guid", "id")
    return guid if guid.lower().startswith("http") else ""


def _repair(text: str) -> str:
    for entity, numeric in _HTML_ENTITIES.items():
        text = text.replace(entity, numeric)
    text = _BARE_AMP.sub("&amp;", text)
    return _CONTROL_CHARS.sub("", text)


def parse_feed(payload: bytes | str, company: str = "") -> list[RawPosting]:
    """Parse a jobs feed into `RawPosting` objects."""
    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    text = text.lstrip("﻿ \t\r\n")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        root = ElementTree.fromstring(_repair(text))

    postings: list[RawPosting] = []
    for item in [el for el in root.iter() if _strip_ns(el.tag) in {"item", "entry"}]:
        title = _first_text(item, "title")
        link = _extract_link(item)
        if not title or not link:
            continue
        postings.append(
            RawPosting(
                title=title,
                # Job feeds vary: some carry a proper company element, most
                # leave it to the source's own configuration.
                company=_first_text(item, "company", "author", "source") or company,
                location=_first_text(item, "location", "city", "region"),
                url=link,
                summary=_first_text(item, "description", "summary", "content", "encoded"),
                posted_raw=_first_text(
                    item, "pubdate", "published", "date", "updated", "created"
                ),
                guid=_first_text(item, "guid", "id"),
            )
        )
    return postings


@register("rss")
def fetch(
    source: Source,
    profile: Profile,
    settings: FetchSettings,
    session: requests.Session,
) -> list[RawPosting]:
    response = session.get(
        source.url,
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        },
        timeout=settings.timeout_seconds,
        allow_redirects=True,
    )
    if response.status_code >= 400:
        raise SourceError(f"HTTP {response.status_code}")
    try:
        postings = parse_feed(response.content, company=source.company or source.name)
    except ElementTree.ParseError as exc:
        raise SourceError(f"could not parse feed XML ({exc})") from exc
    if not postings:
        raise SourceError("feed parsed but contained no items")
    return postings
