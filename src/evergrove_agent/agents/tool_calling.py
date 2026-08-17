"""The bridge between a model and the tool system (plan section 23, feature 3).

Day 1 gave every provider a `ToolSpec` to advertise and a `ToolCall` to report back. Day 2
gave every tool a registry that validates arguments, times the call and never raises.
Nothing joined the two: no code turned a registered `Tool` into the advertisement a model
reads, decided which tools a reasoning stage may even see, or routed a model's request back
into the registry. `tools/wiring.py` says so in as many words — which subset is
*advertised* is this layer's decision, and this is that layer.

Two guarantees shape the whole file.

**A model cannot bypass the registry.** All it ever produces is a name and a dict of
arguments. Every path from that dict to a running tool goes through `ToolRegistry.call`,
which owns argument validation — nothing here builds a tool's input model itself, because a
second validator is a second contract, and two contracts drift.

**A model's mistake is a value, not an exception.** A misspelled tool, a tool it was not
offered, malformed arguments and a tool that failed all come back as a `ToolResult`
carrying a `ToolError` it can read and correct on its next turn.

Nothing here names a provider. `OllamaProvider` passes `ToolSpec.parameters` through
untouched and `HostedProvider` translates it with `to_gemini_schema`; both already parse
their own replies into `ToolCall`. This module speaks only the contract they share.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from evergrove_agent.config import AgentRole
from evergrove_agent.llm.base import ToolCall, ToolSpec
from evergrove_agent.schemas import ErrorCode, ToolError, ToolResult
from evergrove_agent.tools.base import RunContext, Tool
from evergrove_agent.tools.registry import ToolRegistry

_ROLE_TOOLS: dict[AgentRole, tuple[str, ...]] = {
    "supervisor": (),
    "researcher": ("web_search", "fetch_url"),
    "appraiser": (),
}
"""Which tools each reasoning role may be offered.

Keyed on `config.AgentRole` rather than a fourth enum of its own, so the stage that picks a
provider and the stage that picks a menu cannot drift apart, and Day 5's three agents
inherit this table unchanged.

Two of the three menus are empty on purpose. The supervisor plans and finalises and the
appraiser judges, and all three are constrained-decoding calls made with `schema=`: handing
a tool menu to one of them asks a 4B model to do two different things in a single turn,
which is where its reliability goes. Only the researcher acts.

`normalize_sources` appears nowhere. It is registered so a trace shows what normalisation
discarded (`wiring.py`), but which URLs are the same page is not a judgement a model should
be making, and `web_search` already runs it as a pipeline step.
"""

_ATTACHMENT_TOOL = "read_document"
"""Advertised to the researcher only when the task actually carries an attachment.

