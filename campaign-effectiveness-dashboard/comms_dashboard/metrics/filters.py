"""The filter state shared across every page.

Frozen and hashable so it can be a cache key. Every query function takes one of
these plus a ``data_version``; the pair is what makes ``@st.cache_data`` correct
after an ingest instead of quietly serving stale numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal


@dataclass(frozen=True)
class FilterState:
    date_from: date | None = None
    date_to: date | None = None
    campaign_ids: tuple[str, ...] = ()      # () means all
    channels: tuple[str, ...] = ()
    departments: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    grade_bands: tuple[str, ...] = ()
    programme: str | None = None
    funnel_basis: Literal["sum", "dedup"] = "sum"

    def with_(self, **changes: Any) -> FilterState:
        return replace(self, **changes)

    # -- SQL construction --------------------------------------------------- #

    def where(self, alias: str = "", *, include_dates: bool = True) -> tuple[str, list[Any]]:
        """Build a WHERE fragment and its parameters.

        Returns ``("1=1", [])`` when nothing is filtered, so callers can always
        interpolate it without special-casing the empty state.
        """
        prefix = f"{alias}." if alias else ""
        clauses: list[str] = ["1=1"]
        params: list[Any] = []

        if self.campaign_ids:
            placeholders = ", ".join("?" for _ in self.campaign_ids)
            clauses.append(f"{prefix}campaign_id IN ({placeholders})")
            params.extend(self.campaign_ids)
        if self.channels:
            placeholders = ", ".join("?" for _ in self.channels)
            clauses.append(f"{prefix}channel IN ({placeholders})")
            params.extend(self.channels)
        if include_dates and self.date_from:
            clauses.append(f"{prefix}period_start >= ?")
            params.append(self.date_from)
        if include_dates and self.date_to:
            clauses.append(f"{prefix}period_start <= ?")
            params.append(self.date_to)
        if self.programme:
            clauses.append(f"{prefix}programme = ?")
            params.append(self.programme)

        return " AND ".join(clauses), params

    def segment_where(self, alias: str = "e") -> tuple[str, list[Any]]:
        """WHERE fragment for the employee dimension (coverage pages only)."""
        prefix = f"{alias}." if alias else ""
        clauses: list[str] = ["1=1"]
        params: list[Any] = []
        for column, values in (
            ("department", self.departments),
            ("region", self.regions),
            ("grade_band", self.grade_bands),
        ):
            if values:
                placeholders = ", ".join("?" for _ in values)
                clauses.append(f"{prefix}{column} IN ({placeholders})")
                params.extend(values)
        return " AND ".join(clauses), params

    @property
    def has_segment_filter(self) -> bool:
        return bool(self.departments or self.regions or self.grade_bands)

    @property
    def summary(self) -> str:
        """One line describing the current view, for exports and page headers."""
        parts: list[str] = []
        if self.date_from or self.date_to:
            parts.append(
                f"{self.date_from or 'start'} to {self.date_to or 'latest'}"
            )
        parts.append(
            f"{len(self.campaign_ids)} campaign(s)" if self.campaign_ids else "all campaigns"
        )
        parts.append(
            f"{len(self.channels)} channel(s)" if self.channels else "all channels"
        )
        if self.has_segment_filter:
            segments = list(self.departments) + list(self.regions) + list(self.grade_bands)
            parts.append("segments: " + ", ".join(segments))
        return " · ".join(parts)
