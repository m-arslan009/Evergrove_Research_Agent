"""What every reasoning role needs, and no role owns (Day 5 T1).

The Day 5 split moved Day 3's four stage functions into `supervisor.py`, `researcher.py` and
`appraiser.py`. This module holds what was left: the pieces more than one of them uses, plus
the orchestration mechanics both the single-agent loop and the Supervisor drive.

Nothing here decides anything. Three groups, and the split is the point:

**Failure and identity** — `StopReason` and `PreparationFailed`, which name how a run ended.
They live here rather than with the Supervisor because `single_agent.run_agent` produces the
first and `supervisor.finalise` raises the second, and neither module may import the other.

**The ledger's floor** — `_claim_reasoning_call` and the two reserves. `RunBudget.claim`
remains the only enforcement point; this adds a floor above it, in one place, so "how many
calls are held back" stays a number at a call site rather than a rule copied into three role
modules.

**Constrained decoding** — `_decode`, shared by the Supervisor's planner and the Appraiser
because their failure shapes are identical.

**Orchestration mechanics** — `_assign` and the three memory calls. These are not decisions:
they turn a decision into a worker's assignment, and they mirror what happened into the two
memories through the registry. Both loops need them and neither loop should own them, which
is exactly why they are here and not in `supervisor.py`.

**This module imports no sibling in `agents/`.** It sits at the bottom of the package's own
dependency graph — `runtime` → the three roles → `single_agent` → `__init__` — so the split
cannot produce a cycle. It never imports `evergrove_agent.agents` either; siblings are
reached by module path, which is what stops the package re-entering itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from evergrove_agent.config import Settings, get_settings
from evergrove_agent.llm import LLMProvider, Message, build_provider
from evergrove_agent.memory.run_memory import entries_from
from evergrove_agent.schemas import (
    AppraisalVerdict,
    FocusPreparationReport,
    PreviousPreparation,
    ResearchAssignment,
    ResearchFindings,
    RunState,
    SupervisorDecision,
    TaskContext,
)
from evergrove_agent.tools.base import RunBudget, RunContext
from evergrove_agent.tools.memory_tools import (
    RecallInput,
    RecallOutput,
    RecordRunMemoryInput,
    SavePreparationInput,
)
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tools.validate_report import ReportIssue

StopReason = Literal[
    "sufficient",
    "planner_finalised",
    "hop_cap",
    "budget_spent",
    "no_followup",
    "planner_unavailable",
    "appraiser_unavailable",
]
"""Why the loop stopped. Read by `render_stop_reason`, which decides which of these are
worth telling the user about — the first two mean the run finished, the rest mean it was
cut short."""


class PreparationFailed(RuntimeError):
    """The run could not produce a valid report at all.

    An exception rather than a Pydantic model, and it lives beside the loop rather than in
    `schemas/`, for the same reason `LLMError` does: it is not a message any stage exchanges
    with another, it is the end of a run. **A run that cannot produce a valid report never
    returns a partial one** — a half-filled plan that looks complete is worse than a failure
    the caller can see.

    Message-first, with the same facts also attached structurally. The string still says
    everything on its own, but S11's `service.py` and S12's CLI both have to show a user
    *why* a run failed, and recovering that by parsing a formatted sentence breaks the first
    time the sentence is reworded. Deliberately not a trace record: Day 4 owns tracing, and
    a terminal failure must not wait for it.
    """

    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        attempts: int = 0,
        issues: Sequence[ReportIssue] = (),
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.attempts = attempts
        """Finalise attempts actually made. Fewer than `MAX_OUTPUT_RETRIES` when the ledger
        stopped the ladder early, which is the difference between "the model cannot write a
        valid report" and "the run ran out of room to ask it again"."""
        self.issues = tuple(issues)
        """What `validate_report` still objected to on the last attempt that produced a
        parseable report. Empty when every attempt drifted off the schema, or when the budget
        refused the first attempt."""


