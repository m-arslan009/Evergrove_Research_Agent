"""The three memory tools: recall a previous preparation, save one, mirror a hop.

Plan section 23 T4/T5. `memory/prep_memory.py` and `memory/run_memory.py` do the storage and
raise on a `sqlite3.Error`; this module is where that becomes something a research run can
survive. It is the exact division `tracing/store.py` and `tracing/tracer.py` already use, and
it exists for the same reason: **memory is an enhancement, and a memory outage must never fail
a run.** A run on this hardware takes 9-15 minutes, and losing one to a diagnostic write would
be a worse failure than having no memory at all.

**A storage failure is a `ToolResult` carrying `ErrorCode.UNKNOWN`, not a silent success.**
The plan's wording for `recall_previous_preparation` is "returns found=false on any failure",
which would make "we have never prepared this" and "the database is broken" the same answer,
and would make this the only tool in the project that reports success after a failed write.
The guarantee that actually matters is kept: nothing here raises, a `ToolResult` never ends a
run, and the caller treats *anything* other than `found=true` as "no memory to work from".
The difference is that the trace and the log can still tell a human which one happened.

**Registered, never advertised.** These are pipeline tools, on the same terms as
`normalize_sources` and `validate_report`: `advertised_tool_names` does not list them, so the
model cannot reach one, and whether a preparation is remembered is not the model's call.
Recall happens once at the start of a run and a save happens once after validation — both
decided by our own code (plan section 12.2), which is also the only way "never save an invalid
preparation" can be structurally true rather than a prompt instruction.

None of them costs budget: `TOOL_BUDGET` has no entry for a local SQLite write, and a name
absent from that map is free.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict, Field

from evergrove_agent.config import Settings, get_settings
from evergrove_agent.memory import prep_memory, run_memory
from evergrove_agent.memory.db import open_database
from evergrove_agent.memory.run_memory import RunMemoryEntry
from evergrove_agent.schemas import (
    ErrorCode,
    FocusPreparationReport,
    PreviousPreparation,
    ToolError,
    ToolResult,
)
from evergrove_agent.tools.base import RunContext

logger = logging.getLogger(__name__)


# --- the shared connection seam -------------------------------------------------------------


class _MemoryTool:
    """The database seam every memory tool takes, and the guard around it.

    `settings` and `connection` are the same injection seams `FetchUrlTool` and
    `WebSearchTool` take: given no connection, each call opens and closes its own; given the
    run's connection by `wiring.py`, every call shares it for the run's lifetime.

    A base class rather than three copies because the *stance* is what has to be identical:
    all three tools degrade the same way, log at the same level, and never raise. Three
    hand-written `try` blocks would drift, and the one that drifted would be the one that
    ends a run.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._connection = connection

    @contextmanager
    def _connection_scope(self) -> Iterator[sqlite3.Connection]:
        """The injected connection, or one opened and closed for this call."""
        if self._connection is not None:
            yield self._connection
            return
        with open_database(self._settings.db_path) as connection:
            yield connection

    def _unavailable(self, action: str, exc: Exception) -> ToolResult[None]:
        """A storage failure, as the one answer all three tools give it.

        `retryable=False`: the disk or the schema is the problem, and asking again inside the
        same run would spend a turn learning that twice. `OSError` is caught beside
        `sqlite3.Error` because `connect` creates `DB_PATH`'s parent directory, so an
        unwritable path fails before SQLite is reached — the same pair `service.py` catches.
        """
        logger.warning("memory unavailable while %s (%s: %s)", action, type(exc).__name__, exc)
        return ToolResult(
            ok=False,
            error=ToolError(
                code=ErrorCode.UNKNOWN,
                message=f"memory is unavailable, so {action} did not happen.",
                retryable=False,
            ),
            duration_ms=0,
        )


# --- recall_previous_preparation -------------------------------------------------------------


class RecallInput(BaseModel):
    """The task to look for, and how far back to look."""

    model_config = ConfigDict(extra="forbid")

    task_title: str = Field(min_length=1, max_length=500)
    """Matched on `normalize_task_key(task_title)`, so "Continue PostgreSQL indexing" finds
    what "Learn PostgreSQL indexing" prepared."""
    max_age_days: int | None = Field(default=None, ge=1)
    """`None` means `MEMORY_RECALL_MAX_AGE_DAYS` (30). Present so a caller — or an
    evaluation — can narrow the window without changing configuration."""


class RecallOutput(BaseModel):
    """Whether a recent preparation exists for this task, and what it was.

    `found` is redundant against `previous is not None` and is kept anyway: it is the field
    the plan's Evaluation 5 reads off the trace, and a boolean in a span summary is legible
    where a nested object is not.
    """

    model_config = ConfigDict(extra="forbid")

    found: bool = False
    previous: PreviousPreparation | None = None


