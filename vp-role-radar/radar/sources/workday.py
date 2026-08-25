"""Workday — the career site most large insurers run.

Workday serves a public JSON search endpoint that needs no key and no login:

    POST https://<host>/wday/cxs/<tenant>/<site>/jobs
    {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "key account"}

The awkward part is knowing <tenant> and <site>. Rather than ask for them, this
adapter derives both from the careers URL as a person copies it out of their
browser — see the note in config/sources.yaml.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import requests

from ..config import FetchSettings, Profile, Source
from ..model import RawPosting
from .base import SourceError, queries_for, register, request_json

#: "en-US", "en-GB", "zh-CN" — a locale segment, not the site name.
_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2})?$")

_PAGE_SIZE = 20


def endpoint(url: str) -> tuple[str, str, str, str]:
    """(json endpoint, host, site, locale) from a Workday careers URL.

    >>> endpoint("https://aia.wd3.myworkdayjobs.com/en-US/AIACareers")[0]
    'https://aia.wd3.myworkdayjobs.com/wday/cxs/aia/AIACareers/jobs'
    """
    parts = urlsplit(url)
    host = parts.netloc
    if not host:
        raise SourceError(f"not a URL: {url!r}")

    tenant = host.split(".")[0]
    segments = [segment for segment in parts.path.split("/") if segment]
    # An already-derived CXS URL is accepted too, so pasting either form works.
    if "cxs" in segments:
        site = segments[-1] if segments[-1] != "jobs" else segments[-2]
        return url.rstrip("/"), host, site, "en-US"

    locale = "en-US"
    if segments and _LOCALE.match(segments[0]):
        locale = segments.pop(0)
    if not segments:
        raise SourceError(
            f"cannot tell the Workday site name from {url!r} — it should look like "
            f"https://<tenant>.wd3.myworkdayjobs.com/en-US/<SiteName>"
        )
    site = segments[0]
    return f"https://{host}/wday/cxs/{tenant}/{site}/jobs", host, site, locale


@register("workday")
def fetch(
    source: Source,
    profile: Profile,
    settings: FetchSettings,
    session: requests.Session,
) -> list[RawPosting]:
    api, host, site, locale = endpoint(source.url)
    postings: list[RawPosting] = []
    seen: set[str] = set()

    for query in queries_for(source, profile):
        payload = request_json(
            session,
            api,
            settings,
            method="POST",
            json_body={
                "appliedFacets": {},
                "limit": _PAGE_SIZE,
                "offset": 0,
                "searchText": query,
            },
        )
        if not isinstance(payload, dict):
            raise SourceError("expected a JSON object from the Workday search endpoint")

        for job in payload.get("jobPostings") or []:
            if not isinstance(job, dict):
                continue
            path = str(job.get("externalPath") or "").strip()
            title = str(job.get("title") or "").strip()
            if not path or not title or path in seen:
                continue
            seen.add(path)

            # bulletFields carries the requisition id, and on many tenants the
            # posting date as well; neither is reliably one or the other, so
            # both are handed to the date parser and whichever reads as a date
            # wins.
            bullets = [str(b) for b in (job.get("bulletFields") or []) if b]
            postings.append(
                RawPosting(
                    title=title,
                    company=source.company or source.name,
                    location=str(job.get("locationsText") or "").strip(),
                    url=f"https://{host}/{locale}/{site}{path}",
                    summary="",
                    posted_raw=str(job.get("postedOn") or "").strip() or " ".join(bullets),
                    guid=path,
                )
            )
    return postings
