"""The `runs` and `spans` rows: reading and writing them, and nothing else.

Plan sections 12.2 and 13. This is storage only — it does not know what an agent, a tool
or a hook is, it never decides what a span *means*, and it opens no connection of its own:
the caller passes one from `db.connect` / `db.open_database`, which stays the single way
into the file. The same split `memory/cache.py` takes, for the same reason.

**Two writes per operation, not one.** A row is inserted when something *starts*, with
`ended_at` left NULL, and updated when it finishes. A run on this hardware takes 9-15
minutes, and S14 killed several of them: a span that only appears once it has succeeded is
missing at precisely the moment a trace is wanted. `ended_at IS NULL` is therefore
meaningful — it reads "this operation never finished", which is a diagnosis rather than a
gap.

**This module raises.** A `sqlite3.Error` propagates, because a store that silently
swallows a failed write cannot be trusted by anything, including a test. Keeping a
tracing failure from ending a research run is `tracer.Tracer`'s job, in one place.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from evergrove_agent.config import get_settings
from evergrove_agent.memory.db import transaction

SpanKind = Literal["agent", "tool", "llm"]
"""What sort of operation a span records: an agent boundary, a tool call, or a model call.

The three the plan's trace tree distinguishes (section 13). A plain `Literal` rather than
an enum, matching `BudgetKind` and `SearchSourceType`.
"""

RunStatus = Literal["running", "ok", "failed", "budget_exhausted"]
"""How a run ended — plus `running`, which is what `start_run` writes.