# --- how much of the budget each stage may take ------------------------------------------

_MAX_DECODE_ATTEMPTS = 2
"""One constrained-decoding call, plus one re-ask quoting the validation errors back.
`extra="forbid"` on the model-output schemas is what turns drift into this retry; a third
attempt is S10's ladder for the report, not this loop's for a decision."""

_FINALISE_RESERVE = 1
"""Model calls held back from planning and judging.

`claim("model_call")` is indifferent to which stage is asking, so without this a loop that
researches enthusiastically spends the last call on a research turn and the run cannot
produce a report at all. Finalise is mandatory, not one more claim."""

_RESEARCH_RESERVE = 2
"""Model calls held back from a research turn: the finalise call, and this hop's appraisal.

The same reservation applied twice. Without it a hop can gather evidence and then leave
itself unjudged, which is the one outcome that wastes both the searches and the reads it
just spent.

Kept beside `_FINALISE_RESERVE` although only the Researcher applies it: the two are one
decision about how a ten-call budget is divided, and splitting them across modules would let
one be re-tuned without the other being reconsidered."""


def _claim_reasoning_call(budget: RunBudget, *, reserve: int) -> bool:
    """Spend one model call on a non-final stage, keeping `reserve` calls back.

    The one place the reserves above are applied, so "how many are held back" is a number at
    a call site rather than a rule copied into four functions. Falls through to
    `RunBudget.claim`, which is still the only enforcement point — this adds a floor, it does
    not add a second ledger.
    """
    if budget.remaining("model_call") <= reserve:
        return False
    return budget.claim("model_call")


# --- the providers the four stages use ----------------------------------------------------


def _alternate_provider(settings: Settings) -> LLMProvider | None:
    """The other provider, when one is genuinely configured — the retry ladder's last hope.

    **"Genuinely configured" is the whole point.** `HostedProvider` constructs happily
    without a key and only raises when it is called, so treating it as available whenever the
    class exists would spend the final attempt discovering that there was never a second
    model. A hosted alternate therefore requires `GOOGLE_API_KEY`; a local one requires
    nothing beyond the reachable Ollama the primary path is already betting on.

    Resolved against the supervisor's provider because finalising is the supervisor's stage,
    and `build_provider` stays the only factory — `override` is the same seam the CLI's
    `--provider` flag uses.
    """
    if settings.provider_for("supervisor") == "hosted":
        return build_provider("supervisor", settings, override="local")
    if settings.google_api_key is None:
        return None
    return build_provider("supervisor", settings, override="hosted")


@dataclass(frozen=True)
class AgentProviders:
    """One provider per reasoning role, resolved once for the whole run.

    A parameter rather than three `build_provider` calls inside the stages, because that is
    what lets the offline suite drive the entire loop with a `FakeProvider` and no patching —
    the same injection seam every tool takes for its client, backend and connection.

    Three fields rather than a mapping so a missing role is a construction error, and so
    Day 5's three agents can each be handed exactly their own.
    """

    supervisor: LLMProvider
    researcher: LLMProvider
    appraiser: LLMProvider
    fallback: LLMProvider | None = None
    """The provider the retry ladder's last attempt uses, when an alternate is configured.

    Defaulted rather than required, because it is not a role — it is a second chance at one
    stage. A run with no alternate simply repeats on the primary, and a test constructing
    `AgentProviders(fake, fake, fake)` keeps meaning exactly what it did before."""

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> AgentProviders:
        """The providers `*_PROVIDER` configures. `build_provider` stays the only factory."""
        settings = settings or get_settings()
        return cls(
            supervisor=build_provider("supervisor", settings),
            researcher=build_provider("researcher", settings),
            appraiser=build_provider("appraiser", settings),
            fallback=_alternate_provider(settings),
        )


# --- constrained decoding, with one re-ask ------------------------------------------------


