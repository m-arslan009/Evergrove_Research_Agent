"""The small API a hook calls: start a run, start a span, finish either.

Plan section 13. Two jobs, and only these two:

1. **Coordinate identity with persistence.** `RunContext` mints span ids and knows what
   nests under what; `tracing/store.py` writes rows. Something has to do both in the right
   order, and leaving that to each call site is how a trace tree ends up flat.
2. **Keep a tracing failure from ending a research run.** Every write here is guarded:
   a `sqlite3.Error` is logged at WARNING and swallowed. The same stance
   `tools/fetch_url.py` takes over its cache — a diagnostic that can kill a fifteen-minute
   run is worse than no diagnostic. `store.py` deliberately still raises, so the guard
   lives in exactly one place and a test can see the real error.

**Start and finish are separate methods, not a context manager.** A registry pre-hook and
post-hook are two independent callables with no shared frame: the pre-hook calls
`open_span` and the post-hook calls `close_span`. A context manager cannot span the two,
which is why the primitive is a pair. (A convenience manager for the agent and model spans
that *are* opened and closed in one frame belongs with the work that adds them.)

Nothing here decides *what* to trace. Which operations get a span, what they are named and
what a summary contains is the caller's business; this module records what it is told.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from evergrove_agent.config import Settings, get_settings
from evergrove_agent.schemas import TaskContext
from evergrove_agent.tools.base import RunContext
from evergrove_agent.tracing.store import (
    RunStatus,
    SpanKind,
    finish_run,
    finish_span,
    start_run,
    start_span,
)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Wall-clock UTC, injected so a test can pin a duration without waiting for it."""
    return datetime.now(UTC)


class Tracer:
    """Writes one run's trace, given a connection to write it through.

    Takes a caller-supplied connection rather than opening its own, the same way
    `memory/cache.py` does: `db.connect` / `db.open_database` stays the single way into the
    file, and whoever owns the run owns the connection's lifetime.

    Wall-clock timestamps, not the monotonic clock `RunBudget` uses. A budget answers "may
    this run spend one more", where a clock adjustment must not hand out extra allowance; a
    trace answers "when did this happen", which has to be a real instant someone can line
    up against a log line.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._connection = connection
        self._clock = clock
        self._summary_chars = (settings or get_settings()).trace_summary_chars
        """Resolved once at construction, not per span: a run's trace must not change
        shape halfway through, and it honours a caller's `settings` override the same way
        `RunBudget` snapshots its limits."""

    # --- runs -----------------------------------------------------------------------------

    def start_run(self, ctx: RunContext, task: TaskContext) -> None:
        """Record that `ctx.run_id` has begun work on `task`."""
        self._guard(
            "start run",
            ctx.run_id,
            lambda: start_run(
                self._connection,
                run_id=ctx.run_id,
                task_title=task.task_title,
                session_minutes=task.session_minutes,
                now=self._clock(),
            ),
        )

    def finish_run(
        self, ctx: RunContext, *, status: RunStatus, hops_used: int
    ) -> None:
        """Close the run out, snapshotting the three counters off its own ledger.

        `hops_used` is passed in because `RunState` owns that count, not `RunBudget` —
        the same division `RunBudget.hops_remaining` already rests on. The other three come
        straight from `ctx.budget`, which is the live ledger this is a snapshot of; reading
        them here is what keeps a second copy of them from existing.
        """
        budget = ctx.budget
        self._guard(
            "finish run",
            ctx.run_id,
            lambda: finish_run(
                self._connection,
                run_id=ctx.run_id,
                status=status,
                hops_used=hops_used,
                model_calls=budget.model_calls_used,
                search_calls=budget.searches_used,
                fetch_calls=budget.fetches_used,
                now=self._clock(),
            ),
        )

    # --- spans ----------------------------------------------------------------------------

    def open_span(
        self,
        ctx: RunContext,
        name: str,
        kind: SpanKind,
        *,
        input_summary: str | None = None,
    ) -> str:
        """Begin a span for `name` and return its id, for `close_span` to finish.

        The id is minted and pushed onto `ctx` **before** the row is written, so a failed
        write cannot desynchronise the stack from the trace: the returned id is always
        valid, always closeable, and anything opened in the meantime still nests under it.
        A span whose insert failed simply has no row, and `close_span` finds nothing to
        update.
        """
        span_id, parent_span_id = ctx.begin_span()
        self._guard(
            "start span",
            span_id,
            lambda: start_span(
                self._connection,
                span_id=span_id,
                run_id=ctx.run_id,
                parent_span_id=parent_span_id,
                name=name,
                kind=kind,
                input_summary=input_summary,
                summary_chars=self._summary_chars,
                now=self._clock(),
            ),
        )
        return span_id

    def close_span(
        self,
        ctx: RunContext,
        span_id: str,
        *,
        ok: bool,
        error_code: str | None = None,
        from_cache: bool = False,
        output_summary: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Finish `span_id`, and make whatever contained it active again.

        The stack is popped **first and unconditionally**, so a failed write leaves the
        nesting correct for the rest of the run rather than trapping every later operation
        underneath a span that is already over.

        Pass `duration_ms` when the caller already measured the call —
        `ToolResult.duration_ms` is exactly that number, timed centrally by
        `ToolRegistry.call`. Omitted, the store derives it from the row's own timestamps.
        """
        ctx.end_span(span_id)
        self._guard(
            "finish span",
            span_id,
            lambda: finish_span(
                self._connection,
                span_id=span_id,
                ok=ok,
                error_code=error_code,
                from_cache=from_cache,
                output_summary=output_summary,
                duration_ms=duration_ms,
                summary_chars=self._summary_chars,
                now=self._clock(),
            ),
        )

    # --- the one guard --------------------------------------------------------------------

    def _guard(self, what: str, identifier: str, write: Callable[[], None]) -> None:
        """Run one trace write, turning a database failure into a log line.

        Only `sqlite3.Error` is caught. A `TypeError` or a bad `SpanKind` is a bug in the
        caller and must surface as one — swallowing everything would make a trace that is
        silently never written look exactly like a trace that works.
        """
        try:
            write()
        except sqlite3.Error as exc:
            logger.warning(
                "trace write failed (%s %s): %s: %s; the run continues untraced",
                what,
                identifier,
                type(exc).__name__,
                exc,
            )
