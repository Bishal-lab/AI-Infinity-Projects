"""Remembering what has already been sent.

The fetch window is deliberately wider than a day, so consecutive runs overlap.
This store is what stops that overlap turning into a digest that repeats
yesterday's headlines. It keys on both the article URL and its normalised
headline, because the same story often arrives tomorrow under a different link.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .dedupe import article_keys
from .model import Article

log = logging.getLogger(__name__)

_VERSION = 1


class SeenStore:
    """A small JSON file mapping article keys to when they were sent."""

    def __init__(self, path: str | os.PathLike[str], retention_days: int = 14) -> None:
        self.path = Path(path)
        self.retention_days = max(1, int(retention_days))
        self._entries: dict[str, str] = {}
        self._loaded = False

    # ------------------------------------------------------------------ #

    def load(self) -> "SeenStore":
        self._entries = {}
        self._loaded = True
        if not self.path.is_file():
            return self
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
            if isinstance(entries, dict):
                self._entries = {
                    str(key): str(value) for key, value in entries.items() if key
                }
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            # A damaged store means one noisier digest, not a failed run.
            log.warning("seen-store at %s unreadable (%s); starting fresh", self.path, exc)
            self._entries = {}
        return self

    def save(self) -> None:
        """Write atomically, so an interrupted run cannot corrupt the store."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entries": self._entries,
        }
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, prefix=".seen-", suffix=".tmp", delete=False
        )
        try:
            with handle:
                json.dump(payload, handle, ensure_ascii=False, indent=0, sort_keys=True)
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------ #

    def __len__(self) -> int:
        return len(self._entries)

    def is_seen(self, article: Article) -> bool:
        return any(key in self._entries for key in article_keys(article))

    def filter_new(self, articles: Sequence[Article]) -> list[Article]:
        """Drop anything already sent, including duplicates within this batch."""
        fresh: list[Article] = []
        batch: set[str] = set()
        for article in articles:
            keys = article_keys(article)
            if self.is_seen(article) or any(key in batch for key in keys):
                continue
            batch.update(keys)
            fresh.append(article)
        return fresh

    def mark(self, articles: Iterable[Article], when: datetime | None = None) -> None:
        stamp = (when or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
            timespec="seconds"
        )
        for article in articles:
            for key in article_keys(article):
                self._entries[key] = stamp

    def prune(self, now: datetime | None = None) -> int:
        """Forget entries older than the retention window. Returns how many."""
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self.retention_days)
        stale = []
        for key, value in self._entries.items():
            try:
                stamp = datetime.fromisoformat(value)
            except ValueError:
                stale.append(key)
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp < cutoff:
                stale.append(key)
        for key in stale:
            del self._entries[key]
        return len(stale)
