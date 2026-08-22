"""Collapsing the same story reported by several sources.

A wire story reaches the digest three or four times: once from the specialist
desk, once from a general business feed, and once or twice through a search
feed. All three carry different URLs, so URL equality alone is not enough — the
headlines have to be compared as well.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable, Sequence

from .model import Article

# Words that carry no signal when comparing two headlines about the same event.
_STOPWORDS = frozenset(
    """
    a an the and or of to in on for at by with from as is are was were be been
    it its this that these those new says say said after over into amid ahead
    up down more most may might will would can could should percent pc cent
    """.split()
)
_NON_WORD = re.compile(r"[^\w\s]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

#: Two headlines sharing at least this fraction of their stemmed, meaningful
#: words are treated as the same story. Tuned against real rewrites: "IRDAI
#: eases surrender value norms for life insurers" and "IRDAI relaxes surrender
#: value norms for life insurance companies" overlap 0.67 and must merge, while
#: two different events on the same subject sit well below this.
DEFAULT_THRESHOLD = 0.62

#: Reporting-period markers. Two headlines that name different periods are about
#: different events however similar their wording — a Q1 results story and a Q2
#: results story on the same insurer are otherwise near-identical.
_PERIOD = re.compile(r"^(?:q[1-4]|h[12]|fy\d{2,4}|cy\d{2,4}|\d{4})$")


def normalise_title(title: str) -> str:
    text = _NON_WORD.sub(" ", (title or "").lower())
    return _WHITESPACE.sub(" ", text).strip()


#: Suffixes stripped when comparing headlines, longest first. This is a crude
#: stemmer on purpose: it only has to make "life insurers" and "life insurance
#: companies" look alike, which is exactly how two desks write up one story.
_SUFFIXES = ("ations", "ation", "ements", "ement", "ances", "ance", "ings",
             "ing", "ies", "ers", "ed", "er", "es", "s")
_MIN_STEM = 4


def stem(word: str) -> str:
    """Reduce a word to a comparison stem, leaving short words alone."""
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)]
    return word


def title_tokens(title: str) -> frozenset[str]:
    """Meaningful lowercase words in a headline, numbers kept (they matter:
    'Q1' and 'Q2' are what separate two otherwise identical results stories)."""
    words = normalise_title(title).split()
    return frozenset(
        stem(w)
        for w in words
        # Short tokens are noise unless they carry a digit, where they are
        # usually the very thing that distinguishes two stories ("Q1", "5G").
        if (len(w) > 2 or any(ch.isdigit() for ch in w)) and w not in _STOPWORDS
    )


def period_markers(title: str) -> frozenset[str]:
    """Reporting periods named in a headline, e.g. {"q1", "fy26"}."""
    return frozenset(w for w in title_tokens(title) if _PERIOD.match(w))


def same_story(left: str, right: str, threshold: float = None) -> bool:
    """Whether two headlines describe the same event."""
    if threshold is None:
        threshold = DEFAULT_THRESHOLD
    left_periods, right_periods = period_markers(left), period_markers(right)
    if left_periods and right_periods and not (left_periods & right_periods):
        return False
    return similarity(left, right) >= threshold


def similarity(left: str, right: str) -> float:
    """Jaccard overlap of two headlines' meaningful words, 0.0 to 1.0."""
    a, b = title_tokens(left), title_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def url_key(article: Article) -> str:
    return "u:" + _hash(article.url.lower())


def title_key(article: Article) -> str:
    return "t:" + _hash(normalise_title(article.title))


def article_keys(article: Article) -> tuple[str, str]:
    """Both identities of a story.

    The seen-store checks both, because the same story can arrive under a
    different URL tomorrow (via a search feed) but keeps its headline.
    """
    return url_key(article), title_key(article)


def _rank(article: Article) -> tuple[float, float]:
    """Which of two duplicates to keep: higher score wins, then the newer one."""
    timestamp = article.published.timestamp() if article.published else 0.0
    return (article.score, timestamp)


def dedupe(
    articles: Sequence[Article], threshold: float = DEFAULT_THRESHOLD
) -> list[Article]:
    """Return one article per story, best copy kept, in the input order.

    The survivor records how many copies it absorbed in `duplicate_count`,
    which the renderers use to show corroboration ("+2 more").
    """
    kept: list[Article] = []
    seen_urls: dict[str, int] = {}
    seen_titles: dict[str, int] = {}

    for article in articles:
        article.dedupe_key = url_key(article)
        index = seen_urls.get(url_key(article))
        if index is None:
            index = seen_titles.get(title_key(article))
        if index is None:
            if title_tokens(article.title):
                for position, candidate in enumerate(kept):
                    if same_story(article.title, candidate.title, threshold):
                        index = position
                        break

        if index is None:
            seen_urls[url_key(article)] = len(kept)
            seen_titles[title_key(article)] = len(kept)
            kept.append(article)
            continue

        incumbent = kept[index]
        merged_count = incumbent.duplicate_count + 1
        if _rank(article) > _rank(incumbent):
            article.duplicate_count = merged_count
            kept[index] = article
            seen_urls[url_key(article)] = index
            seen_titles[title_key(article)] = index
        else:
            incumbent.duplicate_count = merged_count

    return kept
