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
    # --- memory (plan section 12.2) ----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS prep_memory (
        id                INTEGER PRIMARY KEY,
        task_key          TEXT NOT NULL,
        original_task     TEXT NOT NULL,
        interpreted_goal  TEXT NOT NULL,
        session_objective TEXT NOT NULL,
        topics_covered    TEXT NOT NULL,
        topics_deferred   TEXT NOT NULL,
        source_urls       TEXT NOT NULL,
        run_id            TEXT NOT NULL,
        created_at        TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_prep_task_key ON prep_memory(task_key)",
    """
    CREATE TABLE IF NOT EXISTS run_memory (
        id         INTEGER PRIMARY KEY,
        run_id     TEXT NOT NULL,
        hop        INTEGER NOT NULL,
        kind       TEXT NOT NULL,
        content    TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_run_memory_run ON run_memory(run_id)",
    # --- tracing (plan sections 12.2 and 13) ------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id          TEXT PRIMARY KEY,
        task_title      TEXT NOT NULL,
        session_minutes INTEGER NOT NULL,
        started_at      TEXT NOT NULL,
        ended_at        TEXT,
        status          TEXT NOT NULL,
        hops_used       INTEGER,
        model_calls     INTEGER,
        search_calls    INTEGER,
        fetch_calls     INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS spans (
        span_id        TEXT PRIMARY KEY,
        run_id         TEXT NOT NULL,
        parent_span_id TEXT,
        name           TEXT NOT NULL,
        kind           TEXT NOT NULL,
        started_at     TEXT NOT NULL,
        ended_at       TEXT,
        duration_ms    INTEGER,
        ok             INTEGER,
        error_code     TEXT,
        from_cache     INTEGER,
        input_summary  TEXT,
        output_summary TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_spans_run ON spans(run_id)",
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

`SCHEMA_VERSION` is **not** bumped by an addition: it marks a change to an existing
table's shape, and `runs`/`spans` (T1) and `prep_memory`/`run_memory` (T4) were all new
tables on an already-populated file.

`spans.run_id` deliberately carries **no foreign key** to `runs.run_id`, matching plan
section 12.2. `foreign_keys = ON` is set below, so a key here would make a span
unwritable in exactly the case a trace is most wanted — the one where the run row
failed to be written. `ended_at` is nullable on both tables for the same reason: a row
is written when an operation *starts*, so a run killed mid-flight still leaves evidence.
`run_memory.run_id` carries no key either, for that same reason.

**`prep_memory` and `run_memory` share this file and nothing else.** One survives runs and
is looked up by a normalised task key; the other belongs to exactly one `run_id` and is that
run's own record of its hops. Neither is ever read as a substitute for the other, and
`prep_memory` gains an `interpreted_goal` column the plan's DDL does not list — the two
narrowing fields answer different questions and a continuation needs both.
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
