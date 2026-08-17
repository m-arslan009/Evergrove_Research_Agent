"""The registry's hooks: what a tool call costs, and what it leaves behind (Day 4 T2/T3).

Everything here goes through `ToolRegistry.call` against stub tools, because the hooks are
what is under test and a real tool would only add a network or a disk to the picture. The
stubs are *named* after real tools (`web_search`, `fetch_url`, `read_document`), since
`TOOL_BUDGET` maps names to counters and a stub called something else would prove the
mapping works for a tool that does not exist.

Not re-proven here: span id minting, parent derivation and the stack's tolerance of an
out-of-order close (`test_tracing.py`), the ledger's arithmetic (`test_run_budget.py`), or
the registry's resolve/validate behaviour (`test_tool_registry.py`). This file is only
about what the two hook chains add on top of them.

Offline: a temporary SQLite file, no model, no network.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from evergrove_agent.config import Settings
from evergrove_agent.memory import db
from evergrove_agent.schemas import ErrorCode, ToolError, ToolResult
from evergrove_agent.tools.base import RunBudget, RunContext
from evergrove_agent.tools.hooks import install_registry_hooks
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tracing import SpanRecord, Tracer, get_spans

BUDGETED = [("web_search", "search"), ("fetch_url", "fetch")]
"""The mapping `TOOL_BUDGET` declares, as test cases. A tool added to that table without a
case here is a counter nothing proves is enforced."""


class StubInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = "postgresql indexing"


class StubOutput(BaseModel):
    echoed: str


class StubTool:
    """A tool that does nothing but record that it ran, and end however a test needs.

    `outcome` covers the four endings the post-hook has to record differently: a success, a
    success answered from a cache, a contract-abiding failure, and a tool that breaks the
    contract by raising.
    """

    description = "Stands in for a real tool."
    input_model = StubInput
    output_model = StubOutput

    def __init__(
        self,
        name: str,
        *,
        outcome: str = "ok",
        observer: Callable[[RunContext], None] | None = None,
    ) -> None:
        self.name = name
        self.calls: list[StubInput] = []
        self._outcome = outcome
        self._observer = observer

    async def run(self, args: StubInput, ctx: RunContext) -> ToolResult[Any]:
        self.calls.append(args)
        if self._observer is not None:
            self._observer(ctx)
        if self._outcome == "raise":
            raise RuntimeError("disk on fire")
        if self._outcome == "fail":
            return ToolResult(
                ok=False,
                error=ToolError(
                    code=ErrorCode.SEARCH_UNAVAILABLE,
                    message="the backend is unreachable",
                    retryable=True,
                ),
                duration_ms=0,
            )
        return ToolResult(
            ok=True,
            data=StubOutput(echoed=args.query),
            duration_ms=0,
            from_cache=self._outcome == "cached",
        )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialised database in a temporary file — never `DB_PATH`."""
    with db.open_database(tmp_path / "agent.sqlite3") as conn:
        yield conn


@pytest.fixture
def tracer(connection: sqlite3.Connection, settings: Settings) -> Tracer:
    return Tracer(connection, settings=settings)


@pytest.fixture
def ctx(settings: Settings) -> RunContext:
    """Two of everything: enough to prove a limit is reached rather than never tested."""
    return RunContext(
        budget=RunBudget.from_settings(
            Settings(_env_file=None, max_search_calls=2, max_fetch_calls=2)
        )
    )


@pytest.fixture
def wire(tracer: Tracer) -> Callable[..., ToolRegistry]:
    """A registry holding the given tools, with both hook chains installed."""

    def _wire(*tools: Any) -> ToolRegistry:
        registry = ToolRegistry()
        for tool in tools:
            registry.register(tool)
        install_registry_hooks(registry, tracer=tracer)
        return registry

    return _wire


def spans_for(connection: sqlite3.Connection, ctx: RunContext) -> list[SpanRecord]:
    return get_spans(connection, ctx.run_id)


# --- the span is opened before the tool and closed after it ---------------------------------


