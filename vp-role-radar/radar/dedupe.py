"""Collapsing one opening listed in several places.

A single VP role reaches the radar more than once: from the insurer's own
careers site, from an aggregator that indexed it, and again from a second
aggregator. All of them carry different URLs, so URL equality alone is not
enough — the employer and the title have to be compared as well.

The comparison is deliberately stricter than the news digest's next door. Two
articles about one event are written in different words and should merge on
loose overlap; two openings at the same insurer are often genuinely different
jobs with nearly identical titles ("VP - Key Accounts, West" and "VP - Key
Accounts, South"), and merging those would hide one of them.
"""

from __future__ import annotations

import hashlib
import re
from typing import Sequence

from .model import Opening

_NON_WORD = re.compile(r"[^\w\s]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

# Words that say nothing about which job this is.
_STOPWORDS = frozenset(
    """
    a an the and or of to in for at by with from as is are on
    role position opening opportunity job vacancy hiring urgent immediate
    required wanted new exciting leading reputed
    """.split()
)

# Grade synonyms, flattened so that "AVP" and "Assistant Vice President" — the
# same job advertised twice — compare equal.
_SYNONYMS = {
    "avp": "assistantvicepresident",
    "assistant vice president": "assistantvicepresident",
    "associate vice president": "assistantvicepresident",
    "dvp": "deputyvicepresident",
    "deputy vice president": "deputyvicepresident",
    "svp": "seniorvicepresident",
    "senior vice president": "seniorvicepresident",
    "evp": "executivevicepresident",
    "executive vice president": "executivevicepresident",
    "vp": "vicepresident",
    "vice president": "vicepresident",
    "banca": "bancassurance",
    "kam": "keyaccountmanagement",
    "key account management": "keyaccountmanagement",
    "key accounts": "keyaccountmanagement",
    "key account": "keyaccountmanagement",
}

#: Two titles at the same employer sharing at least this fraction of their
#: meaningful words are the same opening. High on purpose: see the module
#: docstring on why regional variants of one title must not merge.
DEFAULT_THRESHOLD = 0.85

#: Markers that make two otherwise identical titles different jobs. A title
#: naming one of these merges only with a title naming the same one.
_DISCRIMINATORS = frozenset(
    """
    north south east west central northern southern eastern western
    mumbai delhi ncr gurgaon gurugram bangalore bengaluru hyderabad chennai
    pune kolkata dubai abudhabi riyadh doha singapore hongkong
    """.split()
)


def normalise_title(title: str) -> str:
    """Lowercase, punctuation-free, with grade synonyms flattened."""
    text = (title or "").lower()
    # Longest first, so "senior vice president" is not eaten by "vice president".
    for phrase in sorted(_SYNONYMS, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(phrase)}\b", _SYNONYMS[phrase], text)
    text = _NON_WORD.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def normalise_company(company: str) -> str:
    """Drop the corporate suffixes that differ between two listings of one job."""
    text = _NON_WORD.sub(" ", (company or "").lower())
    words = [
        word
        for word in text.split()
        if word not in {"ltd", "limited", "pvt", "private", "plc", "inc", "llc",
                        "co", "company", "group", "holdings", "india", "insurance"}
    ]
    return " ".join(words).strip() or text.strip()


def title_tokens(title: str) -> frozenset[str]:
    """Meaningful words in a job title."""
    words = normalise_title(title).split()
    return frozenset(
        word for word in words if len(word) > 2 and word not in _STOPWORDS
    )


def discriminators(title: str) -> frozenset[str]:
    """Region or city markers that separate two variants of one title."""
    return frozenset(word for word in title_tokens(title) if word in _DISCRIMINATORS)


def similarity(left: str, right: str) -> float:
    """Jaccard overlap of two titles' meaningful words, 0.0 to 1.0."""
    a, b = title_tokens(left), title_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def same_opening(left: Opening, right: Opening, threshold: float = DEFAULT_THRESHOLD) -> bool:
    """Whether two postings are the same job.

    Requires the same employer: two insurers advertising an identically worded
    VP role are two openings, and the radar must show both.
    """
    left_company = normalise_company(left.company)
    right_company = normalise_company(right.company)
    if not left_company or not right_company or left_company != right_company:
        return False

    left_marks, right_marks = discriminators(left.title), discriminators(right.title)
    if left_marks != right_marks:
        return False

    return similarity(left.title, right.title) >= threshold


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def url_key(opening: Opening) -> str:
    """The URL as an identity: case-folded, and with "www." collapsed so that
    two listings of one job on the same host compare equal."""
    url = opening.url.lower()
    return "u:" + _hash(url.replace("://www.", "://", 1))


def role_key(opening: Opening) -> str:
    """Employer plus title plus any regional marker — the identity of the job
    itself, independent of which board it was found on."""
    return "r:" + _hash(
        f"{normalise_company(opening.company)}|{normalise_title(opening.title)}"
    )


def opening_keys(opening: Opening) -> tuple[str, str]:
    """Both identities of an opening.

    The seen-store checks both, because the same job reappears next week under
    a different aggregator URL but keeps its employer and title.
    """
    return url_key(opening), role_key(opening)


def _rank(opening: Opening) -> tuple[float, float]:
    """Which of two duplicates to keep: better fit wins, then the newer one."""
    timestamp = opening.posted.timestamp() if opening.posted else 0.0
    return (opening.fit, timestamp)


def dedupe(
    openings: Sequence[Opening], threshold: float = DEFAULT_THRESHOLD
) -> list[Opening]:
    """Return one opening per job, best copy kept, in the input order.

    The survivor records how many copies it absorbed in `duplicate_count`, which
    the renderers show as corroboration ("also on 2 other boards").
    """
    kept: list[Opening] = []
    seen_urls: dict[str, int] = {}
    seen_roles: dict[str, int] = {}

    for opening in openings:
        opening.dedupe_key = role_key(opening)
        index = seen_urls.get(url_key(opening))
        if index is None:
            index = seen_roles.get(role_key(opening))
        if index is None:
            for position, candidate in enumerate(kept):
                if same_opening(opening, candidate, threshold):
                    index = position
                    break

        if index is None:
            seen_urls[url_key(opening)] = len(kept)
            seen_roles[role_key(opening)] = len(kept)
            kept.append(opening)
            continue

        incumbent = kept[index]
        merged_count = incumbent.duplicate_count + 1
        if _rank(opening) > _rank(incumbent):
            opening.duplicate_count = merged_count
            kept[index] = opening
            seen_urls[url_key(opening)] = index
            seen_roles[role_key(opening)] = index
        else:
            incumbent.duplicate_count = merged_count

    return kept
