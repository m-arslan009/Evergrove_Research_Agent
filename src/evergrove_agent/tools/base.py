"""The runtime tool contract (plan section 9.3) and the hook signatures around it.

Kept separate from `registry.py` so a tool module can implement the contract without
importing the registry that dispatches it — the same split as `llm/base.py` and its
providers. Nothing here touches a model, the network, or SQLite.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from evergrove_agent.schemas import ToolResult


def _new_run_id() -> str:
    """Short, greppable run identifier — the form used throughout the plan's traces."""
    return f"run_{uuid.uuid4().hex[:6]}"


@dataclass
class RunContext:
    """Everything a tool is allowed to know about the run it belongs to.

    Deliberately near-empty today. Day 3 adds the budget counters and Day 4 the span
    stack (`span_id` / `parent_span_id`); it exists now because `Tool.run` takes it, and
    plan section 27 names its shape as expensive to change — the registry, every hook and
    every agent read it.
    """

    run_id: str = field(default_factory=_new_run_id)


@runtime_checkable
class Tool(Protocol):
    """What every tool implements — the uniform surface hooks and tracing rely on.

    `description` and `input_model` are the half the model sees: they become the
    `ToolSpec` advertised to the provider. `run` is the half only the registry calls.
    """

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]

    async def run(self, args: BaseModel, ctx: RunContext) -> ToolResult[Any]:
        """Do the work and report the outcome.

        Must never raise: a failure is a `ToolResult` carrying a `ToolError`, so the agent
        can reason about it and the trace can record it (plan section 9.3).
        """
        ...


@dataclass(frozen=True)
class ToolInvocation:
    """One call, after the name resolved and the arguments validated, as hooks see it.

    Hooks take this rather than three loose parameters so Day 4 can add fields without
    rewriting every hook signature.
    """

    tool: Tool
    args: BaseModel
    ctx: RunContext


class PreToolHook(Protocol):
    """Runs before the tool, in registration order.

    Returning a `ToolResult` short-circuits the call — the tool never runs, and that
    result goes on through the post-hooks. This is how Day 4's cache hit and budget
    refusal work. Returning `None` lets the call continue.
    """

    async def __call__(self, invocation: ToolInvocation) -> ToolResult[Any] | None: ...


class PostToolHook(Protocol):
    """Runs after the tool (or after a short-circuit), in registration order.

    Returning `None` leaves the result untouched — the shape an observer such as the
    Day 4 trace writer takes. Returning a `ToolResult` replaces it for every later hook
    and for the caller.
    """

    async def __call__(
        self, invocation: ToolInvocation, result: ToolResult[Any]
    ) -> ToolResult[Any] | None: ...
