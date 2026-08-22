"""The two data shapes that travel through the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawEntry:
    """One <item>/<entry> as it came off the wire, before any cleaning."""

    title: str = ""
    link: str = ""
    summary: str = ""
    published_raw: str = ""
    guid: str = ""
    publisher: str = ""


@dataclass
class Article:
    """A cleaned, scored, routed story."""

    source_id: str
    source_name: str
    title: str
    url: str
    summary: str = ""
    published: datetime | None = None
    publisher: str = ""
    guid: str = ""

    # Filled in by relevance.py
    score: float = 0.0
    section_id: str | None = None
    matched: tuple[str, ...] = field(default_factory=tuple)

    # Filled in by dedupe.py
    dedupe_key: str = ""
    duplicate_count: int = 0

    @property
    def attribution(self) -> str:
        """Who to credit. A search feed knows the real publisher; use it."""
        return self.publisher or self.source_name
