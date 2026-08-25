"""Careerjet's public search API.

    GET https://public.api.careerjet.net/search?locale_code=en_IN&keywords=…

Free, but it wants an affiliate id (CAREERJET_AFFID). This is the widest of the
three aggregators for this search: it indexes Indian and Gulf boards alike, so
it is the one worth setting up first.

Careerjet asks callers to pass the end user's IP and user agent. There is no
end user here — this is a scheduled job reading its own owner's search — so the
runner's own identity is sent rather than anything invented.
"""

from __future__ import annotations

import os

import requests

from ..config import FetchSettings, Profile, Source
from ..model import RawPosting
from .base import SourceError, SourceSkipped, queries_for, register, request_json

_PAGE_SIZE = 20


@register("careerjet")
def fetch(
    source: Source,
    profile: Profile,
    settings: FetchSettings,
    session: requests.Session,
) -> list[RawPosting]:
    affid = (os.environ.get("CAREERJET_AFFID") or "").strip()
    if not affid:
        raise SourceSkipped("no CAREERJET_AFFID set")

    postings: list[RawPosting] = []
    seen: set[str] = set()

    for query in queries_for(source, profile):
        payload = request_json(
            session,
            source.url,
            settings,
            params={
                "locale_code": source.locale or "en_IN",
                "keywords": query,
                "location": source.location,
                "affid": affid,
                "user_ip": "127.0.0.1",
                "user_agent": settings.user_agent,
                "pagesize": _PAGE_SIZE,
                "sort": "date",
            },
        )
        if not isinstance(payload, dict):
            raise SourceError("expected a JSON object from the Careerjet API")
        if str(payload.get("type", "")).upper() == "ERROR":
            raise SourceError(str(payload.get("error") or "Careerjet reported an error"))

        for job in payload.get("jobs") or []:
            if not isinstance(job, dict):
                continue
            url = str(job.get("url") or "").strip()
            title = str(job.get("title") or "").strip()
            if not url or not title or url in seen:
                continue
            seen.add(url)
            postings.append(
                RawPosting(
                    title=title,
                    company=str(job.get("company") or "").strip(),
                    location=str(job.get("locations") or source.location).strip(),
                    url=url,
                    summary=str(job.get("description") or ""),
                    posted_raw=str(job.get("date") or ""),
                    guid=url,
                )
            )
    return postings
