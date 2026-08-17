"""What the registry does around every tool call: trace it, and make it pay (Day 4 T2/T3).

Plan sections 13 and 14.4. `ToolRegistry` has held two empty hook lists since Day 1 for
exactly this, so tracing and budget enforcement arrive as two callables added to those
lists rather than as an edit to seven tools. Nothing below is reachable from a tool: a tool
returns its `ToolResult` and never learns that either of these ran.

**The order the hooks are installed in is the behaviour.** `install_registry_hooks` is the
one place it is decided, because it is not obvious and it is not local:

1. the tracing pre-hook opens the span, so a call that is about to be *refused* is still a
   span — a refusal that leaves no trace is missing from precisely the run someone is
   trying to explain;
2. `enforce_run_budget` claims the counter, and returns a `ToolResult` when it cannot,
   which short-circuits the tool;
3. the tracing post-hook closes the span over whichever result came back — the tool's, the
   refusal, or the registry's own failure.

**Neither hook can change a result.** The budget hook only ever *replaces* a call it
refused before it happened; the tracing hooks return `None` throughout. A trace that could
rewrite what a tool answered would be a trace nobody could trust.

**Two sinks, one observation (T6).** Plan section 13 specifies the post-hook trace write as
"one row into `spans`, plus one structured JSON log line". Both are emitted from
`TracingHooks.after`, from the same values, so a line and the row it mirrors cannot
disagree. They differ in one way only: the row needs a database and the line does not, so
the line is emitted for **every** tool call while the row appears only when a `Tracer` was
supplied. A run whose database could not be opened still leaves a record of what it did.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from evergrove_agent.schemas import ErrorCode, ToolError, ToolResult
from evergrove_agent.tools.base import BudgetKind, ToolInvocation
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tracing.tracer import Tracer

trace_logger = logging.getLogger("evergrove_agent.trace")
"""Where the structured JSON lines go.

A logger of its own, not `__name__`, and deliberately **not** a `print`. Plan section 13
says "to stdout", but `main.py` documents stdout as the report and nothing else — "a piped
or redirected report is byte-identical to a silent run" — so a line printed there would
corrupt the one output this project promises to keep clean. Section 13's own choice of
mechanism resolves it: "stdlib JSON logging", where the stream is a handler's business
rather than a hard-coded destination. `main.py --trace-log` attaches one on stderr; a run
with no handler emits nothing, which is why this is silent by default.
"""

TOOL_BUDGET: dict[str, BudgetKind] = {
    "web_search": "search",
    "fetch_url": "fetch",
}
"""Which tools cost which counter. Everything absent is free.

