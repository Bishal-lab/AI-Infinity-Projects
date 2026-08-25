"""Source adapters.

Each module here knows how to read one kind of board and returns `RawPosting`
objects. Nothing else in the radar knows a source exists, so adding a board is
one small file plus an entry in ``config/sources.yaml``.
"""

from __future__ import annotations

from .base import (  # noqa: F401
    ADAPTERS,
    FetchResult,
    SourceError,
    SourceSkipped,
    fetch_all,
    fetch_source,
    register,
)

# Importing each module is what registers its adapter.
from . import adzuna, careerjet, greenhouse, jooble, lever, rss, workday  # noqa: E402,F401