class RecallPreviousPreparationTool(_MemoryTool):
    """Look up what a previous run prepared for this task."""

    name = "recall_previous_preparation"
    description = (
        "Look up the most recent validated preparation for a task, so a session can "
        "continue previous work instead of repeating it. Pipeline step; not offered to "
        "the model."
    )
    input_model = RecallInput
    output_model = RecallOutput

    async def run(self, args: RecallInput, ctx: RunContext) -> ToolResult[RecallOutput]:
        """`found=False` when nothing recent matches — a clear answer, not a failure.

        Nothing to recall is the normal state of a first run, so it is `ok=True` with an
        empty payload. Only a broken database is a failing `ToolResult`.
        """
        try:
            with self._connection_scope() as connection:
                previous = prep_memory.recall_previous_preparation(
                    connection,
                    task_title=args.task_title,
                    max_age_days=args.max_age_days,
                )
        except (sqlite3.Error, OSError) as exc:
            return self._unavailable("recalling previous preparation", exc)

        return ToolResult(
            ok=True,
            data=RecallOutput(found=previous is not None, previous=previous),
            duration_ms=0,
        )


# --- save_preparation --------------------------------------------------------------------------


class SavePreparationInput(BaseModel):
    """A finished report to remember."""

    model_config = ConfigDict(extra="forbid")

    report: FocusPreparationReport
    """**Must already have passed `validate_report`.** The rule lives at the call site: the
    loop saves the report `finalise()` returned, and `finalise()` either returns a validated
    report or raises. Re-checking grounding here would be a second definition of S9's rule,
    and this tool has none of the evidence that check needs."""


class SavePreparationOutput(BaseModel):
    """What was stored, and under which key."""

    model_config = ConfigDict(extra="forbid")

    saved: bool = False
    run_id: str = ""
    task_key: str = ""
    """The normalised key the preparation is now findable under. Returned so a trace shows
    *why* a later run's recall did or did not match."""


class SavePreparationTool(_MemoryTool):
    """Remember a validated preparation for a future session."""

    name = "save_preparation"
    description = (
        "Store a compact summary of a validated focus preparation so a later session for "
        "the same task can continue it. Pipeline step; not offered to the model."
    )
    input_model = SavePreparationInput
    output_model = SavePreparationOutput

    async def run(
        self, args: SavePreparationInput, ctx: RunContext
    ) -> ToolResult[SavePreparationOutput]:
        """Write the summary, or report that memory was unavailable. Never raises."""
        try:
            with self._connection_scope() as connection:
                entry = prep_memory.save_preparation(connection, report=args.report)
        except (sqlite3.Error, OSError) as exc:
            return self._unavailable("saving this preparation", exc)

        return ToolResult(
            ok=True,
            data=SavePreparationOutput(
                saved=True, run_id=entry.run_id, task_key=entry.task_key
            ),
            duration_ms=0,
        )


# --- record_run_memory --------------------------------------------------------------------------


class RecordRunMemoryInput(BaseModel):
    """One hop's worth of session memory to write down."""

    model_config = ConfigDict(extra="forbid")

    hop: int = Field(default=0, ge=0, le=3)
    """Which hop these lines belong to; 0 for anything decided before the first hop ran.
    Bounded as `RunState.hop` is, so a mirror cannot claim a hop the run cannot have had."""
    entries: list[RunMemoryEntry] = Field(default_factory=list, max_length=200)
    """Built by `memory.entries_from`, which is the one place a string becomes a `kind`.
    Bounded because a hop keeps at most a few dozen leads and queries, and an unbounded list
    would let one call write a page of rows."""


class RecordRunMemoryOutput(BaseModel):
    """How many rows the write produced."""

    model_config = ConfigDict(extra="forbid")

    written: int = Field(default=0, ge=0)
    """Fewer than `len(entries)` when blanks or duplicates were folded — the writer's
    business, reported here so a trace shows what was actually kept."""


class RecordRunMemoryTool(_MemoryTool):
    """Mirror this run's own progress into `run_memory`."""

    name = "record_run_memory"
    description = (
        "Record what this run has decided, gathered and judged at one hop, so the run's "
        "own history survives the process. Pipeline step; not offered to the model."
    )
    input_model = RecordRunMemoryInput
    output_model = RecordRunMemoryOutput

    async def run(
        self, args: RecordRunMemoryInput, ctx: RunContext
    ) -> ToolResult[RecordRunMemoryOutput]:
        """The run id comes from `ctx`, never from the arguments.

        A tool that could be told which run it belongs to could write one run's memory under
        another's id, and `RunContext` is already the single answer to "which run is this" for
        the budget and the trace.
        """
        try:
            with self._connection_scope() as connection:
                written = run_memory.record_entries(
                    connection,
                    run_id=ctx.run_id,
                    hop=args.hop,
                    entries=args.entries,
                )
        except (sqlite3.Error, OSError) as exc:
            return self._unavailable("recording this run's memory", exc)

        return ToolResult(
            ok=True, data=RecordRunMemoryOutput(written=written), duration_ms=0
        )
