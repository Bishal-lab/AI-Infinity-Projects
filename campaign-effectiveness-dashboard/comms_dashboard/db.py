"""DuckDB connection management and schema bootstrap.

Why DuckDB: a single embedded file with no server and no admin rights to
install, a columnar engine that keeps the group-by-heavy dashboard queries
interactive, and zero-copy handoff to pandas — which is exactly the Streamlit
boundary. Same air-gap friendliness as SQLite, far better at this workload.

Two DuckDB behaviours are handled deliberately here:

* **Single writer.** One process may hold the file for writing. The app keeps a
  single cached connection and serialises writes through ``write_lock``; a
  second process (an admin poking at the file, a cron loader running while
  someone browses) must open it read-only or it will fail to take the lock.
* **Extension autoloading downloads from the internet.** Disabled at connect,
  because a dashboard that phones home on first query is not an on-prem
  dashboard. Excel is read through openpyxl in Python instead.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from .config import PROJECT_ROOT, Settings, StageLadder, get_settings, get_stage_ladder

SQL_DIR = PROJECT_ROOT / "sql"

#: Serialises writes against the single-writer file lock.
write_lock = threading.Lock()


def _apply_offline_pragmas(con: duckdb.DuckDBPyConnection) -> None:
    """Stop DuckDB reaching for the network to fetch extensions."""
    con.execute("SET autoinstall_known_extensions = false")
    con.execute("SET autoload_known_extensions = false")


def connect(
    path: str | Path | None = None,
    *,
    read_only: bool = False,
    settings: Settings | None = None,
) -> duckdb.DuckDBPyConnection:
    """Open the warehouse, creating its directory if needed.

    Pass ``path=":memory:"`` for a throwaway database (tests do this).
    """
    settings = settings or get_settings()
    if path is None:
        path = settings.path("warehouse")
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    _apply_offline_pragmas(con)
    return con


def _run_sql_file(con: duckdb.DuckDBPyConnection, name: str) -> None:
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    con.execute(sql)


def init_schema(
    con: duckdb.DuckDBPyConnection,
    ladder: StageLadder | None = None,
) -> None:
    """Create tables and views, then materialise the stage-support matrix.

    Safe to call on every start: the DDL is idempotent, and the support matrix
    is rebuilt from ``config/stage_ladder.yaml`` so an edit there takes effect
    on the next launch without a migration.
    """
    ladder = ladder or get_stage_ladder()
    _run_sql_file(con, "01_schema.sql")
    _run_sql_file(con, "02_views.sql")
    sync_stage_ladder(con, ladder)


def sync_stage_ladder(con: duckdb.DuckDBPyConnection, ladder: StageLadder) -> None:
    """Rewrite dim_channel and dim_channel_stage_support from the YAML ladder."""
    con.execute("DELETE FROM dim_channel_stage_support")
    con.execute("DELETE FROM dim_channel")

    channel_rows = [
        (c.name, c.display_name, c.grain, c.sort_order)
        for c in sorted(ladder.channels, key=lambda c: c.sort_order)
    ]
    con.executemany(
        "INSERT INTO dim_channel (channel, display_name, grain, sort_order) "
        "VALUES (?, ?, ?, ?)",
        channel_rows,
    )

    support_rows = [
        (
            s.channel,
            s.stage,
            s.ordinal,
            s.status.value,
            s.unit.value,
            s.native_name,
            s.caveat,
        )
        for s in ladder.support.values()
    ]
    con.executemany(
        "INSERT INTO dim_channel_stage_support "
        "(channel, stage, stage_ordinal, support_status, unit, native_name, caveat) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        support_rows,
    )


def next_batch_id(con: duckdb.DuckDBPyConnection) -> int:
    return int(con.execute("SELECT nextval('seq_batch_id')").fetchone()[0])


def data_version(con: duckdb.DuckDBPyConnection) -> int:
    """A monotonic marker of "the data changed".

    The Streamlit pages key their ``@st.cache_data`` on this. Without it the
    cache happily serves pre-load numbers after an ingest, which looks exactly
    like the loader silently failing.
    """
    row = con.execute("SELECT COALESCE(MAX(batch_id), 0) FROM load_batch").fetchone()
    return int(row[0]) if row else 0


def query_df(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: list[Any] | tuple[Any, ...] | None = None,
) -> pd.DataFrame:
    """Run a query and return a DataFrame, using a cursor so it is thread-safe."""
    cur = con.cursor()
    try:
        _apply_offline_pragmas(cur)
        if params:
            return cur.execute(sql, list(params)).df()
        return cur.execute(sql).df()
    finally:
        cur.close()


def table_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Row counts for the admin page."""
    tables = [
        "dim_campaign",
        "dim_employee",
        "dim_employee_identity",
        "fact_recipient_stage",
        "fact_stage_metric",
        "load_batch",
    ]
    counts: dict[str, int] = {}
    for t in tables:
        try:
            counts[t] = int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        except duckdb.Error:  # pragma: no cover - table missing before init
            counts[t] = 0
    return counts
