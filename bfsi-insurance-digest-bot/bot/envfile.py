"""A three-line .env reader.

Enough to keep credentials out of the shell history when running locally,
without adding python-dotenv for it. Values already present in the environment
always win, so a GitHub Actions secret is never shadowed by a stray local file.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | os.PathLike[str]) -> int:
    """Load KEY=VALUE lines from `path` into os.environ. Returns how many."""
    file = Path(path)
    if not file.is_file():
        return 0
    loaded = 0
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded
