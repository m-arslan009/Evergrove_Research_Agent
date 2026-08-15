"""The runtime tool contract (plan section 9.3) and the hook signatures around it.

Kept separate from `registry.py` so a tool module can implement the contract without
importing the registry that dispatches it — the same split as `llm/base.py` and its
providers. Nothing here touches a model, the network, or SQLite.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from evergrove_agent.config import Settings, get_settings
from evergrove_agent.schemas import ToolResult


def _new_run_id() -> str:
    """Short, greppable run identifier — the form used throughout the plan's traces."""
    return f"run_{uuid.uuid4().hex[:6]}"


BudgetKind = Literal["search", "fetch", "model_call"]
"""The three things a run spends one at a time. Each maps to a counter on `RunBudget`;
Day 4's pre-hook maps a tool name onto one of these values and claims through it."""

_LEDGER: dict[BudgetKind, tuple[str, str]] = {
    "search": ("max_searches", "searches_used"),
    "fetch": ("max_fetches", "fetches_used"),
    "model_call": ("max_model_calls", "model_calls_used"),
}
"""kind → (limit field, counter field). One table so `remaining`, `claim` and
`exhausted_limits` cannot disagree about which counter belongs to which limit."""


@dataclass
class RunBudget:
    """What one run has spent, and what it is still allowed to spend (plan section 14.4).

    The counterpart to `schemas.RunState`, and the split is deliberate: `RunState` is what
    the run has *seen* — a Pydantic message Day 4 mirrors to `run_memory` — and this is
    what the run has *spent*. A live clock does not belong in a serialised message, and the
    sources a run gathered do not belong in a ledger, so neither object holds a copy of the
    other's field.

    Limits are snapshotted at construction rather than read from `Settings` on each check,
    so a run cannot change budget halfway through and a trace can be read back against
    fixed numbers.

    Nothing here decides what a refusal *means*. `claim` answers `False`; the caller
    chooses: Day 3's loop records a skipped step and finalises with what it already has,
    and Day 4's registry pre-hook turns the same `False` into a
    `ToolResult(BUDGET_EXCEEDED)` without this class changing. That is why no `ToolResult`,
    no `ErrorCode` and no prompt wording appears below.
    """

    max_searches: int
    max_fetches: int
    max_model_calls: int
    max_hops: int
    max_sources_kept: int
    timeout_s: float
    clock: Callable[[], float] = time.monotonic
    """Injected so the deadline is testable without waiting 900 seconds (engineering
    decision 10). Monotonic, not wall time: a clock adjustment mid-run must not hand the
    agent extra budget or cut it short."""

    searches_used: int = 0
    fetches_used: int = 0
    model_calls_used: int = 0

    started_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.started_at = self.clock()

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> RunBudget:
        """The budget the configured limits describe. Resolves its own settings if given none."""
        settings = settings or get_settings()
        return cls(
            max_searches=settings.max_search_calls,
            max_fetches=settings.max_fetch_calls,
            max_model_calls=settings.max_model_calls,
            max_hops=settings.max_hops,
            max_sources_kept=settings.max_sources_kept,
            timeout_s=float(settings.total_run_timeout_s),
            clock=clock,
        )

    # --- the ledger ---------------------------------------------------------------------

    def remaining(self, kind: BudgetKind) -> int:
        """How many more of `kind` this run may spend. Never negative."""
        limit, used = _LEDGER[kind]
        return max(0, getattr(self, limit) - getattr(self, used))

    def claim(self, kind: BudgetKind) -> bool:
        """Spend one of `kind` if the run still may, and report whether it could.

        The single enforcement point. Claim *before* making the call, never after: the
        same direction `memory.budget.reserve_search_call` takes, because a call that timed
        out may still have been spent at the other end, and over-counting is the safe error.

        A refused claim increments nothing, so `remaining` and `exhausted_limits` keep
        telling the finalise step the truth however many times it asks.
        """
        if self.expired or self.remaining(kind) == 0:
            return False
        _, used = _LEDGER[kind]
        setattr(self, used, getattr(self, used) + 1)
        return True

    # --- the clock ------------------------------------------------------------------------

    @property
    def elapsed_seconds(self) -> float:
        """How long this run has been going."""
        return self.clock() - self.started_at

    @property
    def remaining_seconds(self) -> float:
        """How long it may still run. Never negative."""
        return max(0.0, self.timeout_s - self.elapsed_seconds)

    @property
    def expired(self) -> bool:
        """Whether the run is out of time.

        Checked inside `claim`, which is what makes a timed-out run *stop* at its next
        step instead of noticing afterwards. `TOTAL_RUN_TIMEOUT_S` keeps its existing job
        as the Ollama client timeout; this is a second, run-level reading of the same
        value rather than a new setting.
        """
        return self.remaining_seconds <= 0.0

    # --- limits whose count belongs to `RunState` ------------------------------------------

    def hops_remaining(self, hops_used: int) -> int:
        """How many further research hops `MAX_HOPS` allows. Never negative.

        A function of a count the caller passes in, not of a counter kept here:
        `RunState.hop` already owns that number, and a second copy would drift from it.
        Clamped, because the answer feeds `ResearchAssignment`, whose allowances are `ge=0`.
        """
        return max(0, self.max_hops - hops_used)

    def sources_remaining(self, sources_kept: int) -> int:
        """How many more sources `MAX_SOURCES_KEPT` allows this run to keep. Never negative.

        Same shape and same reason as `hops_remaining`: the sources themselves live in
        `RunState.findings`, so only the limit lives here.
        """
        return max(0, self.max_sources_kept - sources_kept)

    # --- why a run stopped ------------------------------------------------------------------

    @property
    def exhausted_limits(self) -> tuple[str, ...]:
        """Which limits are spent, as identifiers, in a stable order.

        What S8 and S10 read to finalise honestly: a run that stopped because it ran out
        of searches has to say so in `unknowns` rather than produce a thin report that
        reads like a confident one. Identifiers, never sentences — turning these into
        prompt text is `agents/prompt_context.py`'s job, and state stays out of rendering.

        Covers only what this object counts. Whether the hop cap or the source cap is spent
        depends on a count `RunState` owns, so the loop asks `hops_remaining` /
        `sources_remaining` for those.
        """
        spent = [kind for kind in _LEDGER if self.remaining(kind) == 0]
        if self.expired:
            spent.append("time")
        return tuple(spent)


@dataclass
class RunContext:
    """Everything a tool is allowed to know about the run it belongs to.

    Day 4 adds the span stack (`span_id` / `parent_span_id`); plan section 27 names this
    shape as expensive to change, because the registry, every hook and every agent read it.
    """

    run_id: str = field(default_factory=_new_run_id)
    budget: RunBudget = field(default_factory=RunBudget.from_settings)
    """The run's spend ledger. Reachable from every tool and, on Day 4, from every hook,
    which is why it lives here and not beside the loop: moving enforcement into a registry
    pre-hook then needs no new plumbing."""


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
