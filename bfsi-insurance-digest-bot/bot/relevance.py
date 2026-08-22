"""Deciding whether a story belongs in the brief, and under which heading.

The rules live in ``config/topics.yaml``; this module is only the machinery that
applies them. It is deliberately transparent — every verdict carries the reason
and the keywords behind it, so `preview --explain` can show why a story got in
or was passed over, and the config can be tuned on evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .config import Config, Scoring, Section
from .model import Article

# A keyword matches on word boundaries only: "LIC" must not fire on "policy".
_PREFIX = r"(?<!\w)"
# ...but a trailing plural is still the same keyword: a feed writes "life
# insurers", the config says "life insurer".
_SUFFIX = r"(?:e?s)?(?!\w)"


def _keyword_pattern(keyword: str) -> str:
    """Escape a keyword, but keep whitespace, hyphens and plurals flexible.

    "non-par" then also matches "non par", "annuity" also matches "annuities",
    and "value of new business" survives a feed that used a non-breaking space.
    """
    text = keyword.strip()
    # "-y" -> "-ies": only the final word inflects.
    if len(text) > 3 and text.endswith("y") and text[-2].lower() not in "aeiou":
        stem, suffix = text[:-1], "(?:y|ies)"
    else:
        stem, suffix = text, ""
    escaped = re.escape(stem)
    escaped = re.sub(r"(\\?\s)+", r"[\\s\\u00a0]+", escaped)
    escaped = escaped.replace(r"\-", r"[-–—\s]")
    return escaped + suffix


_SPACES = re.compile("[\\s\u00a0]+")
_SEPARATORS = re.compile("[\\s\u00a0\u2010-\u2015-]+")


def _squash(text: str) -> str:
    """Letters only, singularised — for comparing two spellings of a keyword."""
    return _SEPARATORS.sub("", text).rstrip("s")


def _singular_forms(text: str) -> list[str]:
    """Candidate dictionary forms of a matched phrase, commonest first.

    Only the last word is inflected, so "life insurers" reduces to
    "life insurer" and "general insurance policies" to "...policy".
    """
    words = text.split(" ")
    last = words[-1]
    stems = [last]
    if last.endswith("ies") and len(last) > 4:
        stems.append(last[:-3] + "y")
    if last.endswith("es") and len(last) > 3:
        stems.append(last[:-2])
    if last.endswith("s") and len(last) > 2:
        stems.append(last[:-1])
    return [" ".join(words[:-1] + [stem]).strip() for stem in stems]


class KeywordMatcher:
    """Matches a list of keywords against text in one regex pass."""

    def __init__(self, keywords: Iterable[str]) -> None:
        # Longest first: regex alternation is leftmost-first, so without this
        # "SBI" would shadow "SBI Life".
        self.keywords: tuple[str, ...] = tuple(
            sorted({k.strip() for k in keywords if k and k.strip()}, key=len, reverse=True)
        )
        self._lookup = {k.lower(): k for k in self.keywords}
        if self.keywords:
            body = "|".join(_keyword_pattern(k) for k in self.keywords)
            self._regex: re.Pattern[str] | None = re.compile(
                f"{_PREFIX}(?:{body}){_SUFFIX}", re.IGNORECASE
            )
        else:
            self._regex = None

    def find(self, text: str) -> set[str]:
        """The set of configured keywords present in `text`."""
        if not self._regex or not text:
            return set()
        found: set[str] = set()
        for match in self._regex.finditer(text):
            normalised = _SPACES.sub(" ", match.group(0)).strip().lower()
            canonical = None
            # The regex admits plurals, so reduce the match to the dictionary
            # form before looking it up: "life insurers" -> "life insurer".
            for candidate in _singular_forms(normalised):
                canonical = self._lookup.get(candidate)
                if canonical:
                    break
            if canonical is None:
                # Matched through the flexible spacing or hyphen rule; compare
                # on letters alone so the hit still lands on a real keyword.
                squashed = _squash(normalised)
                canonical = next(
                    (k for k in self.keywords if _squash(k.lower()) == squashed), None
                )
            if canonical:
                found.add(canonical)
        return found

    def matches(self, text: str) -> bool:
        return bool(self._regex and text and self._regex.search(text))


@dataclass
class Verdict:
    """Why an article was kept or dropped."""

    accepted: bool
    score: float = 0.0
    section_id: str | None = None
    matched: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""


@dataclass
class _SectionMatcher:
    section: Section
    order: int
    strong: KeywordMatcher
    supporting: KeywordMatcher


class Scorer:
    """Applies the topic rules to articles."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.scoring: Scoring = config.scoring
        self._exclude = KeywordMatcher(config.exclude)
        self._gate = KeywordMatcher(config.domain_gate)
        self._sections = [
            _SectionMatcher(
                section=section,
                order=index,
                strong=KeywordMatcher(section.strong),
                supporting=KeywordMatcher(section.supporting),
            )
            for index, section in enumerate(config.sections)
        ]
        self._source_weight = {s.id: s.weight for s in config.sources}
        self._source_hint = {s.id: s.section_hint for s in config.sources}

    # ------------------------------------------------------------------ #

    def _contributions(
        self, matcher: KeywordMatcher, title: str, summary: str, weight: float
    ) -> list[tuple[str, float]]:
        """One entry per distinct keyword, worth more when it hit the headline."""
        in_title = matcher.find(title)
        in_summary = matcher.find(summary)
        out: list[tuple[str, float]] = []
        for keyword in in_title | in_summary:
            multiplier = self.scoring.title_multiplier if keyword in in_title else 1.0
            out.append((keyword, weight * multiplier))
        return out

    def score(self, article: Article) -> Verdict:
        title = article.title or ""
        summary = article.summary or ""
        haystack = f"{title}. {summary}"

        blocked = self._exclude.find(haystack)
        if blocked:
            return Verdict(False, reason=f"excluded on '{sorted(blocked)[0]}'")

        source_weight = self._source_weight.get(article.source_id, 0.0)
        hint = self._source_hint.get(article.source_id)
        best: tuple[float, int, str, tuple[str, ...]] | None = None
        matched_strong = False

        for entry in self._sections:
            strong_hits = self._contributions(
                entry.strong, title, summary, self.scoring.strong_weight
            )
            matched_strong = matched_strong or bool(strong_hits)
            contributions = strong_hits + self._contributions(
                entry.supporting, title, summary, self.scoring.supporting_weight
            )
            if not contributions and hint != entry.section.id:
                continue

            # Cap the number of counted keywords so a roundup that name-drops
            # every insurer cannot outrank a real regulatory circular.
            contributions.sort(key=lambda pair: pair[1], reverse=True)
            counted = contributions[: max(1, self.scoring.max_keywords_counted)]
            subtotal = sum(value for _, value in counted) * entry.section.multiplier
            if hint == entry.section.id:
                subtotal += self.scoring.hint_bonus

            keywords = tuple(keyword for keyword, _ in counted)
            candidate = (subtotal, -entry.order, entry.section.id, keywords)
            if best is None or candidate > best:
                best = candidate

        if best is None:
            return Verdict(False, reason="no topic keywords matched")

        # The domain gate. A strong keyword is itself proof the story is BFSI —
        # "HDFC Life" and "VNB margin" say so without using the word insurance.
        # The explicit gate list and the specialist-desk bypass are the two
        # other ways in; an article that matched nothing but supporting words is
        # what this keeps out.
        if (
            not matched_strong
            and source_weight < self.scoring.gate_bypass_weight
            and not self._gate.matches(haystack)
        ):
            return Verdict(False, reason="outside the BFSI domain")

        subtotal, _, section_id, keywords = best
        total = round(subtotal + source_weight, 3)
        if total < self.config.digest.min_score:
            return Verdict(
                False,
                score=total,
                section_id=section_id,
                matched=keywords,
                reason=f"scored {total:g}, below the {self.config.digest.min_score:g} threshold",
            )
        return Verdict(
            True,
            score=total,
            section_id=section_id,
            matched=keywords,
            reason=f"{section_id} @ {total:g}",
        )

    def apply(self, articles: Sequence[Article]) -> tuple[list[Article], list[tuple[Article, Verdict]]]:
        """Split articles into accepted (scored in place) and rejected+reason."""
        accepted: list[Article] = []
        rejected: list[tuple[Article, Verdict]] = []
        for article in articles:
            verdict = self.score(article)
            if verdict.accepted:
                article.score = verdict.score
                article.section_id = verdict.section_id
                article.matched = verdict.matched
                accepted.append(article)
            else:
                rejected.append((article, verdict))
        return accepted, rejected
