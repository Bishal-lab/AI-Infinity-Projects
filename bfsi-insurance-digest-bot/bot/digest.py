"""The pipeline: sources in, a finished brief out.

    fetch → normalise → time window → score & route → dedupe → drop already-sent
          → rank → apply caps → group into sections

Every stage is a plain function over lists, and the fetch step is injectable, so
the whole thing can be exercised offline against fixture feeds.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Sequence

from .config import Config, Section, Source
from .dedupe import dedupe
from .feeds import FetchResult, fetch_all
from .model import Article
from .normalize import to_article
from .relevance import Scorer, Verdict
from .state import SeenStore

log = logging.getLogger(__name__)

FetchFn = Callable[[Sequence[Source], object], list[FetchResult]]

#: Feeds occasionally publish a few minutes into the future (clock skew, or a
#: scheduled post). Allow that rather than silently dropping fresh news.
_FUTURE_TOLERANCE = timedelta(hours=3)


def local_zone(name: str):
    """The digest's display timezone, with a safe fallback.

    A container without tzdata would otherwise fail at 08:00 with a ZoneInfo
    error, which is the worst possible time to discover a missing OS package.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - any failure here means "use the fallback"
        if name in {"Asia/Kolkata", "Asia/Calcutta", "IST"}:
            log.warning("timezone data for %s unavailable; using a fixed +05:30", name)
            return timezone(timedelta(hours=5, minutes=30), "IST")
        log.warning("timezone data for %s unavailable; using UTC", name)
        return timezone.utc


@dataclass
class DigestSection:
    section: Section
    articles: list[Article] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.section.title

    @property
    def emoji(self) -> str:
        return self.section.emoji


@dataclass
class Stats:
    entries_fetched: int = 0
    articles_parsed: int = 0
    in_window: int = 0
    accepted: int = 0
    after_dedupe: int = 0
    unseen: int = 0
    published: int = 0


@dataclass
class Digest:
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    sections: list[DigestSection] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    sources_total: int = 0
    stats: Stats = field(default_factory=Stats)
    rejected: list[tuple[Article, Verdict]] = field(default_factory=list)

    @property
    def articles(self) -> list[Article]:
        return [article for section in self.sections for article in section.articles]

    @property
    def total_items(self) -> int:
        return sum(len(section.articles) for section in self.sections)

    @property
    def is_empty(self) -> bool:
        return self.total_items == 0

    @property
    def sources_ok(self) -> int:
        return self.sources_total - len(self.failures)

    @property
    def degraded(self) -> bool:
        """True when enough sources failed that the brief may be incomplete."""
        return bool(self.failures) and self.sources_total > 0

    def failure_ratio(self) -> float:
        return len(self.failures) / self.sources_total if self.sources_total else 0.0


# --------------------------------------------------------------------------- #
# Pipeline stages
# --------------------------------------------------------------------------- #

def collect_articles(results: Sequence[FetchResult]) -> list[Article]:
    articles: list[Article] = []
    for result in results:
        for entry in result.entries:
            article = to_article(entry, result.source)
            if article is not None:
                articles.append(article)
    return articles


def within_window(
    articles: Sequence[Article],
    window_start: datetime,
    now: datetime,
    undated: str = "window",
) -> list[Article]:
    """Keep articles published inside the window.

    Undated items are common on feeds that only ever list the latest stories.
    The `undated` policy decides whether to trust them (they are dated as of the
    run, so they will not resurface tomorrow) or discard them.
    """
    horizon = now + _FUTURE_TOLERANCE
    kept: list[Article] = []
    for article in articles:
        if article.published is None:
            if undated == "drop":
                continue
            article.published = now
            kept.append(article)
            continue
        if article.published > horizon:
            # Clock skew or a scheduled post; treat it as just-published.
            article.published = now
        if article.published >= window_start:
            kept.append(article)
    return kept


def rank(articles: Sequence[Article]) -> list[Article]:
    """Most relevant first; recency breaks ties."""
    return sorted(
        articles,
        key=lambda a: (a.score, a.published or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )


def apply_caps(articles: Sequence[Article], config: Config) -> list[Article]:
    """Trim to the configured shape: per source, then per section, then total.

    Applied to an already-ranked list, so what survives is the strongest of
    each. The per-source cap runs first, which is what stops one prolific desk
    filling the whole brief.
    """
    settings = config.digest
    per_source: dict[str, int] = defaultdict(int)
    per_section: dict[str | None, int] = defaultdict(int)
    kept: list[Article] = []

    for article in articles:
        if len(kept) >= settings.max_items_total:
            break
        if per_source[article.source_id] >= settings.max_items_per_source:
            continue
        if per_section[article.section_id] >= settings.max_items_per_section:
            continue
        per_source[article.source_id] += 1
        per_section[article.section_id] += 1
        kept.append(article)
    return kept


def group_sections(articles: Sequence[Article], config: Config) -> list[DigestSection]:
    """Bucket articles under their section, dropping sections with no news."""
    buckets: dict[str, list[Article]] = defaultdict(list)
    for article in articles:
        if article.section_id:
            buckets[article.section_id].append(article)

    sections = [
        DigestSection(section=section, articles=buckets[section.id])
        for section in config.sections
        if buckets.get(section.id)
    ]
    if config.digest.section_order == "volume":
        sections.sort(key=lambda entry: len(entry.articles), reverse=True)
    return sections


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def build_digest(
    config: Config,
    now: datetime | None = None,
    store: SeenStore | None = None,
    fetch_fn: FetchFn | None = None,
    keep_rejected: bool = False,
) -> Digest:
    """Run the whole pipeline and return the finished brief.

    `store` may be None (every story counts as new — used by `preview`), and
    `fetch_fn` may be swapped for a fixture loader in tests.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    zone = local_zone(config.digest.timezone)
    window_start = now - timedelta(hours=config.digest.lookback_hours)
    sources = config.enabled_sources
    fetcher = fetch_fn or fetch_all

    results = list(fetcher(sources, config.fetch))
    failures = [
        (result.source.name, result.error or "unknown error")
        for result in results
        if not result.ok
    ]
    for name, error in failures:
        log.warning("source unavailable: %s (%s)", name, error)

    stats = Stats(entries_fetched=sum(len(result.entries) for result in results))

    articles = collect_articles(results)
    stats.articles_parsed = len(articles)

    articles = within_window(articles, window_start, now, config.digest.undated_items)
    stats.in_window = len(articles)

    scorer = Scorer(config)
    accepted, rejected = scorer.apply(articles)
    stats.accepted = len(accepted)

    accepted = rank(accepted)
    accepted = dedupe(accepted)
    stats.after_dedupe = len(accepted)

    if store is not None:
        accepted = store.filter_new(accepted)
    stats.unseen = len(accepted)

    selected = apply_caps(accepted, config)
    stats.published = len(selected)

    digest = Digest(
        generated_at=now.astimezone(zone),
        window_start=window_start.astimezone(zone),
        window_end=now.astimezone(zone),
        sections=group_sections(selected, config),
        failures=failures,
        sources_total=len(sources),
        stats=stats,
        rejected=rejected if keep_rejected else [],
    )
    log.info(
        "digest built: %d entries -> %d in window -> %d relevant -> %d unique -> %d new -> %d sent",
        stats.entries_fetched, stats.in_window, stats.accepted,
        stats.after_dedupe, stats.unseen, stats.published,
    )
    return digest