async def _decode(
    provider: LLMProvider,
    prompt: str,
    schema: type[BaseModel],
    *,
    budget: RunBudget,
    settings: Settings,
    reserve: int,
) -> BaseModel | None:
    """One constrained-decoding turn, re-asked once if the reply does not fit the schema.

    Shared by the planner and the appraiser because their failure shapes are identical: both
    are `generate(schema=...)` calls whose output steers control flow, and for both the
    honest answer to "it still does not parse" is to stop asking.

    `None` means this stage has no answer — the budget refused, or the model drifted twice.
    The caller degrades; it never retries in a wider loop.

    `LLMError` propagates. An unreachable model is a broken run, not something to reason
    around, and that asymmetry with `ToolError` is deliberate project-wide.
    """
    messages = [Message(role="user", content=prompt)]

    for _ in range(_MAX_DECODE_ATTEMPTS):
        if not _claim_reasoning_call(budget, reserve=reserve):
            return None
        response = await provider.generate(
            messages, schema=schema, temperature=settings.temperature
        )
        try:
            return schema.model_validate_json(response.text)
        except ValidationError as exc:
            messages = [
                Message(role="user", content=prompt),
                Message(role="assistant", content=response.text),
                Message(role="user", content=_RETRY_INSTRUCTION.format(errors=_errors(exc))),
            ]
    return None


_RETRY_INSTRUCTION = (
    "That reply did not match the schema:\n{errors}\n"
    "Answer again with JSON matching the schema you were given, and nothing else."
)
"""The re-ask, as an extra message rather than a prompt placeholder — the same mechanism
S10 uses for the report, and the reason neither stage prompt needs a retry variant."""


def _errors(exc: ValidationError) -> str:
    """A `ValidationError` as a few lines a model can act on.

    Field path and message only. The full Pydantic rendering carries input values and URLs
    that would spend a large slice of a 4096-token window saying very little.
    """
    return "\n".join(
        f"- {'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()[:5]
    )


# --- orchestration mechanics: turning a decision into work, and recording what happened ----


def _assign(
    decision: SupervisorDecision, state: RunState, budget: RunBudget
) -> ResearchAssignment:
    """The planner's decision, plus what the run can still afford, as one message.

    Self-contained because the Researcher receives this and nothing else. The allowances are
    sized from `remaining(...)` but do not enforce anything — `claim` stays the only
    enforcement point, and this is what the researcher is *told* it may spend.

    `max_fetches` is also bounded by `sources_remaining`, which is how `MAX_SOURCES_KEPT`
    reaches the run: it caps sources actually **read**, since leads cost nothing to keep and
    are what the grounding set is built from.

    Called only when `decision.action == "RESEARCH"`, whose validator guarantees a non-empty
    `research_question`.
    """
    return ResearchAssignment(
        research_question=decision.research_question or "",
        session_minutes=state.task.session_minutes,
        source_preference=decision.source_preference,
        hop=state.hop + 1,
        max_searches=budget.remaining("search"),
        max_fetches=min(
            budget.remaining("fetch"), budget.sources_remaining(len(state.fetched_urls))
        ),
        avoid_queries=list(state.used_queries)[:32],
        avoid_urls=sorted(state.evidence_urls)[:64],
    )