async def test_the_span_is_open_while_the_tool_runs_and_closed_afterwards(
    connection: sqlite3.Connection, ctx: RunContext, wire: Callable[..., ToolRegistry]
) -> None:
    """Ordering is the whole point of a pre/post pair, and both mistakes are silent.

    A pre-hook that opened the span *after* the tool would still produce a complete-looking
    row — with a start time that postdates the work it describes. A post-hook that never
    closed one would leave every tool call reading "never finished". Observed from inside
    the tool, which is the only vantage point that can tell the two apart.
    """
    seen: list[str | None] = []
    tool = StubTool("read_document", observer=lambda c: seen.append(c.current_span_id))

    result = await wire(tool).call("read_document", {}, ctx)

    assert result.ok
    assert seen == [spans_for(connection, ctx)[0].span_id], (
        "the tool must run inside its own span, not before or after it"
    )
    assert ctx.span_stack == []
    assert spans_for(connection, ctx)[0].ended_at is not None


async def test_a_tool_span_nests_under_the_operation_that_was_active(
    connection: sqlite3.Connection, ctx: RunContext, wire: Callable[..., ToolRegistry]
) -> None:
    """Tool spans have to hang off whatever opened before them, or the trace is a flat list.

    Nothing else opens a span during a run *yet*, so without this the parenting the agent
    and model spans will depend on is untested until the day they arrive — and a hook that
    derived its own parent instead of using `RunContext` would look correct until then.
    """
    outer, _ = ctx.begin_span()

    await wire(StubTool("read_document")).call("read_document", {}, ctx)

    assert spans_for(connection, ctx)[0].parent_span_id == outer
    assert ctx.current_span_id == outer, "the outer operation is active again"


async def test_the_span_records_the_call_the_registry_actually_made(
    connection: sqlite3.Connection, ctx: RunContext, wire: Callable[..., ToolRegistry]
) -> None:
    """One row per call, carrying the registry's own measurement.

    `duration_ms` is asserted equal to the result's rather than merely present: the store
    will happily derive a duration from its own timestamps, so a post-hook that forgot to
    pass the measured one produces a plausible number for a different interval.
    """
    registry = wire(StubTool("read_document"))

    result = await registry.call("read_document", {"query": "b-tree"}, ctx)

    (span,) = spans_for(connection, ctx)
    assert (span.name, span.kind, span.ok) == ("read_document", "tool", True)
    assert span.duration_ms == result.duration_ms
    assert span.error_code is None
    assert span.from_cache is False
    assert "b-tree" in (span.input_summary or "")


# --- the budget ------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "kind"), BUDGETED)
async def test_a_run_pays_for_two_calls_and_the_third_is_refused(
    ctx: RunContext, wire: Callable[..., ToolRegistry], name: str, kind: str
) -> None:
    """The regression the whole subtask exists for: a limit that is reported, not enforced.

    The third call must come back as a readable `BUDGET_EXCEEDED` result *and* leave the
    tool untouched — a refusal the tool runs anyway is not a budget, and one that raises
    instead of returning would end the run rather than degrade it.
    """
    tool = StubTool(name)
    registry = wire(tool)

    results = [await registry.call(name, {}, ctx) for _ in range(3)]

    assert [result.ok for result in results] == [True, True, False]
    assert results[-1].error.code is ErrorCode.BUDGET_EXCEEDED
    assert results[-1].error.retryable is False
    assert len(tool.calls) == 2, "the refused call must not reach the tool"
    assert ctx.budget.remaining(kind) == 0


async def test_a_tool_with_no_counter_is_never_charged(
    ctx: RunContext, wire: Callable[..., ToolRegistry]
) -> None:
    """`read_document` reads local disk. A run whose search budget drains while it reads an
    attachment has a budget that measures the wrong thing."""
    registry = wire(StubTool("read_document"))

    for _ in range(5):
        assert (await registry.call("read_document", {}, ctx)).ok

    assert ctx.budget.searches_used == 0
    assert ctx.budget.fetches_used == 0


async def test_a_refused_call_is_still_a_closed_span(
    connection: sqlite3.Connection, ctx: RunContext, wire: Callable[..., ToolRegistry]
) -> None:
    """A refusal that leaves no trace is missing from exactly the run someone is reading.

    This is what fixes the hook order: the tracing pre-hook has to be installed before the
    budget pre-hook, or the short-circuit happens first and the call disappears entirely.
    """
    registry = wire(StubTool("web_search"))
    for _ in range(3):
        await registry.call("web_search", {}, ctx)

    refused = spans_for(connection, ctx)[-1]
    assert refused.ok is False
    assert refused.error_code == "BUDGET_EXCEEDED"
    assert refused.ended_at is not None
    assert len(spans_for(connection, ctx)) == 3, "a refused call is a span like any other"


