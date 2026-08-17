"""Session memory: what one run knew at each hop, kept on disk (plan section 12.1).

**`RunState` is the session memory the run itself uses.** It already holds the goal, every
hop's findings, the latest verdict and the derived `used_queries` / `evidence_urls` /
`fetched_urls` sets, and the loop already carries it from hop to hop — which is what makes
hop 2 build on hop 1 rather than repeat it. This module does **not** replace any of that and
must never become a second answer to "what has this run seen": nothing in the loop reads a
decision back out of SQLite.

What this module adds is **durability**. `RunState` lives in one process and dies with it, so
a finished run leaves no evidence of how it got there — and a run on this hardware takes 9-15
minutes, several of which have been killed mid-flight. `run_memory` is a per-hop projection
of `RunState`: written as the run goes, read back by a trace, an audit or a test.

Storage only, and it **raises**, on the same terms as `cache.py`, `prep_memory.py` and
`tracing/store.py`. Turning a failure into something a run can survive is
`tools/memory_tools.py`'s job.

Kept logically separate from `prep_memory` despite sharing one SQLite file: this table
belongs to exactly one `run_id` and describes hops, while `prep_memory` survives runs and is
keyed on a normalised task. Neither is ever read as a stand-in for the other.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evergrove_agent.memory.db import transaction

RunMemoryKind = Literal[
    "goal", "finding", "appraisal", "decision", "seen_url", "seen_query"
]
"""What a remembered line is about — plan section 12.2's six kinds, unchanged.

A plain `Literal`, matching `SpanKind`, `BudgetKind` and `SearchSourceType`. The set is
closed on purpose: a seventh kind means a reader has to learn a new vocabulary, and every
one of these six already has a place in the run it describes.
"""

_COLUMNS = "run_id, hop, kind, content, created_at"

_CONTENT_CHARS = 2000
"""Ceiling on one remembered line, applied at write time.

