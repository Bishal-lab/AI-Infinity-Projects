"""Jooble's search API.

    POST https://<country>.jooble.org/api/<key>
    {"keywords": "vice president bancassurance", "location": "India"}

Free key from https://jooble.org/api/about. Jooble has usable Gulf and
South-East Asian coverage, which is why it is here alongside Careerjet.
"""

from __future__ import annotations

import os

import requests

from ..config import FetchSettings, Profile, Source
from ..model import RawPosting
from .base import SourceError, SourceSkipped, queries_for, register, request_json


@register("jooble")
def fetch(
    source: Source,
    profile: Profile,
    settings: FetchSettings,
    session: requests.Session,
) -> list[RawPosting]:
    key = (os.environ.get("JOOBLE_API_KEY") or "").strip()
    if not key:
        raise SourceSkipped("no JOOBLE_API_KEY set")

    endpoint = f"{source.url.rstrip('/')}/{key}"
    postings: list[RawPosting] = []
    seen: set[str] = set()

    for query in queries_for(source, profile):
        payload = request_json(
            session,
            endpoint,
            settings,
            method="POST",
            json_body={"keywords": query, "location": source.location, "page": "1"},
        )
        if not isinstance(payload, dict):
            raise SourceError("expected a JSON object from the Jooble API")

        for job in payload.get("jobs") or []:
            if not isinstance(job, dict):
                continue
            url = str(job.get("link") or "").strip()
            title = str(job.get("title") or "").strip()
            if not url or not title or url in seen:
                continue
            seen.add(url)
            postings.append(
                RawPosting(
                    title=title,
                    company=str(job.get("company") or "").strip(),
                    location=str(job.get("location") or source.location).strip(),
                    url=url,
                    summary=str(job.get("snippet") or ""),
                    posted_raw=str(job.get("updated") or ""),
                    guid=str(job.get("id") or url),
                )
            )
    return postings
