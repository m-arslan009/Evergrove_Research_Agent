"""The one SQLite file, and the only way anything opens it.

Plan section 12: cache, memory, budget counters and traces all live in a single file,
stdlib `sqlite3`, no ORM. This module owns opening it, the pragmas it is opened with,
creating its tables, and running a group of writes as one unit. It owns no feature: the
source cache, the search cache, the monthly quota guard, memory and tracing each add
their table's DDL to `SCHEMA_STATEMENTS` below and keep their own queries.

Nothing here imports a tool, a model, HTTP or a search backend — stdlib plus `config`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from evergrove_agent.config import get_settings

SCHEMA_VERSION = 1
"""Bumped when an existing table changes shape. Recorded in `schema_meta` so a later
subtask can tell an old file from a current one instead of guessing."""

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_cache (
        url          TEXT PRIMARY KEY,
        final_url    TEXT NOT NULL,
        title        TEXT NOT NULL,
        text         TEXT NOT NULL,
        content_type TEXT NOT NULL,
        fetched_at   TEXT NOT NULL,
        expires_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS search_cache (
        key          TEXT PRIMARY KEY,
        query        TEXT NOT NULL,
        backend      TEXT NOT NULL,
        source_type  TEXT NOT NULL,
        max_results  INTEGER NOT NULL,
        results_json TEXT NOT NULL,
        searched_at  TEXT NOT NULL,
        expires_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS search_budget (
        month TEXT PRIMARY KEY,
        used  INTEGER NOT NULL DEFAULT 0
    )
    """,
    f"""
    INSERT OR IGNORE INTO schema_meta (key, value)
    VALUES ('schema_version', '{SCHEMA_VERSION}')
    """,
)
"""Every table in the database, in creation order.

All DDL lives here rather than in the feature modules: `cache.py` needs `connect`, so
`db.py` importing it back would be a cycle, and creation order would then depend on
which module happened to be imported first. Adding a table later is appending an
`IF NOT EXISTS` statement to this tuple — the layer itself does not change.
"""

_PRAGMAS: tuple[str, ...] = (
    "PRAGMA foreign_keys = ON",
    # WAL survives in the file and lets a reader work while a writer holds the lock.
    "PRAGMA journal_mode = WAL",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA synchronous = NORMAL",
)


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open the database at `db_path`, or at `DB_PATH` from config when omitted.

    Creates the parent directory on first use — the configured default lives under
    `data/`, which is not in the repository. Rows come back as `sqlite3.Row` so callers
    read columns by name.
    """
    path = Path(db_path) if db_path is not None else get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        connection.execute(pragma)
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Cursor]:
    """Run a group of statements as one unit: commit on success, roll back on error.

    `sqlite3`'s own `with connection:` does not open a transaction around DDL, and it
    leaves the cursor to the caller. A cache write that fails halfway must leave nothing
    behind, so every multi-statement write goes through here.
    """
    cursor = connection.cursor()
    try:
        yield cursor
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        cursor.close()


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create every table the database needs, if it does not already have it.

    Idempotent and non-destructive: safe on a fresh file, on an already-initialised one,
    and on one holding real cached data. Cheap enough to call on every open.
    """
    with transaction(connection) as cursor:
        for statement in SCHEMA_STATEMENTS:
            cursor.execute(statement)


@contextmanager
def open_database(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Connect, ensure the schema exists, and close again — the usual way in.

    Saves every caller repeating connect / initialise / try-finally-close. Long-lived
    callers that manage their own lifetime can use `connect` directly instead.
    """
    connection = connect(db_path)
    try:
        initialize_schema(connection)
        yield connection
    finally:
        connection.close()
