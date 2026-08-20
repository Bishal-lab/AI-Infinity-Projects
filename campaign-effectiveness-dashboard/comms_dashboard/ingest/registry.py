"""Adapter registry and source detection.

Detection has to be honest about not knowing. A file that scores well for two
sources, or well for none, goes to the admin with its headers on screen and a
source picker — because guessing wrong here silently attributes one channel's
numbers to another, and nothing downstream would ever notice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from ..config import SourceMapping, get_mappings
from ..models import IngestError
from .base import DetectionScore, FileProbe, SourceAdapter
from .reader import read_headers

#: adapter classes, by source name, populated by the @register_adapter decorator
_ADAPTER_CLASSES: dict[str, type[SourceAdapter]] = {}

#: How far ahead the winner must be before detection is considered decisive.
AMBIGUITY_MARGIN = 2.0


def register_adapter(cls: type[SourceAdapter]) -> type[SourceAdapter]:
    if not cls.source:
        raise ValueError(f"{cls.__name__} must set a `source` class attribute")
    _ADAPTER_CLASSES[cls.source] = cls
    return cls


def _ensure_adapters_imported() -> None:
    if not _ADAPTER_CLASSES:
        from . import adapters  # noqa: F401  (import registers them)


def build_adapters(
    mappings: dict[str, SourceMapping] | None = None,
) -> dict[str, SourceAdapter]:
    """Instantiate one adapter per configured mapping.

    A mapping with no bespoke adapter class still works: it gets the generic
    base, which is the whole point of the YAML-driven design.
    """
    _ensure_adapters_imported()
    mappings = mappings if mappings is not None else get_mappings()
    adapters: dict[str, SourceAdapter] = {}
    for source, mapping in mappings.items():
        cls = _ADAPTER_CLASSES.get(source, SourceAdapter)
        adapter = cls(mapping)
        adapter.source = source
        adapters[source] = adapter
    return adapters


def get_adapter(
    source: str, mappings: dict[str, SourceMapping] | None = None
) -> SourceAdapter:
    adapters = build_adapters(mappings)
    if source not in adapters:
        raise IngestError(
            f"unknown source {source!r}; configured sources are {sorted(adapters)}"
        )
    return adapters[source]


class DetectionResult:
    """The outcome of asking "what kind of file is this?"."""

    def __init__(
        self,
        scores: list[DetectionScore],
        probe: FileProbe,
        min_scores: dict[str, float],
    ) -> None:
        self.scores = sorted(scores, key=lambda s: s.score, reverse=True)
        self.probe = probe
        self._min_scores = min_scores

    @property
    def best(self) -> DetectionScore | None:
        return self.scores[0] if self.scores else None

    @property
    def runner_up(self) -> DetectionScore | None:
        return self.scores[1] if len(self.scores) > 1 else None

    @property
    def source(self) -> str | None:
        """The detected source, or None when detection was not decisive."""
        if self.best is None:
            return None
        if self.best.score < self._min_scores.get(self.best.source, 4):
            return None
        if self.runner_up and (self.best.score - self.runner_up.score) < AMBIGUITY_MARGIN:
            return None
        return self.best.source

    @property
    def reason(self) -> str:
        if self.best is None:
            return "no adapters configured"
        if self.best.score < self._min_scores.get(self.best.source, 4):
            return (
                f"nothing matched confidently (best was {self.best.source!r} at "
                f"{self.best.score:g}, below its threshold of "
                f"{self._min_scores.get(self.best.source, 4):g})"
            )
        if self.runner_up and (self.best.score - self.runner_up.score) < AMBIGUITY_MARGIN:
            return (
                f"ambiguous — {self.best.source!r} scored {self.best.score:g} and "
                f"{self.runner_up.source!r} scored {self.runner_up.score:g}"
            )
        return f"matched {self.best.source!r}: " + "; ".join(self.best.reasons[:4])

    def as_table(self) -> list[dict[str, object]]:
        return [
            {
                "source": s.source,
                "score": s.score,
                "threshold": self._min_scores.get(s.source, 4),
                "why": "; ".join(s.reasons) or "no signals matched",
            }
            for s in self.scores
        ]


def detect_source(
    path: Path,
    adapters: dict[str, SourceAdapter] | None = None,
    headers: Iterable[str] | None = None,
) -> DetectionResult:
    """Score a file against every configured source."""
    adapters = adapters if adapters is not None else build_adapters()
    if headers is None:
        # Try each adapter's read spec until one yields headers — a file with a
        # title row above the header is common in Excel exports.
        headers = _probe_headers(path, adapters)
    probe = FileProbe(path=path, headers=tuple(str(h) for h in headers))
    scores = [adapter.detect(probe) for adapter in adapters.values()]
    min_scores = {s: a.mapping.detect.min_score for s, a in adapters.items()}
    return DetectionResult(scores, probe, min_scores)


def _probe_headers(path: Path, adapters: dict[str, SourceAdapter]) -> list[str]:
    specs: list[dict] = [{}]
    for adapter in adapters.values():
        spec = adapter.mapping.read
        if spec and spec not in specs:
            specs.append(spec)
    last_error: Exception | None = None
    for spec in specs:
        try:
            headers = read_headers(path, spec)
            if headers:
                return headers
        except Exception as exc:  # noqa: BLE001 - try the next read spec
            last_error = exc
    raise IngestError(f"{path.name}: could not read a header row ({last_error})")


def registered_sources() -> list[str]:
    _ensure_adapters_imported()
    return sorted(_ADAPTER_CLASSES)


__all__: list[str] = [
    "register_adapter",
    "build_adapters",
    "get_adapter",
    "detect_source",
    "DetectionResult",
    "registered_sources",
]
