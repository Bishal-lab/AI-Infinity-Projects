"""Shared fixtures.

Each test that touches the warehouse gets its own temporary DuckDB file, so
tests cannot interfere with each other or with a developer's real data.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from comms_dashboard import db  # noqa: E402
from comms_dashboard.config import (  # noqa: E402
    load_all_mappings,
    load_settings,
    load_stage_ladder,
)

SAMPLES = PROJECT_ROOT / "data" / "samples"
EDGE_CASES = SAMPLES / "edge_cases"


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def ladder():
    return load_stage_ladder()


@pytest.fixture(scope="session")
def mappings():
    return load_all_mappings()


@pytest.fixture
def con(tmp_path, ladder):
    """A fresh warehouse per test."""
    connection = db.connect(tmp_path / "test.duckdb")
    db.init_schema(connection, ladder)
    yield connection
    connection.close()


@pytest.fixture
def inbox(tmp_path):
    """An empty inbox directory the loader can archive out of."""
    path = tmp_path / "inbox"
    path.mkdir()
    return path


@pytest.fixture
def staged(inbox):
    """Copy a sample file into a scratch inbox so archiving does not move the original."""

    def _stage(name: str, source_dir: Path = SAMPLES) -> Path:
        target = inbox / name
        shutil.copy2(source_dir / name, target)
        return target

    return _stage


@pytest.fixture
def loaded(con, staged, settings, ladder):
    """A warehouse with the full sample set ingested."""
    from comms_dashboard.ingest.loader import ingest_file

    order = [
        "employee_roster.csv",
        "campaign_registry.csv",
        "email_campaigns_2026-01.csv",
        "whatsapp_broadcast_2026-03.xlsx",
        "lms_enrolments_2026-02.csv",
        "teams_webinar_attendance_2026-04.xlsx",
        "viva_engage_posts_2026-05.csv",
    ]
    for name in order:
        report = ingest_file(
            con, staged(name), settings=settings, ladder=ladder, archive=False
        )
        assert report.status.startswith("loaded"), (name, report.issues)
    return con
