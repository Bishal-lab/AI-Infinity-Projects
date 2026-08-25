"""Saved searches — the part of the brief that does not depend on an API.

The boards carrying the most Indian and Gulf VP roles (iimjobs, Naukri, Bayt)
publish nothing key-free to read programmatically. Rather than pretend that gap
away, every digest ends with one-click, pre-filtered searches: the ones written
out in settings.yaml, plus a freshness-limited query per region built from the
profile's own search phrases.

The generated ones go through Google with a `site:` filter on purpose. A board
can rearrange its own search URLs — and several have — but a site query keeps
working, and `tbs=qdr:w` holds it to the past week.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from .config import Config, SavedSearch

#: Which boards are worth a generated query in which region.
_BOARDS = {
    "india": ("iimjobs.com", "naukri.com"),
    "gcc": ("bayt.com", "gulftalent.com"),
    "asia": ("efinancialcareers.com", "jobsdb.com"),
}

#: Kept short: a Google query with ten phrases in it returns nothing.
_MAX_PHRASES = 3


def google_site_search(site: str, phrases: list[str], region_title: str) -> SavedSearch:
    """A past-week Google query for one board."""
    alternation = " OR ".join(f'"{phrase}"' for phrase in phrases[:_MAX_PHRASES])
    query = f"site:{site} ({alternation})"
    return SavedSearch(
        label=f"{site} · {region_title} (past week)",
        url=f"https://www.google.com/search?q={quote_plus(query)}&tbs=qdr:w",
    )


def generated_searches(config: Config) -> list[SavedSearch]:
    """One query per board per region, built from profile.yaml's phrases."""
    profile = config.profile
    phrases = [phrase for phrase in profile.queries if phrase]
    if not phrases:
        return []

    searches: list[SavedSearch] = []
    for region in profile.regions:
        for site in _BOARDS.get(region.id, ()):
            searches.append(google_site_search(site, phrases, region.title))
    return searches


def all_searches(config: Config) -> list[SavedSearch]:
    """Everything to print at the foot of a digest, configured ones first."""
    seen: set[str] = set()
    out: list[SavedSearch] = []
    for search in list(config.saved_searches) + generated_searches(config):
        if search.url in seen:
            continue
        seen.add(search.url)
        out.append(search)
    return out
