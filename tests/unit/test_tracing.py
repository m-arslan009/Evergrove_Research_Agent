"""Tracing: run and span identity, the span tree, and what reaches SQLite.

Every test runs against a temporary database file — never `DB_PATH`. Nothing here touches a
model, the network or a real clock: `Tracer` takes an injected one, so a duration is proven
without waiting for it.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evergrove_agent.config import Settings
from evergrove_agent.memory import db
from evergrove_agent.schemas import TaskContext
from evergrove_agent.tools.base import RunBudget, RunContext
from evergrove_agent.tracing import Tracer, get_run, get_spans, store

STARTED_AT = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
TASK = TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25)


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialised database in a temporary file."""
    with db.open_database(tmp_path / "agent.sqlite3") as conn:
        yield conn


@pytest.fixture
def ctx(settings: Settings) -> RunContext:
    """A run context whose budget comes from defaults, not a developer's `.env`."""
    return RunContext(budget=RunBudget.from_settings(settings))


@pytest.fixture
def tracer(connection: sqlite3.Connection, settings: Settings) -> Tracer:
    """A tracer on a pinned clock, so every timestamp in a test is the one asserted."""
    return Tracer(connection, settings=settings, clock=lambda: STARTED_AT)


# --- identity and the tree (pure, no database) -------------------------------------------


def test_a_span_parents_the_one_it_was_opened_inside(ctx: RunContext) -> None:
    """Parent derivation, at every shape of nesting the trace tree can take.

    The highest-value assertion in this file. `parent_span_id` is what makes a trace a tree
    rather than a flat list, and a wrong derivation is invisible at the call site: every
    span still gets written, the renderer still runs, and the nesting is silently lost.
    Pins all four rules at once — top level is unparented, an inner span points at the one
    enclosing it, a sibling opened after a close points at the *outer* span again rather
    than at its finished sibling, and closing everything returns the run to the top level.
    """
    outer, outer_parent = ctx.begin_span()
    assert outer_parent is None

    inner, inner_parent = ctx.begin_span()
    assert inner_parent == outer
    assert ctx.current_span_id == inner

    ctx.end_span(inner)
    sibling, sibling_parent = ctx.begin_span()
    assert sibling_parent == outer

    ctx.end_span(sibling)
    ctx.end_span(outer)
    assert ctx.current_span_id is None
    assert ctx.begin_span()[1] is None


def test_every_span_id_is_distinct_and_the_stack_empties(ctx: RunContext) -> None:
    """Ids are unique and a closed span leaves nothing behind.

    Two regressions, both silent. A reused `span_id` makes `finish_span` update the wrong
    row — so one operation's duration and error code land on another's. A stack entry that
    is never popped mis-parents every operation for the rest of the run, which on a
    fifteen-minute run is the whole trace.
    """
    minted = []
    for _ in range(50):
        span_id, _ = ctx.begin_span()
        minted.append(span_id)
    for span_id in reversed(minted):
        ctx.end_span(span_id)

    assert len(set(minted)) == len(minted)
    assert ctx.span_stack == []


def test_closing_out_of_order_does_not_strand_the_rest_of_the_run(
    ctx: RunContext,
) -> None:
    """An unclosed inner span must not mis-parent everything that follows it.

    A raising tool or a short-circuited hook chain leaves an inner span open, and the
    failure to prevent is that one leaked entry sitting under every later operation for the
    rest of the run. Closing the outer span discards what was above it, because those can
    no longer close in order either. An unknown id is ignored rather than raising: a trace
    must never be the thing that ends a run.
    """
    outer, _ = ctx.begin_span()
    leaked, _ = ctx.begin_span()

    ctx.end_span(outer)
    assert ctx.span_stack == []
    assert ctx.begin_span()[1] is None

    ctx.end_span("span_neverseen")
    ctx.end_span(leaked)


# --- persistence -------------------------------------------------------------------------


def test_a_span_is_written_when_it_starts_and_completed_when_it_finishes(
    tracer: Tracer, connection: sqlite3.Connection, ctx: RunContext
) -> None:
    """The span round trip, and that an in-flight span is distinguishable from a failed one.

    Catches a column dropped, mis-ordered or crossed between write and read — `ok` landing
    in `from_cache` is the sort of defect that makes a trace confidently wrong. Also pins
    the two-writes design: a span that has started but not finished reads as
    `ended_at is None` with `ok is None`, which is what makes a killed run legible instead
    of absent. Timestamps must come back timezone-aware, or every later comparison raises.
    """
    span_id = tracer.open_span(ctx, "tool.fetch_url", "tool", input_summary="postgresql.org")

    in_flight = get_spans(connection, ctx.run_id)[0]
    assert (in_flight.ended_at, in_flight.duration_ms, in_flight.ok) == (None, None, None)
    assert in_flight.name == "tool.fetch_url"
    assert in_flight.kind == "tool"
    assert in_flight.input_summary == "postgresql.org"
    assert in_flight.started_at == STARTED_AT
    assert in_flight.started_at.tzinfo is not None

    tracer.close_span(
        ctx, span_id, ok=False, error_code="TIMEOUT", output_summary="no response"
    )
    cached_id = tracer.open_span(ctx, "tool.fetch_url", "tool")
    tracer.close_span(ctx, cached_id, ok=True, from_cache=True)

    failed, cached = get_spans(connection, ctx.run_id)
    assert (failed.ok, failed.error_code, failed.from_cache) == (False, "TIMEOUT", False)
    assert failed.output_summary == "no response"
    assert failed.ended_at == STARTED_AT
    assert (cached.ok, cached.error_code, cached.from_cache) == (True, None, True)