A menu entry costs a small model attention on every turn, and without an attachment this
one cannot answer anything but `NOT_FOUND` or `PATH_NOT_ALLOWED`.
"""


def advertised_tool_names(
    role: AgentRole, *, has_attachment: bool = False
) -> tuple[str, ...]:
    """The tools `role` is allowed to use, in menu order.

    Names rather than specs, so the policy can be asserted without building a registry —
    and so the same tuple serves both as what is advertised and as what `dispatch` accepts.
    Those must be one value: a tool the model can call but was never shown is a capability
    nobody reviewed, and one shown but not callable is a turn wasted on a refusal.
    """
    names = _ROLE_TOOLS[role]
    if has_attachment and role == "researcher":
        names += (_ATTACHMENT_TOOL,)
    return names


def to_tool_spec(tool: Tool) -> ToolSpec:
    """A registered tool as the model sees it — the advertisement, not the implementation.

    `parameters` is the input model's own JSON Schema, verbatim: what the model is told is
    then literally what the registry validates against, so the two cannot describe
    different tools. Pruning it (titles, defaults) to save a few dozen tokens would buy a
    second, divergent description of one contract.

    Raises `pydantic.ValidationError` if a tool's `description` outgrows `ToolSpec`'s
    ceiling. That is a wiring bug, and it surfaces where `wiring.py`'s duplicate-name check
    does — at composition, before a model is involved — rather than as a value some agent
    has to reason about.
    """
    return ToolSpec(
        name=tool.name,
        description=tool.description,
        parameters=tool.input_model.model_json_schema(),
    )


def advertise(
    registry: ToolRegistry, role: AgentRole, *, has_attachment: bool = False
) -> list[ToolSpec]:
    """The menu for `role`, ready to hand to `LLMProvider.generate(tools=...)`.

    Raises `ValueError` if the policy names a tool the registry does not hold. The same
    reasoning as `TOOL_NAMES` being declared rather than derived: a name that drifted is a
    failing assertion at build time, never a capability the agent silently lost.
    """
    specs: list[ToolSpec] = []
    for name in advertised_tool_names(role, has_attachment=has_attachment):
        tool = registry.get(name)
        if tool is None:
            raise ValueError(
                f"{role} is meant to be offered {name!r}, which is not registered. "
                f"Registered: {', '.join(registry.names) or 'nothing'}."
            )
        specs.append(to_tool_spec(tool))
    return specs


@dataclass(frozen=True)
class ToolCallOutcome:
    """What the model asked for, beside what happened — kept together.

    Both halves are load-bearing downstream and neither can be recovered from the other:
    S6 builds a `GatheredSource` from the URL the model actually passed, a `ToolFailure`
    from the error, and a trace row from both. The pairing lives here rather than in
    `schemas/` because it is not a message between reasoning stages and never crosses a
    process boundary — the precedent is `ToolInvocation` in `tools/base.py`.
    """

    call: ToolCall
    result: ToolResult[Any]


async def dispatch(
    call: ToolCall,
    *,
    registry: ToolRegistry,
    ctx: RunContext,
    allowed: Sequence[str],
) -> ToolResult[Any]:
    """Run one tool the model asked for. Never raises.

    `allowed` is the menu that turn was given. A name outside it is refused here, before the
    registry is touched: the registry holds tools this stage was deliberately not offered,
    and "registered" must not become "reachable" just because a model guessed the name.

    Everything else goes to `ToolRegistry.call` with the model's arguments **unchanged**, as
    the mapping they arrived as. That call is the only argument validator in the system, and
    it answers a bad one with `BAD_ARGUMENTS` and a line the model can act on.
    """
    if call.name not in allowed:
        return ToolResult(
            ok=False,
            error=ToolError(
                code=ErrorCode.UNKNOWN,
                message=f"{call.name!r} is not available at this step. {_menu(allowed)}",
                retryable=False,
            ),
            duration_ms=0,
        )
    return await registry.call(call.name, call.arguments, ctx)


async def dispatch_all(
    calls: Sequence[ToolCall],
    *,
    registry: ToolRegistry,
    ctx: RunContext,
    allowed: Sequence[str],
) -> list[ToolCallOutcome]:
    """Every call from one model turn, in the order it asked for them.

    Sequential, never `asyncio.gather`: the per-run counters (S4) and the monthly search
    quota are claimed one call at a time, and a run whose tools fire in a different order
    each time cannot be read back off a trace.

    An empty list in gives an empty list out. A model that answered with prose instead of a
    tool call has not failed — what the loop does about it is the research step's business.
    """
    return [
        ToolCallOutcome(
            call=call,
            result=await dispatch(call, registry=registry, ctx=ctx, allowed=allowed),
        )
        for call in calls
    ]


def _menu(allowed: Sequence[str]) -> str:
    """The available-tools sentence, phrased as the registry phrases its own.

    One wording for both refusals, so a model that mistyped a name and one that reached for
    a tool it was never offered recover the same way.
    """
    return (
        f"Available tools: {', '.join(allowed)}."
        if allowed
        else "No tools are available at this step."
    )
