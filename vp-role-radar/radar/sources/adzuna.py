"""Adzuna's search API.

    GET https://api.adzuna.com/v1/api/jobs/in/search/1?app_id=…&app_key=…

Free tier from https://developer.adzuna.com/. Strong in India; it has no Gulf
country endpoint, so it is the third of the three aggregators rather than the
first, and `max_days_old` is set from the radar's own lookback window.
"""

from __future__ import annotations

import os

import requests

from ..config import FetchSettings, Profile, Source
from ..model import RawPosting
from .base import SourceError, SourceSkipped, queries_for, register, request_json

_PAGE_SIZE = 20


@register("adzuna")
def fetch(
    source: Source,
    profile: Profile,
    settings: FetchSettings,
    session: requests.Session,
) -> list[RawPosting]:
    app_id = (os.environ.get("ADZUNA_APP_ID") or "").strip()
    app_key = (os.environ.get("ADZUNA_APP_KEY") or "").strip()
    if not app_id or not app_key:
        missing = " and ".join(
            name for name, value in (("ADZUNA_APP_ID", app_id), ("ADZUNA_APP_KEY", app_key))
            if not value
        )
        raise SourceSkipped(f"no {missing} set")

    postings: list[RawPosting] = []
    seen: set[str] = set()

    for query in queries_for(source, profile):
        payload = request_json(
            session,
            source.url,
            settings,
            params={
                "app_id": app_id,
                "app_key": app_key,
                "results_per_page": _PAGE_SIZE,
                "what": query,
                "where": source.location,
                "max_days_old": 14,
                "sort_by": "date",
                "content-type": "application/json",
            },
        )
        if not isinstance(payload, dict):
            raise SourceError("expected a JSON object from the Adzuna API")

        for job in payload.get("results") or []:
            if not isinstance(job, dict):
                continue
            url = str(job.get("redirect_url") or "").strip()
            title = str(job.get("title") or "").strip()
            if not url or not title or url in seen:
                continue
            seen.add(url)
            company = job.get("company") or {}
            location = job.get("location") or {}
            postings.append(
                RawPosting(
                    title=title,
                    company=str(company.get("display_name") or "").strip()
                    if isinstance(company, dict) else "",
                    location=str(location.get("display_name") or source.location).strip()
                    if isinstance(location, dict) else source.location,
                    url=url,
                    summary=str(job.get("description") or ""),
                    posted_raw=str(job.get("created") or ""),
                    guid=str(job.get("id") or url),
                )
            )
    return postings