async def _recall_previous_preparation(
    registry: ToolRegistry,
    ctx: RunContext,
    task: TaskContext,
    settings: Settings,
) -> PreviousPreparation | None:
    """What an earlier run prepared for this task, or `None` — and `None` is never a failure.

    **The one lookup path.** It goes through the registered tool rather than
    `prep_memory.recall_previous_preparation`, for the same reason the writes do: the tool is
    the guard that turns a `sqlite3.Error` into a `ToolResult`, and the registry is what makes
    the call a span in the trace. A `SELECT` of our own here would be a second path that has
    to re-implement both.

    **Four outcomes, one answer.** A miss (`found=False`), a storage failure (`ok=False`
    carrying `UNKNOWN`), a payload of an unexpected type, and no recent row all degrade to
    `None`, and `None` means the run proceeds exactly as it did before this existed. That is
    the whole guarantee memory rests on: `registry.call` never raises and the tool never
    raises, so there is no path from a broken database to a failed run.

    `found` is checked rather than inferred from `previous is not None`: the tool's contract
    makes `found` the field that distinguishes "nothing recent matches" from a failure, and
    reading the payload around it would quietly re-couple the two.

    `max_age_days` is passed explicitly so the *run's* settings decide the recall window.
    Leaving it `None` is documented as "use `MEMORY_RECALL_MAX_AGE_DAYS`", but the default is
    resolved inside `prep_memory` through the process-wide `get_settings()`, which ignores a
    `settings` override this loop was handed.

    Costs no budget: `TOOL_BUDGET` has no entry for a local SQLite read, and a name absent
    from that map is free.
    """
    result = await registry.call(
        "recall_previous_preparation",
        RecallInput(
            task_title=task.task_title,
            max_age_days=settings.memory_recall_max_age_days,
        ),
        ctx,
    )
    if not result.ok or not isinstance(result.data, RecallOutput):
        return None
    return result.data.previous if result.data.found else None


async def _mirror_session_memory(
    registry: ToolRegistry,
    ctx: RunContext,
    *,
    decision: SupervisorDecision,
    findings: ResearchFindings,
    verdict: AppraisalVerdict | None,
) -> None:
    """Write one finished hop into `run_memory`, and carry on regardless of the outcome.

    **`RunState` remains the session memory this loop runs on.** It already carries the goal,
    the findings and the seen queries and URLs from hop to hop, and nothing here is ever read
    back to make a decision — reading state out of SQLite would be the second, competing state
    mechanism `memory/run_memory.py` exists not to be. This is durability: `RunState` dies with
    the process, and a nine-to-fifteen-minute run deserves to leave a record of how it got
    where it did.

    Best-effort by construction. `registry.call` never raises and the memory tool turns a
    storage failure into a `ToolResult`, so the result is deliberately not inspected: there is
    nothing this loop could usefully do about a mirror that did not write, and stopping a run
    over it would be the exact failure the tool's guard exists to prevent.
    """
    await registry.call(
        "record_run_memory",
        RecordRunMemoryInput(
            hop=findings.hop,
            entries=entries_from(
                goal=findings.research_question,
                decision=f"{decision.action}: {decision.reasoning}",
                findings=findings.notes or f"{len(findings.sources)} sources gathered",
                appraisal=_appraisal_line(verdict),
                queries=findings.queries_used,
                urls=[source.url for source in findings.sources],
            ),
        ),
        ctx,
    )


def _appraisal_line(verdict: AppraisalVerdict | None) -> str:
    """The verdict as one remembered line, or `""` when the hop was never judged.

    Empty means `entries_from` records no `appraisal` row at all, which is the honest shape:
    a missing row reads as "this hop was not judged", where a row saying nothing would read as
    a judgement that found nothing.
    """
    if verdict is None:
        return ""
    line = f"sufficient={verdict.sufficient}: {verdict.reasoning}"
    if verdict.missing_information:
        line += f" | missing: {'; '.join(verdict.missing_information)}"
    if verdict.requested_followup:
        line += f" | follow-up: {verdict.requested_followup}"
    return line


async def _remember_preparation(
    registry: ToolRegistry, ctx: RunContext, report: FocusPreparationReport
) -> None:
    """Store the finished preparation so a later session can continue it.

    Called with the report `finalise` returned, which is the only report that exists: every
    other outcome is a `PreparationFailed`. That is why no `validated=` flag is passed and no
    grounding is re-checked here — the guarantee comes from *where* this is called, not from
    something the caller promises.

    Best-effort, for the same reason as the mirror above: a run that produced a valid report
    has already succeeded, and failing it because the report could not be filed away would
    throw away the fifteen minutes that produced it.
    """
    await registry.call("save_preparation", SavePreparationInput(report=report), ctx)
