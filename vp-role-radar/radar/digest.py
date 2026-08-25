"""Assembling one morning's brief.

Fetch every source, normalise what comes back, score it against the profile,
drop what is stale or already sent, and group what is left by how well it fits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Sequence

from .config import Config
from .dedupe import dedupe
from .fit import Scorer, Verdict
from .model import Opening
from .normalize import to_opening
from .sources import FetchResult, fetch_all
from .state import SeenStore

log = logging.getLogger(__name__)

#: Tier id -> how it is titled in the brief, in the order they appear.
TIER_TITLES = {
    "strong": "Strong fit",
    "possible": "Possible fit",
    "stretch": "Stretch",
}


def local_zone(name: str):
    """The configured timezone, falling back to UTC on a container with no tzdata."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - any failure here means "use UTC"
        log.warning("timezone %s unavailable; using UTC", name)
        return timezone.utc


@dataclass
class DigestTier:
    id: str
    title: str
    openings: list[Opening] = field(default_factory=list)


@dataclass
class Stats:
    postings_fetched: int = 0
    in_window: int = 0
    accepted: int = 0
    after_dedupe: int = 0
    unseen: int = 0
    published: int = 0


@dataclass
class Digest:
    generated_at: datetime
    tiers: list[DigestTier] = field(default_factory=list)
    stats: Stats = field(default_factory=Stats)
    #: (source name, why) for sources that failed outright.
    failures: list[tuple[str, str]] = field(default_factory=list)
    #: (source name, why) for sources that are switched off for want of a key.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    sources_total: int = 0
    #: Populated only by `preview --explain`.
    rejected: list[tuple[Opening, Verdict]] = field(default_factory=list)

    @property
    def openings(self) -> list[Opening]:
        return [opening for tier in self.tiers for opening in tier.openings]

    @property
    def is_empty(self) -> bool:
        return not self.openings

    def failure_ratio(self) -> float:
        """Of the sources that could run, how many did not."""
        runnable = self.sources_total - len(self.skipped)
        if runnable <= 0:
            return 0.0
        return len(self.failures) / runnable


def collect_openings(
    results: Sequence[FetchResult],
    config: Config,
    now: datetime | None = None,
) -> tuple[list[Opening], list[tuple[str, str]], list[tuple[str, str]]]:
    """Turn fetch results into openings, keeping the failures as values."""
    openings: list[Opening] = []
    failures: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []

    for result in results:
        if result.skipped:
            skipped.append((result.source.name, result.skipped))
            continue
        if not result.ok:
            failures.append((result.source.name, result.error or "unknown error"))
            continue
        for posting in result.postings:
            opening = to_opening(posting, result.source, now=now)
            if opening is not None:
                openings.append(opening)
    return openings, failures, skipped


def within_window(
    openings: Sequence[Opening],
    config: Config,
    now: datetime | None = None,
) -> list[Opening]:
    """Drop anything posted longer ago than the lookback window.

    Undated postings are governed by `radar.undated_items`. Trusting them is the
    default and the right call here: several boards date only to the day or not
    at all, and the seen-store — not this window — is what prevents repeats.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=config.radar.lookback_days)
    keep_undated = config.radar.undated_items == "window"

    fresh: list[Opening] = []
    for opening in openings:
        if opening.posted is None:
            if keep_undated:
                fresh.append(opening)
            continue
        # A posting dated in the future is a badly set clock somewhere, not a
        # reason to hide the job.
        if opening.posted >= cutoff:
            fresh.append(opening)
    return fresh


def rank(openings: Sequence[Opening]) -> list[Opening]:
    """Best fit first; among equals, the most recently posted."""
    return sorted(
        openings,
        key=lambda o: (o.fit, o.posted.timestamp() if o.posted else 0.0),
        reverse=True,
    )


def apply_caps(openings: Sequence[Opening], config: Config) -> list[Opening]:
    """Per source, then per tier, then overall — in that order."""
    settings = config.radar
    per_source: dict[str, int] = {}
    per_tier: dict[str, int] = {}
    kept: list[Opening] = []

    for opening in openings:
        if per_source.get(opening.source_id, 0) >= settings.max_items_per_source:
            continue
        if per_tier.get(opening.tier, 0) >= settings.max_items_per_tier:
            continue
        if len(kept) >= settings.max_items_total:
            break
        per_source[opening.source_id] = per_source.get(opening.source_id, 0) + 1
        per_tier[opening.tier] = per_tier.get(opening.tier, 0) + 1
        kept.append(opening)
    return kept


def group_tiers(openings: Sequence[Opening]) -> list[DigestTier]:
    """Group into Strong / Possible / Stretch, dropping any tier with nothing in it."""
    tiers: list[DigestTier] = []
    for tier_id, title in TIER_TITLES.items():
        members = [opening for opening in openings if opening.tier == tier_id]
        if members:
            tiers.append(DigestTier(id=tier_id, title=title, openings=members))
    return tiers


def build_digest(
    config: Config,
    store: SeenStore | None = None,
    keep_rejected: bool = False,
    now: datetime | None = None,
    results: Sequence[FetchResult] | None = None,
) -> Digest:
    """Run the whole pipeline.

    `results` is an injection point for the tests, which must never touch the
    network: pass fetch results in and everything downstream runs unchanged.
    """
    reference = now or datetime.now(timezone.utc)
    if results is None:
        results = fetch_all(config.enabled_sources, config.profile, config.fetch)

    openings, failures, skipped = collect_openings(results, config, now=reference)
    stats = Stats(postings_fetched=len(openings))

    fresh = within_window(openings, config, now=reference)
    stats.in_window = len(fresh)

    scorer = Scorer(config.profile)
    accepted: list[Opening] = []
    rejected: list[tuple[Opening, Verdict]] = []
    for opening in fresh:
        verdict = scorer.score(opening)
        if not verdict.accepted or verdict.fit < config.radar.min_fit:
            if keep_rejected:
                if verdict.accepted and not verdict.reason:
                    verdict.reason = (
                        f"scored {verdict.fit:.0f}, below the {config.radar.min_fit:.0f} floor"
                    )
                rejected.append((opening, verdict))
            continue
        opening.fit = verdict.fit
        opening.tier = verdict.tier
        opening.seniority = verdict.seniority
        opening.region = verdict.region
        opening.region_title = verdict.region_title
        opening.matched = verdict.matched
        opening.reasons = verdict.reasons
        accepted.append(opening)
    stats.accepted = len(accepted)

    unique = dedupe(accepted)
    stats.after_dedupe = len(unique)

    unseen = store.filter_new(unique) if store is not None else unique
    stats.unseen = len(unseen)

    published = apply_caps(rank(unseen), config)
    stats.published = len(published)

    return Digest(
        generated_at=reference,
        tiers=group_tiers(published),
        stats=stats,
        failures=failures,
        skipped=skipped,
        # One result per source attempted — counting the configured sources
        # instead would misreport the ratio whenever results are injected.
        sources_total=len(results),
        rejected=rejected,
    )
