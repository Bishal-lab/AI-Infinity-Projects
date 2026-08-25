"""Greenhouse job boards.

    GET https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true

Public, key-free, and returns the whole board in one call — so this adapter
does not search at all: it takes everything and lets the fit scorer decide,
which is more reliable than guessing which words the employer used.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import requests

from ..config import FetchSettings, Profile, Source
from ..model import RawPosting
from .base import SourceError, register, request_json

_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def board_token(url: str) -> str:
    """The board token from any Greenhouse URL, or the token itself."""
    if "://" not in url:
        return url.strip("/")
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if not segments:
        raise SourceError(f"cannot tell the Greenhouse board token from {url!r}")
    # https://boards.greenhouse.io/<token>  and  .../v1/boards/<token>/jobs
    if "boards" in segments:
        index = segments.index("boards")
        if index + 1 < len(segments):
            return segments[index + 1]
    return segments[-1]


@register("greenhouse")
def fetch(
    source: Source,
    profile: Profile,
    settings: FetchSettings,
    session: requests.Session,
) -> list[RawPosting]:
    payload = request_json(
        session,
        _API.format(token=board_token(source.url)),
        settings,
        params={"content": "true"},
    )
    if not isinstance(payload, dict):
        raise SourceError("expected a JSON object from the Greenhouse board API")

    postings: list[RawPosting] = []
    for job in payload.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        title = str(job.get("title") or "").strip()
        url = str(job.get("absolute_url") or "").strip()
        if not title or not url:
            continue
        location = job.get("location") or {}
        postings.append(
            RawPosting(
                title=title,
                company=source.company or source.name,
                location=str(location.get("name") or "").strip()
                if isinstance(location, dict)
                else str(location),
                url=url,
                # Greenhouse returns the description as escaped HTML; the
                # normaliser flattens it.
                summary=str(job.get("content") or ""),
                posted_raw=str(job.get("updated_at") or job.get("first_published") or ""),
                guid=str(job.get("id") or ""),
            )
        )
    return postings