# --- what the outcome was ----------------------------------------------------------------------


async def test_a_cache_hit_is_recorded_on_the_span(
    connection: sqlite3.Connection, ctx: RunContext, wire: Callable[..., ToolRegistry]
) -> None:
    """`from_cache` is how a trace explains a fetch that took 3ms. Only the tool knows it,
    so it has to travel from the result onto the row rather than being inferred."""
    await wire(StubTool("fetch_url", outcome="cached")).call("fetch_url", {}, ctx)

    assert spans_for(connection, ctx)[0].from_cache is True


@pytest.mark.parametrize(
    ("outcome", "error_code"),
    [("fail", "SEARCH_UNAVAILABLE"), ("raise", "UNKNOWN")],
)
async def test_a_failed_call_closes_its_span_with_the_reason(
    connection: sqlite3.Connection,
    ctx: RunContext,
    wire: Callable[..., ToolRegistry],
    outcome: str,
    error_code: str,
) -> None:
    """Both ways a call can go wrong have to end as a closed, explained span.

    A tool that returns a failure is the contract. A tool that *raises* breaks it — and
    used to be the one path that skipped the post-hooks entirely, leaving its span open
    forever precisely when the trace was most wanted.
    """
    result = await wire(StubTool("read_document", outcome=outcome)).call(
        "read_document", {}, ctx
    )

    (span,) = spans_for(connection, ctx)
    assert result.ok is False
    assert span.ok is False
    assert span.error_code == error_code
    assert span.ended_at is not None


# --- the JSON log line (T6) -----------------------------------------------------------------


def logged_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """Every JSON trace line the hooks emitted, parsed."""
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "evergrove_agent.trace"
    ]


async def test_one_tool_call_logs_one_json_line_that_matches_its_span(
    connection: sqlite3.Connection,
    ctx: RunContext,
    wire: Callable[..., ToolRegistry],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The line and the row are one observation, so they must not disagree.

    The regression this catches is the whole reason both are emitted from the same
    post-hook: a log line that re-derives `duration_ms`, re-reads `from_cache`, or measures
    its own timing would drift from the span row it is supposed to mirror, and someone
    debugging a run would have two sources telling them different things. Pinned against
    the stored span rather than against literals, so the two cannot drift apart silently.
    """
    with caplog.at_level(logging.INFO, logger="evergrove_agent.trace"):
        result = await wire(
            StubTool("fetch_url", outcome="fail")
        ).call("fetch_url", {}, ctx)

    (line,) = logged_lines(caplog)
    (span,) = spans_for(connection, ctx)

    assert line["event"] == "tool_call"
    assert line["run_id"] == ctx.run_id
    assert line["span_id"] == span.span_id
    assert line["tool"] == "fetch_url"
    assert line["ok"] is result.ok is span.ok is False
    assert line["error_code"] == span.error_code == "SEARCH_UNAVAILABLE"
    assert line["duration_ms"] == span.duration_ms == result.duration_ms
    assert line["from_cache"] == span.from_cache is False


async def test_a_call_with_no_tracer_still_logs_and_still_pays(
    ctx: RunContext, caplog: pytest.LogCaptureFixture
) -> None:
    """No database is not the same as no record, and never an unenforced budget.

    T6 made `TracingHooks` unconditional precisely so a run whose database could not be
    opened still says what it did. Two things have to hold in that configuration and each
    is silent when broken: the line is emitted with `span_id: null` (rather than the hook
    being skipped entirely, or raising on a `None` tracer), and `enforce_run_budget` still
    refuses the third of two allowed searches.
    """
    registry = ToolRegistry()
    registry.register(StubTool("web_search"))
    install_registry_hooks(registry, tracer=None)

    with caplog.at_level(logging.INFO, logger="evergrove_agent.trace"):
        for _ in range(3):
            await registry.call("web_search", {}, ctx)

    lines = logged_lines(caplog)
    assert [line["span_id"] for line in lines] == [None, None, None]
    assert [line["ok"] for line in lines] == [True, True, False]
    assert lines[-1]["error_code"] == "BUDGET_EXCEEDED"
