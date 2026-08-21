"""The whole preparation, kept so a surface can hand it back (Day 6).

The `prep_report` table and nothing else. Its sibling `prep_memory.py` stores what the *next
run* needs — a compact summary, keyed by subject, deliberately lossy. This stores what a
*reader* needs: the validated `FocusPreparationReport` exactly as it was produced, retrievable
by the run that produced it.

**Why two tables rather than one.** They answer different questions on different keys and
have different reasons to change. `prep_memory` is read by `task_key` to continue a subject,
and its shape is the agent's business; `prep_report` is read by `run_id` to show a record, and
its shape is `FocusPreparationReport`'s. Widening `prep_memory` with a report column would
have made a summary and a record the same row, and `schemas/agents.py` is explicit that
`PreviousPreparation` is "a summary, not a second copy of the report".

Storage only, on the same terms as `prep_memory.py`, `cache.py` and `tracing/store.py`: it
opens no connection of its own, it takes an injected `now`, and **it raises** — a
`sqlite3.Error` propagates so a caller and a test can see it. Keeping a storage failure from
ending a fifteen-minute research run is `tools/memory_tools.py`'s job, in one place.

The report is stored as `model_dump_json()` rather than a column per field, and that is the
point: `FocusPreparationReport` is the most expensive schema in the project to change, and a
table mirroring its fields would be a second definition of it that has to be migrated every
time the first one moves. One text column has no shape to drift.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from evergrove_agent.memory.db import transaction
from evergrove_agent.memory.prep_memory import normalize_task_key
from evergrove_agent.schemas import FocusPreparationReport

_COLUMNS = "run_id, task_key, report_json, created_at"


def save_report(
    connection: sqlite3.Connection,
    *,
    report: FocusPreparationReport,
    now: datetime | None = None,
) -> None:
    """Store `report` whole, under its own run id.

    **Only ever called with a report that already passed `validate_report`**, for the same
    reason and at the same call site as `save_preparation`: `finalise()` either returns a
    validated report or raises `PreparationFailed`, so the loop's success path is the only
    place a report exists to save.

    `INSERT OR REPLACE` rather than the append `prep_memory` uses. A run id identifies one
    run, and one run produces one report — a second row under the same id could only ever be
    the same report written twice. Replacing also means a retried write cannot fail on the
    primary key and take an otherwise successful run's storage down with it.

    `model_dump_json()`, never `model_dump()` plus `json.dumps`: `Resource.url` is an
    `HttpUrl`, which is not a `str` and does not survive the stdlib encoder.
    """
    with transaction(connection) as cursor:
        cursor.execute(
            f"INSERT OR REPLACE INTO prep_report ({_COLUMNS}) VALUES (?, ?, ?, ?)",
            (
                report.run_id,
                normalize_task_key(report.original_task),
                report.model_dump_json(),
                (now or datetime.now(UTC)).isoformat(),
            ),
        )


def get_report(
    connection: sqlite3.Connection, run_id: str
) -> FocusPreparationReport | None:
    """The report that run produced, or `None` if nothing readable is stored under it.

    `None` covers both "no such run" and "the stored row cannot be parsed" — the same
    degradation `recall_previous_preparation` applies, and for the same reason: a row written
    by an older build must not raise inside a read. The caller decides what an absent report
    means; here it is simply absent.
    """
    row = connection.execute(
        "SELECT report_json FROM prep_report WHERE run_id = ?", (run_id,)
    ).fetchone()
    return _to_report(row)


def latest_report(connection: sqlite3.Connection) -> FocusPreparationReport | None:
    """The most recently stored report, or `None` if none is stored.

    Ordered by `created_at` rather than by rowid: the column is indexed for exactly this
    query, and a run id is random, so insertion order carries no meaning a reader could use.
    """
    row = connection.execute(
        "SELECT report_json FROM prep_report ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return _to_report(row)


def _to_report(row: sqlite3.Row | None) -> FocusPreparationReport | None:
    """Rebuild a stored report, or `None` if this row is missing or not readable."""
    if row is None:
        return None
    try:
        return FocusPreparationReport.model_validate_json(row["report_json"])
    except ValueError:
        # `ValueError` covers both a malformed JSON payload and Pydantic's
        # `ValidationError` for one that no longer satisfies the schema.
        return None