`read_document` reads local disk, and `normalize_sources` / `validate_report` are pipeline
steps — none of them spends anything a limit exists to protect. Lifted here from the Day 3
loop, unchanged: the mapping was written in one piece precisely so this move was a move
rather than a rewrite.
"""

_BUDGET_NOUNS: dict[BudgetKind, str] = {
    "search": "searches",
    "fetch": "page reads",
    "model_call": "model calls",
}
"""What each counter is called in the sentence the model reads. Wording carried over from
the loop verbatim — a refusal the model already knows how to recover from should not start
reading differently because enforcement moved."""


async def enforce_run_budget(invocation: ToolInvocation) -> ToolResult[Any] | None:
    """Pay for a tool call before it runs, or refuse it as a result the model can read.

    The pre-hook half of T3. `None` means paid and the registry proceeds; a `ToolResult`
    means the tool is never reached, which is what makes the count honest — claiming after
    the call would let a call that timed out go uncounted.

    Falls through to `RunBudget.claim`, still the single enforcement point (S4). The
    `False` → `ToolResult(BUDGET_EXCEEDED)` lift lives here rather than in `RunBudget`, so
    the ledger stays free of `ErrorCode` and prompt wording.

    Running *inside* the registry rather than before it is what removes the Day 3
    over-count: arguments are already validated by the time this is called, so a malformed
    call is rejected as `BAD_ARGUMENTS` without ever charging the run for it.
    """
    kind = TOOL_BUDGET.get(invocation.tool.name)
    if kind is None or invocation.ctx.budget.claim(kind):
        return None
    return ToolResult(
        ok=False,
        error=ToolError(
            code=ErrorCode.BUDGET_EXCEEDED,
            message=(
                f"this run has no {_BUDGET_NOUNS[kind]} left, so "
                f"{invocation.tool.name} was not run. Work with what you already have."
            ),
            retryable=False,
        ),
        duration_ms=0,
    )


@dataclass
class TracingHooks:
    """One span per tool call, and one JSON line per tool call (T2, T6).

    A pre-hook and a post-hook are two separate callables with no shared frame, which is
    why `Tracer` exposes `open_span` / `close_span` as a pair rather than a context
    manager, and why the span id has to be remembered *somewhere* in between. It is
    remembered here, keyed by the invocation object the registry hands to both halves —
    not by reading `ctx.current_span_id` back, which would silently close the wrong span if
    anything the tool did left a span of its own open.

    One instance per registry, not per call: the dictionary is the correlation, and a fresh
    instance per call would have nothing to correlate. Entries are removed as they close,
    so the only ones that linger are calls whose post-hook never ran.

    **`tracer` is optional (T6).** Without one there are no rows, but the JSON line is still
    emitted — with `span_id: null`, since no span was minted. That is what lets a run whose
    database could not be opened still say what it did, and it is why this hook is now
    installed unconditionally.
    """

    tracer: Tracer | None = None
    _open_spans: dict[int, str] = field(default_factory=dict, repr=False)

    async def before(self, invocation: ToolInvocation) -> None:
        """Open the tool's span. Always returns `None` — an observer never short-circuits.

        `open_span` derives the parent from `RunContext`'s span stack, so a tool called
        inside an agent or model operation nests under it with nothing passed down. Today
        nothing else opens a span during a run, so tool spans sit directly under the run;
        that becomes a tree the moment agent spans exist, without this code changing.

        With no tracer there is nothing to open and nothing to remember: `after` finds no
        entry, logs its line, and closes no span.
        """
        if self.tracer is None:
            return None
        span_id = self.tracer.open_span(
            invocation.ctx,
            invocation.tool.name,
            "tool",
            input_summary=_summarise_input(invocation.args),
        )
        self._open_spans[id(invocation)] = span_id
        return None

    async def after(self, invocation: ToolInvocation, result: ToolResult[Any]) -> None:
        """Close the span this call opened and log the same facts as one JSON line.

        `duration_ms` is the registry's own measurement, handed straight through:
        `ToolRegistry.call` already timed this exact call, and measuring it a second time
        here would put two different numbers on one event. `from_cache` comes off the
        result for the same reason — the tool is the only thing that knows whether it
        answered from its cache. **The log line reads the same values**, so the row and the
        line are one observation written twice, never two observations that can disagree.

        A missing entry means either that no tracer was supplied, or that `before` never ran
        for this invocation (a hook installed out of order, or a pre-hook that raised before
        it). There is no span to close, and inventing one would put a row in the trace for
        an operation nobody observed. The line is still emitted either way.
        """
        span_id = self._open_spans.pop(id(invocation), None)
        error_code = result.error.code.value if result.error is not None else None

        if span_id is not None and self.tracer is not None:
            self.tracer.close_span(
                invocation.ctx,
                span_id,
                ok=result.ok,
                error_code=error_code,
                from_cache=result.from_cache,
                output_summary=_summarise_output(result),
                duration_ms=result.duration_ms,
            )

        _log_tool_call(invocation, result, span_id=span_id, error_code=error_code)
        return None


def install_registry_hooks(
    registry: ToolRegistry, *, tracer: Tracer | None = None
) -> None:
    """Give `registry` the behaviour every tool call is meant to have.

    Both hooks are now unconditional. Budget enforcement always was: a wired registry that
    does not enforce its limits is the failure S4 and T3 exist to prevent. `TracingHooks`
    joined it in T6, because the JSON log line needs no database — `tracer` now decides
    whether **rows** are written, not whether the call is observed at all. A caller with no
    database gets a fully enforced registry that keeps no rows and still reports what it
    did.

    Called by `tools/wiring.py`, which is the only place tools and hooks are assembled.
    Calling it twice on one registry would double every span and charge every call twice.
    """
    hooks = TracingHooks(tracer)
    registry.add_pre_hook(hooks.before)
    registry.add_post_hook(hooks.after)
    registry.add_pre_hook(enforce_run_budget)


def _log_tool_call(
    invocation: ToolInvocation,
    result: ToolResult[Any],
    *,
    span_id: str | None,
    error_code: str | None,
) -> None:
    """One tool call as one line of JSON (T6, plan section 13).

    Every field is read from what the registry and the tool already produced — the same
    values the span row is written from. Nothing is measured, timed or counted here, which
    is what keeps this a second *view* of one observation rather than a second tracing path.

    `ts` is the moment the line was emitted, not a third measurement of the call: the call's
    own length is `duration_ms`, timed centrally by `ToolRegistry.call`. It exists so a line
    from a run with no database still has an instant to line it up against a log.

    Wrapped, because a diagnostic must not be able to fail a tool call — the same stance
    `Tracer._guard` takes over a failed write. Only serialisation is guarded: `ValueError`
    and `TypeError` are what a value `json` cannot encode raises.
    """
    try:
        payload = json.dumps(
            {
                "event": "tool_call",
                "ts": datetime.now(UTC).isoformat(),
                "run_id": invocation.ctx.run_id,
                "span_id": span_id,
                "tool": invocation.tool.name,
                "ok": result.ok,
                "duration_ms": result.duration_ms,
                "from_cache": result.from_cache,
                "error_code": error_code,
            },
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        trace_logger.warning(
            "could not serialise the trace log line for %s: %s: %s",
            invocation.tool.name,
            type(exc).__name__,
            exc,
        )
        return
    trace_logger.info(payload)


def _summarise_input(args: BaseModel) -> str:
    """A tool's validated arguments as one line for the trace.

    The model's own JSON, so the summary is exactly what the tool was asked to do rather
    than a paraphrase that can drift from it. Tool inputs are small by contract — a query,
    a URL, a path — and `store.start_span` bounds this to `TRACE_SUMMARY_CHARS` regardless.
    """
    return args.model_dump_json()


def _summarise_output(result: ToolResult[Any]) -> str | None:
    """What came back, as one line: the error's message, or the payload's own JSON.

    A failure summarises to its message rather than its code, because `error_code` is
    already a column of its own and repeating it would waste the only 200 characters a
    span gets on something already recorded.
    """
    if result.error is not None:
        return result.error.message
    if isinstance(result.data, BaseModel):
        return result.data.model_dump_json()
    return None if result.data is None else str(result.data)
