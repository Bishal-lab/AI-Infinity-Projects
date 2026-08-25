"""Scoring an opening against the candidate profile.

The shape follows the digest bot's `relevance.py` next door — a weighted
keyword matcher, hits in the headline worth more than hits in the body, hard
exclusions applied first — but the question is different. That module asks "is
this story about BFSI?"; this one asks "is this job worth this person's
morning?", and answers with a 0-100 score, a tier, and the reasons behind both.

Three of the five dimensions are also gates. An opening with no VP-grade marker
in its title, or naming no key-account-shaped function anywhere, or sitting
outside the three target regions, is dropped rather than scored low: those are
not weak matches, they are different jobs. Everything that survives is
something a reader would recognise as belonging in the brief.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Profile, Region, SeniorityLevel
from .model import Opening

#: How much matched keyword weight saturates a dimension. Reaching it scores
#: full marks; the curve below it is linear. Tuned so that a posting naming two
#: function keywords in its title is already at the top of that dimension —
#: past that, more keywords say more about the writer than about the job.
_SATURATION = {"function": 3.0, "domain": 2.0, "edge": 3.0}

#: A supporting keyword's worth relative to a strong one.
_SUPPORTING_VALUE = 0.35


def _pattern(keyword: str) -> str:
    """A word-boundary regex for one keyword, tolerant of odd whitespace.

    `\\b` is only meaningful next to a word character, so a keyword that starts
    or ends with punctuation ("P&L") gets the boundary dropped on that side —
    without this, `\\bP&L\\b` would still work but `\\b&co\\b` would not, and the
    rule is easier to keep than to remember.
    """
    escaped = re.escape(keyword.strip())
    escaped = re.sub(r"(?:\\[ ])+", r"\\s+", escaped)
    left = r"\b" if keyword[:1].isalnum() else ""
    right = r"\b" if keyword[-1:].isalnum() else ""
    return f"{left}{escaped}{right}"


class KeywordMatcher:
    """Finds which of a list of keywords appear in a text.

    One compiled alternation rather than one regex per keyword: the radar
    matches a few hundred keywords against every opening from every source, and
    the difference shows on a runner with two cores.
    """

    def __init__(self, keywords: tuple[str, ...] | list[str]) -> None:
        self.keywords = tuple(dict.fromkeys(k for k in keywords if k and k.strip()))
        self._lookup = {k.lower(): k for k in self.keywords}
        if self.keywords:
            # Longest first, so "senior vice president" wins over "vice president".
            ordered = sorted(self.keywords, key=len, reverse=True)
            self._regex: re.Pattern[str] | None = re.compile(
                "|".join(_pattern(k) for k in ordered), re.IGNORECASE
            )
        else:
            self._regex = None

    def __bool__(self) -> bool:
        return bool(self.keywords)

    def matches(self, text: str) -> list[str]:
        """Distinct keywords found in `text`, in the order they were configured."""
        if not self._regex or not text:
            return []
        found: set[str] = set()
        for hit in self._regex.finditer(text):
            phrase = re.sub(r"\s+", " ", hit.group(0)).strip().lower()
            canonical = self._lookup.get(phrase)
            if canonical is None:
                # Whitespace-flexible matches can differ from the configured
                # spelling; fall back to the first keyword that reads the same.
                for key, value in self._lookup.items():
                    if key.replace(" ", "") == phrase.replace(" ", ""):
                        canonical = value
                        break
            if canonical:
                found.add(canonical)
        return [k for k in self.keywords if k in found]


@dataclass
class Verdict:
    """Why an opening is in the brief, or why it is not."""

    accepted: bool
    fit: float = 0.0
    tier: str = ""
    seniority: str = ""
    region: str = ""
    region_title: str = ""
    matched: tuple[str, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)
    #: Set when `accepted` is false — one plain sentence, shown by `preview
    #: --explain` so a surprising omission can be understood without a debugger.
    reason: str = ""


@dataclass
class _RegionMatch:
    region: Region
    place: str
    preferred: bool


class Scorer:
    """Scores openings against one profile. Build once, reuse for every run."""

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self._exclude_title = KeywordMatcher(profile.exclude_title)
        self._exclude_anywhere = KeywordMatcher(profile.exclude_anywhere)
        self._levels: list[tuple[SeniorityLevel, KeywordMatcher]] = [
            (level, KeywordMatcher(level.keywords)) for level in profile.seniority_levels
        ]
        self._function_strong = KeywordMatcher(profile.function_strong)
        self._function_supporting = KeywordMatcher(profile.function_supporting)
        self._domain_strong = KeywordMatcher(profile.domain_strong)
        self._domain_adjacent = KeywordMatcher(profile.domain_adjacent)
        self._edge = KeywordMatcher(profile.edge_keywords)
        self._regions: list[tuple[Region, KeywordMatcher, KeywordMatcher]] = [
            (
                region,
                KeywordMatcher(region.preferred_cities),
                KeywordMatcher(region.countries + region.cities),
            )
            for region in profile.regions
        ]

    # ------------------------------------------------------------------ #
    # Dimensions
    # ------------------------------------------------------------------ #

    def _weighted(
        self,
        strong: KeywordMatcher,
        supporting: KeywordMatcher,
        title: str,
        body: str,
        saturation: float,
        supporting_value: float = _SUPPORTING_VALUE,
    ) -> tuple[float, list[str]]:
        """Score one keyword dimension, 0..1, with the keywords that earned it."""
        multiplier = self.profile.title_multiplier
        total = 0.0
        matched: list[str] = []

        for matcher, value in ((strong, 1.0), (supporting, supporting_value)):
            if not matcher:
                continue
            in_title = set(matcher.matches(title))
            for keyword in matcher.matches(f"{title} {body}"):
                if len(matched) >= self.profile.max_keywords_counted:
                    break
                total += value * (multiplier if keyword in in_title else 1.0)
                matched.append(keyword)

        return min(1.0, total / saturation if saturation > 0 else 0.0), matched

    def locate(self, text: str) -> _RegionMatch | None:
        """Which target region a location string belongs to, if any.

        A preferred city outranks a bare country name, so "Gurgaon, India"
        resolves to Delhi-NCR rather than to India-at-large, and a posting that
        names both a Gulf city and the employer's Indian head office resolves to
        the city the job is actually in.
        """
        if not text:
            return None
        best: _RegionMatch | None = None
        for region, preferred, ordinary in self._regions:
            hits = preferred.matches(text)
            if hits:
                return _RegionMatch(region=region, place=hits[0], preferred=True)
            if best is None:
                other = ordinary.matches(text)
                if other:
                    best = _RegionMatch(region=region, place=other[0], preferred=False)
        return best

    # ------------------------------------------------------------------ #
    # The verdict
    # ------------------------------------------------------------------ #

    def score(self, opening: Opening) -> Verdict:
        profile = self.profile
        title = opening.title
        body = " ".join(filter(None, (opening.company, opening.summary)))
        haystack = f"{title} {body}"

        # 1. Exclusions ------------------------------------------------- #
        excluded = self._exclude_title.matches(title)
        if excluded:
            return Verdict(
                accepted=False,
                reason=f"title names {excluded[0]!r}, which is a different function",
            )
        excluded = self._exclude_anywhere.matches(haystack)
        if excluded:
            return Verdict(accepted=False, reason=f"posting names {excluded[0]!r}")

        # 2. Seniority gate --------------------------------------------- #
        # The longest matching marker wins, across all levels rather than the
        # first level that matches at all. "Assistant Vice President" contains
        # "Vice President", so scanning in configured order would grade every
        # AVP role as a VP one.
        level: SeniorityLevel | None = None
        level_hit = ""
        for candidate, matcher in self._levels:
            for hit in matcher.matches(title):
                if len(hit) > len(level_hit):
                    level, level_hit = candidate, hit
        if level is None:
            return Verdict(
                accepted=False, reason="no VP-grade or AVP-grade marker in the title"
            )

        # 3. Function gate ---------------------------------------------- #
        function_hits = self._function_strong.matches(haystack)
        if not function_hits:
            return Verdict(
                accepted=False,
                reason="nothing in it names key accounts, bancassurance, alliances "
                "or an equivalent function",
            )

        # 4. Geography gate --------------------------------------------- #
        where = self.locate(opening.location) or self.locate(haystack)
        if where is None:
            shown = opening.location or "no location given"
            return Verdict(
                accepted=False,
                reason=f"location ({shown}) is outside India, the GCC and Asia",
            )

        # 5. Experience gate -------------------------------------------- #
        if (
            opening.experience_max is not None
            and opening.experience_max < profile.experience_floor
        ):
            return Verdict(
                accepted=False,
                reason=f"advertises {opening.experience_band}, below the "
                f"{profile.experience_floor}-year floor",
            )

        # 6. Score ------------------------------------------------------- #
        seniority_value = level.value
        function_value, function_matched = self._weighted(
            self._function_strong, self._function_supporting, title, body,
            _SATURATION["function"],
        )
        domain_value, domain_matched = self._weighted(
            self._domain_strong, self._domain_adjacent, title, body,
            _SATURATION["domain"], supporting_value=profile.domain_adjacent_value,
        )
        edge_value, edge_matched = self._weighted(
            self._edge, KeywordMatcher(()), title, body, _SATURATION["edge"]
        )
        geography_value = 1.0 if where.preferred else where.region.value

        fit = (
            profile.seniority_points * seniority_value
            + profile.function_points * function_value
            + profile.domain_points * domain_value
            + profile.geography_points * geography_value
            + profile.edge_points * edge_value
        )
        fit = round(min(100.0, fit), 1)

        thresholds = profile.tier_thresholds
        if fit >= thresholds["strong"]:
            tier = "strong"
        elif fit >= thresholds["possible"]:
            tier = "possible"
        elif fit >= thresholds["stretch"]:
            tier = "stretch"
        else:
            tier = "below"

        return Verdict(
            accepted=True,
            fit=fit,
            tier=tier,
            seniority=level.title,
            region=where.region.id,
            region_title=where.region.title,
            matched=tuple(function_matched + domain_matched + edge_matched),
            reasons=self._reasons(
                opening, level, level_hit, where, function_matched, domain_matched,
                edge_matched,
            ),
        )

    def _reasons(
        self,
        opening: Opening,
        level: SeniorityLevel,
        level_hit: str,
        where: _RegionMatch,
        function_matched: list[str],
        domain_matched: list[str],
        edge_matched: list[str],
    ) -> tuple[str, ...]:
        """The short phrases printed under an opening, in reading order.

        This is the whole point of scoring rather than merely filtering: a brief
        that says "82" and nothing else is asking to be trusted, and a brief
        that says "VP grade · key account management · life insurance · Gurgaon"
        is showing its working.
        """
        reasons: list[str] = [f"{level.title} ({level_hit})"]

        if function_matched:
            reasons.append(" · ".join(function_matched[:3]))
        if domain_matched:
            reasons.append(domain_matched[0])

        place = where.place if where.preferred else (opening.location or where.place)
        title = where.region.title
        reasons.append(f"{title} — {place}" if place else title)

        band = opening.experience_band
        if band:
            low = opening.experience_min
            fits_ideal = low is not None and low >= self.profile.experience_ideal_min
            reasons.append(f"{band} asked{' — squarely in range' if fits_ideal else ''}")

        if edge_matched:
            reasons.append("your edge: " + ", ".join(edge_matched[:3]))
        return tuple(reasons)
