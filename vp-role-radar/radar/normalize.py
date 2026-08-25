"""Turning raw postings into clean, comparable `Opening` objects.

Job sources are inconsistent in every dimension that matters: dates arrive in
three formats or not at all, descriptions come as HTML fragments, the same
opening is listed under four URLs differing only in tracking parameters, and
the experience requirement is prose. Everything downstream assumes this module
has already dealt with that.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import Source
from .model import Opening, RawPosting

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")

# Campaign and session parameters. Two URLs differing only by these are the
# same opening, and keeping them makes the seen-store leak duplicates.
_JUNK_PARAM_PREFIXES = ("utm_", "pk_", "mc_", "hsa_", "ito_")
_JUNK_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "ref", "referrer",
    "cmpid", "campaign_id", "source", "src", "from", "feature", "share",
    "trk", "trackingid", "position", "pagenum", "refid", "sid", "jobid_alias",
}

# Dates that neither RFC 822 nor ISO 8601 covers.
_EXTRA_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d %b %Y",
    "%d %B %Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%b %d, %Y",
    "%B %d, %Y",
)

# Workday and several boards report a posting age in words rather than a date.
_RELATIVE_DATE = re.compile(
    r"""(?ix)
    (?:posted \s+ )?
    (?:
        (?P<today>today|just \s+ posted)
      | (?P<yesterday>yesterday)
      | (?P<count>\d+) \+? \s* (?P<unit>day|days|week|weeks|month|months|hour|hours)
    )
    """
)

# "8-12 years", "10 to 15 yrs", "15+ years", "minimum 12 years of experience".
_BAND = re.compile(
    r"(?i)\b(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*(?:yrs?|years?)\b"
)
_OPEN_ENDED = re.compile(
    r"(?i)\b(?:minimum|min\.?|at\s+least|over|above)?\s*(\d{1,2})\s*\+\s*(?:yrs?|years?)\b"
)
_AT_LEAST = re.compile(
    r"(?i)\b(?:minimum|min\.?|at\s+least|over|more\s+than)\s+(\d{1,2})\s*(?:yrs?|years?)\b"
)


def strip_html(value: str) -> str:
    """Flatten an HTML fragment to a single line of readable text."""
    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", " ", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = _WHITESPACE.sub(" ", text)
    # Dropping a tag between a word and its punctuation leaves "accounts .";
    # descriptions are quoted verbatim in the e-mail, so close that gap.
    return re.sub(r"\s+([,.;:!?])", r"\1", text).strip()


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


def _to_utc(value: datetime) -> datetime:
    """A naive timestamp is treated as UTC — a source that omits an offset is
    almost always publishing UTC, and guessing IST would shift a posting a day."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_date(value: str, now: datetime | None = None) -> datetime | None:
    """Parse a posting date into an aware UTC datetime, or None if unreadable.

    Handles the absolute formats plus the relative phrasing job boards favour
    ("Posted 3 days ago", "Posted Today"), because on several of them that is
    the only date there is.
    """
    text = (value or "").strip()
    if not text:
        return None

    # RFC 822 / RFC 2822 — the RSS default.
    if "," in text or text[:3].isalpha():
        try:
            parsed = parsedate_to_datetime(text)
            if parsed is not None:
                return _to_utc(parsed)
        except (TypeError, ValueError, IndexError):
            pass

    # ISO 8601 — what most job APIs return.
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

    match = _RELATIVE_DATE.search(text)
    if match:
        reference = now or datetime.now(timezone.utc)
        if match.group("today"):
            return reference
        if match.group("yesterday"):
            return reference - timedelta(days=1)
        count = int(match.group("count"))
        unit = (match.group("unit") or "").lower()
        if unit.startswith("hour"):
            return reference - timedelta(hours=count)
        if unit.startswith("day"):
            return reference - timedelta(days=count)
        if unit.startswith("week"):
            return reference - timedelta(weeks=count)
        if unit.startswith("month"):
            return reference - timedelta(days=30 * count)
    return None


def parse_experience(text: str) -> tuple[int | None, int | None]:
    """Read an advertised experience requirement.

    Returns (minimum, maximum), either of which may be None. A closed band
    ("8-12 years") gives both; "15+ years" gives a minimum only. The maximum is
    what the fit gate uses, because a posting capped well below the candidate's
    23 years is a different job however senior its title sounds.
    """
    if not text:
        return None, None

    band = _BAND.search(text)
    if band:
        low, high = int(band.group(1)), int(band.group(2))
        if low > high:
            low, high = high, low
        return low, high

    for pattern in (_OPEN_ENDED, _AT_LEAST):
        match = pattern.search(text)
        if match:
            return int(match.group(1)), None
    return None, None


def canonical_url(url: str) -> str:
    """Strip tracking noise so the same opening compares equal across boards."""
    text = (url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if not parts.scheme or not parts.netloc:
        return text

    # "www." is deliberately kept. This URL is what a reader clicks, and some
    # hosts answer only on the www name; collapsing the two is a dedupe
    # concern, and dedupe.url_key handles it there.
    host = parts.netloc.lower()
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


def clean_location(value: str) -> str:
    """Tidy the location string boards report.

    Workday returns "Gurugram, India" but also "2 Locations" and long
    semicolon-joined lists; the first named place is the useful one.
    """
    text = strip_html(value)
    if not text:
        return ""
    if re.fullmatch(r"(?i)\d+\s+locations?", text):
        return ""
    parts = [part.strip() for part in text.split(";") if part.strip()]
    if len(parts) > 1:
        return truncate(parts[0] + f" (+{len(parts) - 1} more)", 80)
    return truncate(text, 80)


def to_opening(
    entry: RawPosting,
    source: Source,
    summary_limit: int = 400,
    now: datetime | None = None,
) -> Opening | None:
    """Build an `Opening`, or None when the posting lacks a title or a link."""
    title = truncate(strip_html(entry.title), 160)
    url = canonical_url(entry.url)
    if not title or not url:
        return None

    summary = strip_html(entry.summary)
    # The experience band is read from the description as well as any explicit
    # field, because on most boards it is only ever stated in prose.
    experience_source = " ".join(filter(None, (entry.experience_raw, title, summary)))
    experience_min, experience_max = parse_experience(experience_source)

    return Opening(
        source_id=source.id,
        source_name=source.name,
        title=title,
        url=url,
        company=truncate(strip_html(entry.company) or source.company, 80),
        location=clean_location(entry.location),
        summary=truncate(summary, summary_limit),
        posted=parse_date(entry.posted_raw, now=now),
        guid=(entry.guid or "").strip(),
        experience_raw=truncate(strip_html(entry.experience_raw), 60),
        experience_min=experience_min,
        experience_max=experience_max,
    )