def test_duration_is_the_caller_s_when_given_and_derived_otherwise(
    connection: sqlite3.Connection, ctx: RunContext, settings: Settings
) -> None:
    """One event must not end up with two different measured durations.

    `ToolRegistry.call` already times every tool and stamps `ToolResult.duration_ms`; a
    post-hook hands that number through, and a store that recomputed it would report a
    slightly different figure for the same call. The derived path is what agent and model
    spans will use, and it is clamped: a wall-clock adjustment mid-span must not record
    negative time, which would read as an operation that finished before it began.
    """
    later = STARTED_AT + timedelta(milliseconds=1500)
    tracer = Tracer(connection, settings=settings, clock=lambda: later)
    measured = tracer.open_span(ctx, "tool.web_search", "tool")
    tracer.close_span(ctx, measured, ok=True, duration_ms=410)

    store.start_span(
        connection,
        span_id="span_derived",
        run_id=ctx.run_id,
        parent_span_id=None,
        name="researcher.loop",
        kind="agent",
        now=STARTED_AT,
    )
    store.finish_span(connection, span_id="span_derived", ok=True, now=later)

    store.start_span(
        connection,
        span_id="span_skewed",
        run_id=ctx.run_id,
        parent_span_id=None,
        name="supervisor.plan",
        kind="llm",
        now=later,
    )
    store.finish_span(connection, span_id="span_skewed", ok=True, now=STARTED_AT)

    by_id = {span.span_id: span for span in get_spans(connection, ctx.run_id)}
    assert by_id[measured].duration_ms == 410
    assert by_id["span_derived"].duration_ms == 1500
    assert by_id["span_skewed"].duration_ms == 0


def test_a_run_records_its_outcome_and_the_counters_it_spent(
    tracer: Tracer, connection: sqlite3.Connection, ctx: RunContext
) -> None:
    """The run round trip, with the four counters landing in the right columns.

    A trace that misreports what a run spent is worse than no trace: `search_calls` sitting
    in `fetch_calls` would send someone hunting a fetch problem in a run that ran out of
    searches. Also pins that the three live counters are read off `RunBudget` rather than
    recounted, and that `hops_used` is passed in because `RunState` owns it.
    """
    tracer.start_run(ctx, TASK)

    opening = get_run(connection, ctx.run_id)
    assert opening is not None
    assert opening.status == "running"
    assert opening.task_title == TASK.task_title
    assert opening.session_minutes == 25
    assert opening.ended_at is None

    assert ctx.budget.claim("search")
    assert ctx.budget.claim("fetch")
    assert ctx.budget.claim("fetch")
    assert ctx.budget.claim("model_call")
    tracer.finish_run(ctx, status="budget_exhausted", hops_used=2)

    finished = get_run(connection, ctx.run_id)
    assert finished is not None
    assert finished.status == "budget_exhausted"
    assert finished.ended_at == STARTED_AT
    assert (finished.search_calls, finished.fetch_calls) == (1, 2)
    assert (finished.model_calls, finished.hops_used) == (1, 2)


def test_a_database_failure_never_reaches_the_run(
    connection: sqlite3.Connection,
    ctx: RunContext,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A trace write that fails must log and continue, not end a fifteen-minute run.

    The plan's own Day 4 requirement, and the stance `fetch_url` already takes over its
    cache. Two things are asserted together because they fail together: nothing propagates,
    *and* the span stack still unwinds — a guard that swallowed the error but skipped the
    pop would leave every later operation nested under a span that is already over.
    """
    tracer = Tracer(connection, settings=settings, clock=lambda: STARTED_AT)
    connection.close()

    with caplog.at_level(logging.WARNING):
        tracer.start_run(ctx, TASK)
        span_id = tracer.open_span(ctx, "tool.web_search", "tool")
        tracer.close_span(ctx, span_id, ok=True)
        tracer.finish_run(ctx, status="ok", hops_used=1)

    assert ctx.span_stack == []
    assert "trace write failed" in caplog.text


def test_an_oversized_summary_is_truncated_and_marked(
    connection: sqlite3.Connection, ctx: RunContext
) -> None:
    """A span must not become a second copy of the evidence.

    Without a bound at write time, a `fetch_url` post-hook puts twenty thousand characters
    of page text in the trace — text `source_cache` already holds. The ellipsis is part of
    the behaviour: an unmarked cut reads as a tool that returned exactly that much.
    """
    tracer = Tracer(
        connection,
        settings=Settings(_env_file=None, trace_summary_chars=40),
        clock=lambda: STARTED_AT,
    )
    span_id = tracer.open_span(ctx, "tool.fetch_url", "tool", input_summary="q" * 500)
    tracer.close_span(ctx, span_id, ok=True, output_summary="short enough")

    span = get_spans(connection, ctx.run_id)[0]
    assert span.input_summary is not None
    assert len(span.input_summary) == 40
    assert span.input_summary.endswith("…")
    assert span.output_summary == "short enough"