`ResearchFindings.notes` — the longest thing that becomes a `finding` — is bounded at exactly
this, so the limit costs nothing in practice and still stops a fetched page reaching the
table. Marked when it cuts, for the reason `tracing/store` marks its summaries: an unmarked
truncation reads as a hop that noted precisely that much.
"""

_TRUNCATION_MARK = "…"


class RunMemoryEntry(BaseModel):
    """One thing to remember about a hop: what kind of thing it is, and the line itself.

    Pydantic rather than a dataclass because it crosses the registry as part of the
    `record_run_memory` tool's arguments, and the registry validates a tool's input model.
    `content` is bounded generously here and clipped to `_CONTENT_CHARS` at write: a line
    slightly over the stored ceiling should be shortened, never rejected, since a rejected
    argument would cost the hop its whole mirror.
    """

    model_config = ConfigDict(extra="forbid")

    kind: RunMemoryKind
    content: str = Field(min_length=1, max_length=8000)


@dataclass(frozen=True)
class RunMemoryRecord:
    """One `run_memory` row, as stored.

    A storage row, deliberately not a Pydantic model — the same call `CachedSource` and
    `SpanRecord` make. `RunMemoryEntry` is the shape a caller *writes*; this is the shape a
    reader gets back, and it carries the row's identity and time as well.
    """

    id: int
    run_id: str
    hop: int
    """Which hop this belongs to. 0 for anything decided before the first hop ran."""
    kind: RunMemoryKind
    content: str
    created_at: datetime


def record_entries(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    hop: int,
    entries: Iterable[RunMemoryEntry],
    now: datetime | None = None,
) -> int:
    """Write one hop's worth of memory, returning how many rows were written.

    One transaction for the batch: a hop's mirror is one fact about the run, and half a hop
    on disk would be worse than none. Blank lines are skipped and identical `(kind, content)`
    pairs within the batch are written once — a caller projecting `RunState` should not have
    to dedupe first, and a repeated line says nothing a single one does not.

    Rows are appended, never replaced. Re-mirroring a hop therefore duplicates it; the loop
    calls this once per hop, and the readback helpers answer with sets, so a duplicate is
    harmless rather than something worth a uniqueness constraint the writer would have to
    reason about.
    """
    stamp = (now or datetime.now(UTC)).isoformat()

    rows: list[tuple[str, int, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        content = _clip(entry.content)
        if not content:
            continue
        key = (entry.kind, content)
        if key in seen:
            continue
        seen.add(key)
        rows.append((run_id, hop, entry.kind, content, stamp))

    if not rows:
        return 0

    with transaction(connection) as cursor:
        cursor.executemany(
            f"INSERT INTO run_memory ({_COLUMNS}) VALUES (?, ?, ?, ?, ?)", rows
        )
    return len(rows)


def get_run_memory(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    kind: RunMemoryKind | None = None,
) -> list[RunMemoryRecord]:
    """Everything remembered for `run_id`, oldest first, optionally of one kind only.

    Ordered by `id` rather than `created_at`: a whole hop is stamped with one instant, so
    insertion order is the only honest tiebreak — the same reason `get_spans` falls back to
    `rowid`.
    """
    sql = f"SELECT id, {_COLUMNS} FROM run_memory WHERE run_id = ?"
    parameters: tuple[object, ...] = (run_id,)
    if kind is not None:
        sql += " AND kind = ?"
        parameters += (kind,)

    rows = connection.execute(sql + " ORDER BY id", parameters).fetchall()
    return [_to_record(row) for row in rows]


def seen_queries(connection: sqlite3.Connection, run_id: str) -> set[str]:
    """Every query this run has already spent, as recorded on disk.

    The durable counterpart to `RunState.used_queries`, and the reason a repeat is
    recognisable after the process that issued it is gone. The loop still avoids repeats
    through `RunState` — reading them back from here to make a decision would be the second
    state mechanism this module exists not to be.
    """
    return _contents(connection, run_id, "seen_query")


def seen_urls(connection: sqlite3.Connection, run_id: str) -> set[str]:
    """Every URL this run has already gathered or read, as recorded on disk.

    The durable counterpart to `RunState.evidence_urls`. Same reading as `seen_queries`:
    evidence for an audit or a test, not an input to a decision.
    """
    return _contents(connection, run_id, "seen_url")


def _contents(
    connection: sqlite3.Connection, run_id: str, kind: RunMemoryKind
) -> set[str]:
    """The `content` of every row of one kind, as a set — duplicates collapse by definition."""
    rows = connection.execute(
        "SELECT content FROM run_memory WHERE run_id = ? AND kind = ?",
        (run_id, kind),
    ).fetchall()
    return {row["content"] for row in rows}


def _clip(content: str) -> str:
    """Bound one line to `_CONTENT_CHARS`, marking it when it was cut."""
    text = content.strip()
    if len(text) <= _CONTENT_CHARS:
        return text
    return text[: _CONTENT_CHARS - 1] + _TRUNCATION_MARK


def _to_record(row: sqlite3.Row) -> RunMemoryRecord:
    """Rebuild a `run_memory` row, its timestamp back to timezone-aware UTC."""
    return RunMemoryRecord(
        id=row["id"],
        run_id=row["run_id"],
        hop=row["hop"],
        kind=row["kind"],
        content=row["content"],
        created_at=_parse_timestamp(row["created_at"]),
    )


def _parse_timestamp(value: str) -> datetime:
    """Read a stored ISO-8601 timestamp, treating a naive one as UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def entries_from(
    *,
    goal: str = "",
    decision: str = "",
    findings: str = "",
    appraisal: str = "",
    queries: Sequence[str] = (),
    urls: Sequence[str] = (),
) -> list[RunMemoryEntry]:
    """Build a hop's entries from the pieces a caller already has.

    Here rather than in the loop so the six kinds have one construction site and a caller
    never has to remember which string is which `kind`. Empty pieces are dropped, so a hop
    with no verdict simply records no `appraisal` row instead of an empty one.

    It takes plain strings rather than a `RunState`: this package must not decide how the
    loop's own vocabulary reads, and rendering `ResearchFindings` into a line is the caller's
    business — the same division that keeps `prompt_context.py` out of `memory/`.

    Every piece goes through the same `_clip` the writer applies, so a caller handing over an
    over-long line gets it shortened rather than a `ValidationError` — this is a best-effort
    mirror, and it must not be the thing that raises inside a run.
    """
    pieces: list[tuple[RunMemoryKind, str]] = [
        ("goal", goal),
        ("decision", decision),
        ("finding", findings),
        ("appraisal", appraisal),
        *(("seen_query", query) for query in queries),
        *(("seen_url", url) for url in urls),
    ]
    return [
        RunMemoryEntry(kind=kind, content=clipped)
        for kind, value in pieces
        if (clipped := _clip(value))
    ]
