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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from evergrove_agent.schemas import ErrorCode, ToolError, ToolResult
from evergrove_agent.tools.base import BudgetKind, ToolInvocation
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tracing.tracer import Tracer

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
    """One span per tool call: opened by `before`, closed by `after` (T2).

    A pre-hook and a post-hook are two separate callables with no shared frame, which is
    why `Tracer` exposes `open_span` / `close_span` as a pair rather than a context
    manager, and why the span id has to be remembered *somewhere* in between. It is
    remembered here, keyed by the invocation object the registry hands to both halves —
    not by reading `ctx.current_span_id` back, which would silently close the wrong span if
    anything the tool did left a span of its own open.

    One instance per registry, not per call: the dictionary is the correlation, and a fresh
    instance per call would have nothing to correlate. Entries are removed as they close,
    so the only ones that linger are calls whose post-hook never ran.
    """

    tracer: Tracer
    _open_spans: dict[int, str] = field(default_factory=dict, repr=False)

    async def before(self, invocation: ToolInvocation) -> None:
        """Open the tool's span. Always returns `None` — an observer never short-circuits.

        `open_span` derives the parent from `RunContext`'s span stack, so a tool called
        inside an agent or model operation nests under it with nothing passed down. Today
        nothing else opens a span during a run, so tool spans sit directly under the run;
        that becomes a tree the moment agent spans exist, without this code changing.
        """
        span_id = self.tracer.open_span(
            invocation.ctx,
            invocation.tool.name,
            "tool",
            input_summary=_summarise_input(invocation.args),
        )
        self._open_spans[id(invocation)] = span_id
        return None

    async def after(self, invocation: ToolInvocation, result: ToolResult[Any]) -> None:
        """Close the span this call opened, recording how the call went.

        `duration_ms` is the registry's own measurement, handed straight through:
        `ToolRegistry.call` already timed this exact call, and measuring it a second time
        here would put two different numbers on one event. `from_cache` comes off the
        result for the same reason — the tool is the only thing that knows whether it
        answered from its cache.

        A missing entry means `before` never ran for this invocation (a hook installed out
        of order, or a pre-hook that raised before it). There is no span to close, and
        inventing one would put a row in the trace for an operation nobody observed.
        """
        span_id = self._open_spans.pop(id(invocation), None)
        if span_id is None:
            return None
        self.tracer.close_span(
            invocation.ctx,
            span_id,
            ok=result.ok,
            error_code=result.error.code.value if result.error is not None else None,
            from_cache=result.from_cache,
            output_summary=_summarise_output(result),
            duration_ms=result.duration_ms,
        )
        return None


def install_registry_hooks(
    registry: ToolRegistry, *, tracer: Tracer | None = None
) -> None:
    """Give `registry` the behaviour every tool call is meant to have.

    Budget enforcement is unconditional: a wired registry that does not enforce its limits
    is the failure S4 and T3 exist to prevent. Tracing needs somewhere to write, so it is
    installed only when a `tracer` is supplied — a caller with no database still gets a
    fully enforced registry, one that simply keeps no record.

    Called by `tools/wiring.py`, which is the only place tools and hooks are assembled.
    Calling it twice on one registry would double every span and charge every call twice.
    """
    if tracer is not None:
        hooks = TracingHooks(tracer)
        registry.add_pre_hook(hooks.before)
        registry.add_post_hook(hooks.after)
    registry.add_pre_hook(enforce_run_budget)


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
