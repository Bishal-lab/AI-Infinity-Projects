"""The two data shapes that travel through the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawPosting:
    """One opening as it came off a source, before any cleaning.

    Every adapter in `radar/sources/` produces these and nothing else, which is
    what keeps a new board to one small file: normalising, scoring, deduping
    and rendering never learn that a source exists.
    """

    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    summary: str = ""
    posted_raw: str = ""
    guid: str = ""
    experience_raw: str = ""


@dataclass
class Opening:
    """A cleaned, scored opening."""

    source_id: str
    source_name: str
    title: str
    url: str
    company: str = ""
    location: str = ""
    summary: str = ""
    posted: datetime | None = None
    guid: str = ""

    # Filled in by normalize.py, when the posting advertises a band.
    experience_raw: str = ""
    experience_min: int | None = None
    experience_max: int | None = None

    # Filled in by fit.py
    fit: float = 0.0
    tier: str = ""
    seniority: str = ""
    region: str = ""
    region_title: str = ""
    matched: tuple[str, ...] = field(default_factory=tuple)
    #: Short human phrases explaining the score — printed under each opening so
    #: the brief never asks its reader to take a number on trust.
    reasons: tuple[str, ...] = field(default_factory=tuple)

    # Filled in by dedupe.py
    dedupe_key: str = ""
    duplicate_count: int = 0

    @property
    def attribution(self) -> str:
        """Who to credit for the listing."""
        return self.source_name

    @property
    def where(self) -> str:
        """Location as shown to a reader, falling back to the region."""
        return self.location or self.region_title or ""

    @property
    def experience_band(self) -> str:
        """'8-12 yrs', '15+ yrs', or empty when the posting says nothing."""
        if self.experience_min is not None and self.experience_max is not None:
            return f"{self.experience_min}-{self.experience_max} yrs"
        if self.experience_min is not None:
            return f"{self.experience_min}+ yrs"
        if self.experience_max is not None:
            return f"up to {self.experience_max} yrs"
        return ""