The plan names the three terminal values. `running` exists because the row is written
before the outcome is known, and a status column that was NULL for the whole run would
make "still going" and "we forgot to record it" the same answer.
"""

_RUN_COLUMNS = (
    "run_id, task_title, session_minutes, started_at, ended_at, status,"
    " hops_used, model_calls, search_calls, fetch_calls"
)
_SPAN_COLUMNS = (
    "span_id, run_id, parent_span_id, name, kind, started_at, ended_at,"
    " duration_ms, ok, error_code, from_cache, input_summary, output_summary"
)


@dataclass(frozen=True)
class RunRecord:
    """One research run, as the `runs` table holds it.

    A storage row, deliberately not a Pydantic model: nothing here crosses a tool or an
    agent boundary, and the shapes that do live in `schemas/`. Same call as
    `memory.cache.CachedSource`.
    """

    run_id: str
    task_title: str
    session_minutes: int
    started_at: datetime
    ended_at: datetime | None
    """`None` while the run is in flight, or if it was killed before it could finish."""
    status: RunStatus
    hops_used: int | None
    model_calls: int | None
    search_calls: int | None
    fetch_calls: int | None
    """The four counters, `None` until the run finishes. `RunBudget` owns them live; this
    is the snapshot taken when the run ended, so a trace can be read back without it."""


@dataclass(frozen=True)
class SpanRecord:
    """One traced operation, as the `spans` table holds it."""

    span_id: str
    run_id: str
    parent_span_id: str | None
    """`None` for a top-level operation. Everything else nests under one, which is what
    makes the trace a tree rather than a list."""
    name: str
    kind: SpanKind
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    ok: bool | None
    """`None` while in flight — distinct from `False`, which is a recorded failure."""
    error_code: str | None
    from_cache: bool | None
    input_summary: str | None
    output_summary: str | None


# --- runs ---------------------------------------------------------------------------------


def start_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    task_title: str,
    session_minutes: int,
    now: datetime | None = None,
) -> None:
    """Record that `run_id` has begun, with `status = "running"`.

    `INSERT OR REPLACE`, so re-recording a run id is a refresh rather than an
    `IntegrityError` the caller would only find out about by having lost the trace. Run
    ids are six hex characters, so a collision across a long-lived database is possible;
    overwriting the older run's header is the better failure, because the alternative is
    the current run having no trace at all.
    """
    with transaction(connection) as cursor:
        cursor.execute(
            f"INSERT OR REPLACE INTO runs ({_RUN_COLUMNS})"
            " VALUES (?, ?, ?, ?, NULL, 'running', NULL, NULL, NULL, NULL)",
            (run_id, task_title, session_minutes, _stamp(now)),
        )


def finish_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    status: RunStatus,
    hops_used: int,
    model_calls: int,
    search_calls: int,
    fetch_calls: int,
    now: datetime | None = None,
) -> None:
    """Close `run_id` out with its outcome and the four counters it spent.

    The counters are passed in rather than read from a `RunBudget` here, the same shape
    `RunBudget.hops_remaining` takes: this layer records numbers, it does not reach into
    the ledger that produced them. `Tracer.finish_run` is the one place that reads them
    off the live budget.

    An unknown `run_id` updates nothing and reports nothing — the run header write may
    itself have failed, and a trace is a diagnostic rather than a thing worth failing over.
    """
    with transaction(connection) as cursor:
        cursor.execute(
            "UPDATE runs SET ended_at = ?, status = ?, hops_used = ?,"
            " model_calls = ?, search_calls = ?, fetch_calls = ?"
            " WHERE run_id = ?",
            (
                _stamp(now),
                status,
                hops_used,
                model_calls,
                search_calls,
                fetch_calls,
                run_id,
            ),
        )


def get_run(connection: sqlite3.Connection, run_id: str) -> RunRecord | None:
    """The run header for `run_id`, or `None` when nothing was recorded under it."""
    row = connection.execute(
        f"SELECT {_RUN_COLUMNS} FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    return None if row is None else _to_run(row)


# --- spans --------------------------------------------------------------------------------


def start_span(
    connection: sqlite3.Connection,
    *,
    span_id: str,
    run_id: str,
    parent_span_id: str | None,
    name: str,
    kind: SpanKind,
    input_summary: str | None = None,
    summary_chars: int | None = None,
    now: datetime | None = None,
) -> None:
    """Record that `span_id` has begun, nested under `parent_span_id`.

    `ended_at`, `duration_ms`, `ok` and `from_cache` are left NULL: this row says the
    operation started, and `finish_span` says how it went. `input_summary` is bounded here
    rather than at the call site so no caller can put a fetched page in the trace.
    """
    with transaction(connection) as cursor:
        cursor.execute(
            f"INSERT OR REPLACE INTO spans ({_SPAN_COLUMNS})"
            " VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, NULL)",
            (
                span_id,
                run_id,
                parent_span_id,
                name,
                kind,
                _stamp(now),
                _summarise(input_summary, summary_chars),
            ),
        )


def finish_span(
    connection: sqlite3.Connection,
    *,
    span_id: str,
    ok: bool,
    error_code: str | None = None,
    from_cache: bool = False,
    output_summary: str | None = None,
    duration_ms: int | None = None,
    summary_chars: int | None = None,
    now: datetime | None = None,
) -> None:
    """Close `span_id` out: when it ended, how long it took, and whether it worked.

    `duration_ms` is **accepted when the caller has already measured it** and derived from
    the stored timestamps otherwise. `ToolRegistry.call` already times every tool and
    stamps `ToolResult.duration_ms`; a post-hook handing that number straight through
    keeps one measurement of one call, where computing a second here would produce a
    slightly different figure for the same event. A derived duration is clamped at zero,
    so a wall-clock adjustment mid-span cannot record negative time.

    An unknown `span_id` is a no-op, for the same reason `finish_run` tolerates one.
    """
    row = connection.execute(
        "SELECT started_at FROM spans WHERE span_id = ?", (span_id,)
    ).fetchone()
    if row is None:
        return

    ended_at = now or datetime.now(UTC)
    if duration_ms is None:
        elapsed = (ended_at - _parse_timestamp(row["started_at"])).total_seconds()
        duration_ms = max(0, round(elapsed * 1000))

    with transaction(connection) as cursor:
        cursor.execute(
            "UPDATE spans SET ended_at = ?, duration_ms = ?, ok = ?, error_code = ?,"
            " from_cache = ?, output_summary = ? WHERE span_id = ?",
            (
                ended_at.isoformat(),
                duration_ms,
                int(ok),
                error_code,
                int(from_cache),
                _summarise(output_summary, summary_chars),
                span_id,
            ),
        )


def get_spans(connection: sqlite3.Connection, run_id: str) -> list[SpanRecord]:
    """Every span recorded for `run_id`, oldest first.

    Ordered by `started_at` then `rowid`, because two operations can start inside the same
    millisecond and insertion order is then the only honest tiebreak — a trace whose
    ordering varies between reads cannot be read back against the run that produced it.
    """
    rows = connection.execute(
        f"SELECT {_SPAN_COLUMNS} FROM spans WHERE run_id = ?"
        " ORDER BY started_at, rowid",
        (run_id,),
    ).fetchall()
    return [_to_span(row) for row in rows]


# --- row and value conversion -------------------------------------------------------------


def _stamp(now: datetime | None) -> str:
    """The current instant as stored: ISO-8601, timezone-aware, UTC."""
    return (now or datetime.now(UTC)).isoformat()


def _summarise(value: str | None, limit: int | None) -> str | None:
    """Bound a summary to `TRACE_SUMMARY_CHARS`, marking it when it was cut.

    The ellipsis matters: an unmarked truncation reads as a tool that returned exactly
    that much, which is the sort of false detail a trace exists to avoid.
    """
    if value is None:
        return None
    ceiling = limit if limit is not None else get_settings().trace_summary_chars
    if len(value) <= ceiling:
        return value
    return value[: ceiling - 1] + "…"


def _to_run(row: sqlite3.Row) -> RunRecord:
    """Rebuild a `runs` row, timestamps back to timezone-aware UTC."""
    return RunRecord(
        run_id=row["run_id"],
        task_title=row["task_title"],
        session_minutes=row["session_minutes"],
        started_at=_parse_timestamp(row["started_at"]),
        ended_at=_optional_timestamp(row["ended_at"]),
        status=row["status"],
        hops_used=row["hops_used"],
        model_calls=row["model_calls"],
        search_calls=row["search_calls"],
        fetch_calls=row["fetch_calls"],
    )


def _to_span(row: sqlite3.Row) -> SpanRecord:
    """Rebuild a `spans` row: timestamps to aware UTC, the 0/1 columns back to bools."""
    return SpanRecord(
        span_id=row["span_id"],
        run_id=row["run_id"],
        parent_span_id=row["parent_span_id"],
        name=row["name"],
        kind=row["kind"],
        started_at=_parse_timestamp(row["started_at"]),
        ended_at=_optional_timestamp(row["ended_at"]),
        duration_ms=row["duration_ms"],
        ok=_optional_bool(row["ok"]),
        error_code=row["error_code"],
        from_cache=_optional_bool(row["from_cache"]),
        input_summary=row["input_summary"],
        output_summary=row["output_summary"],
    )


def _optional_bool(value: int | None) -> bool | None:
    """SQLite's 0/1 back to a bool, keeping NULL as `None`.

    `None` is not `False` here: an in-flight span has not failed, and collapsing the two
    would make every killed run look like a run of failures.
    """
    return None if value is None else bool(value)


def _optional_timestamp(value: str | None) -> datetime | None:
    """A nullable stored timestamp — NULL means the operation never finished."""
    return None if value is None else _parse_timestamp(value)


def _parse_timestamp(value: str) -> datetime:
    """Read a stored ISO-8601 timestamp, treating a naive one as UTC.

    Everything is written aware and in UTC; the fallback only guards a row written by hand
    or by an older build, so a comparison can never raise on mixed awareness. The third
    local copy of this — `memory/cache.py` and `memory/search_cache.py` each keep their
    own, and promoting a shared one would be an unrelated refactor of two passing modules.
    """
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
