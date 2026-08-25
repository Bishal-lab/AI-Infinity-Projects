"""Lever job boards.

    GET https://api.lever.co/v0/postings/<company>?mode=json

Public and key-free. Like Greenhouse, it returns the whole board at once, so
the adapter takes everything and lets the fit scorer decide.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import requests

from ..config import FetchSettings, Profile, Source
from ..model import RawPosting
from .base import SourceError, register, request_json

_API = "https://api.lever.co/v0/postings/{company}"


def company_slug(url: str) -> str:
    if "://" not in url:
        return url.strip("/")
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if not segments:
        raise SourceError(f"cannot tell the Lever company slug from {url!r}")
    if "postings" in segments:
        index = segments.index("postings")
        if index + 1 < len(segments):
            return segments[index + 1]
    return segments[0]


@register("lever")
def fetch(
    source: Source,
    profile: Profile,
    settings: FetchSettings,
    session: requests.Session,
) -> list[RawPosting]:
    payload = request_json(
        session,
        _API.format(company=company_slug(source.url)),
        settings,
        params={"mode": "json"},
    )
    if not isinstance(payload, list):
        raise SourceError("expected a JSON array from the Lever postings API")

    postings: list[RawPosting] = []
    for job in payload:
        if not isinstance(job, dict):
            continue
        title = str(job.get("text") or "").strip()
        url = str(job.get("hostedUrl") or job.get("applyUrl") or "").strip()
        if not title or not url:
            continue
        categories = job.get("categories") or {}
        postings.append(
            RawPosting(
                title=title,
                company=source.company or source.name,
                location=str(categories.get("location") or "").strip()
                if isinstance(categories, dict)
                else "",
                url=url,
                summary=str(job.get("descriptionPlain") or job.get("description") or ""),
                # Lever reports epoch milliseconds.
                posted_raw=_epoch_ms(job.get("createdAt")),
                guid=str(job.get("id") or ""),
            )
        )
    return postings


def _epoch_ms(value: object) -> str:
    """Epoch milliseconds to an ISO 8601 string the date parser understands."""
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""
