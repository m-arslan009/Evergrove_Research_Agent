"""The single research agent: plan → act → observe → judge → stop (Day 3 S5-S8).

S1-S4 built four pieces that never met. This module is the meeting: `schemas/agents.py`
supplies the vocabulary, `tool_calling.py` reaches the tools, `prompt_context.py` turns the
run's state into prompt text, and `RunContext.budget` says what is still affordable. Nothing
below re-implements any of them.

**Four separately-prompted functions, one loop.** `decide_next_step`, `run_research_step`,
`judge_sufficiency` and `finalise` each take and return a model from `schemas/agents.py`,
even though one agent runs them all today. That is what makes Day 5's split into
`supervisor.py` / `researcher.py` / `appraiser.py` a file move rather than a rewrite.

**A later hop's question is never scripted.** `judge_sufficiency` reads the excerpts and
answers with `AppraisalVerdict.requested_followup`, drawn from what the sources actually
said; `render_progress` puts it in front of the planner; `plan.md` already says to prefer
it. The loop only carries it across — it never writes a question itself, and a run whose
appraiser has nothing specific to ask stops rather than inventing one.

**A refusal is control flow, not an error.** `RunBudget.claim` answers `False` and knows
nothing about prompts, tools or error envelopes; deciding what that means is this module's
job, and it means the same thing everywhere: stop, and finalise honestly with what was
already gathered. `None` from a stage function means the same — that stage could not answer,
so the run stops rather than pretending.

**Nothing here is allowed to loop without a bound.** Hops are bounded by `MAX_HOPS` through
`RunState.hop`, turns within a hop by `_MAX_RESEARCH_TURNS`, re-asks by
`_MAX_DECODE_ATTEMPTS`, and every model call and tool call by the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from evergrove_agent.agents.prompt_context import (
    max_topics_for,
    render_allowance,
    render_already_covered,
    render_attachment,
    render_available_tools,
    render_progress,
    render_research_context,
    render_sources,
    render_stop_reason,
    render_tool_outcome,
)
from evergrove_agent.agents.tool_calling import (
    ToolCallOutcome,
    advertise,
    advertised_tool_names,
    dispatch,
)
from evergrove_agent.config import Settings, get_settings
from evergrove_agent.llm import LLMError, LLMProvider, Message, build_provider
from evergrove_agent.llm.base import ToolCall
from evergrove_agent.llm.prompts import render_prompt
from evergrove_agent.schemas import (
    AppraisalRequest,
    AppraisalVerdict,
    ErrorCode,
    FocusPreparationReport,
    GatheredSource,
    ResearchAssignment,
    ResearchFindings,
    RunState,
    SupervisorDecision,
    TaskContext,
    ToolError,
    ToolFailure,
    ToolResult,
)
from evergrove_agent.search.normalize import canonicalize_url
from evergrove_agent.tools.base import BudgetKind, RunBudget, RunContext
from evergrove_agent.tools.fetch_url import FetchUrlOutput
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tools.web_search import WebSearchOutput

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

    S10 keeps this class and adds the retry ladder in front of it (primary model, primary
    with the validation errors quoted back, then the second provider); today `finalise`
    raises on the first failure.
    """


# --- how much of the budget each stage may take ------------------------------------------

_MAX_RESEARCH_TURNS = 3
"""Model turns one hop may take. Turn 1 finds candidates, turn 2 opens the ones search
actually returned, turn 3 recovers from a failure. A single turn is not enough — the model
would have to guess a URL before seeing any search result, which is the one thing
`research_step.md` forbids it to do."""

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
just spent."""

_TOOL_BUDGET: dict[str, BudgetKind] = {
    "web_search": "search",
    "fetch_url": "fetch",
}
"""Which tools cost which counter. `read_document` reads local disk and costs neither.

This mapping plus `_claim_for_tool` below is the whole of the loop's tool-budget
enforcement, deliberately in one place: Day 4 lifts exactly these two into a registry
pre-hook, and a lift is only cheap while it is one piece."""


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


def _claim_for_tool(call: ToolCall, budget: RunBudget) -> ToolResult[Any] | None:
    """Pay for a tool call before it runs, or refuse it as a result the model can read.

    `None` means paid — the caller dispatches. A `ToolResult` means refused, and the tool is
    never reached, which is what makes the count honest: claiming after the call would let a
    timed-out call go uncounted.

    This is the `False` → `ToolResult(BUDGET_EXCEEDED)` lift that S4's docstring describes,
    written here rather than in `RunBudget` so the ledger stays free of `ErrorCode`.

    A tool with no counter (`read_document`) is free. So is a name the model invented: it is
    not in the mapping, so it costs nothing and `dispatch` refuses it a moment later — a
    guessed name must not be able to drain a budget.

    **Known over-count, resolved by Day 4.** A correctly named tool whose *arguments* are
    malformed is charged here, because the claim has to come before the call and only the
    registry can judge arguments. Day 4's pre-hook runs after `ToolRegistry.call` has
    validated them, so moving this there stops the over-count for free — one more reason the
    mapping and the lift stay in one piece. Over-counting is the safe direction meanwhile,
    and `render_tool_outcome` hands the model the offending field so its next turn recovers.
    """
    kind = _TOOL_BUDGET.get(call.name)
    if kind is None or budget.claim(kind):
        return None
    return ToolResult(
        ok=False,
        error=ToolError(
            code=ErrorCode.BUDGET_EXCEEDED,
            message=(
                f"this run has no {_BUDGET_NOUNS[kind]} left, so {call.name} was not run. "
                "Work with what you already have."
            ),
            retryable=False,
        ),
        duration_ms=0,
    )


_BUDGET_NOUNS: dict[BudgetKind, str] = {
    "search": "searches",
    "fetch": "page reads",
    "model_call": "model calls",
}


# --- the providers the four stages use ----------------------------------------------------


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

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> AgentProviders:
        """The providers `*_PROVIDER` configures. `build_provider` stays the only factory."""
        settings = settings or get_settings()
        return cls(
            supervisor=build_provider("supervisor", settings),
            researcher=build_provider("researcher", settings),
            appraiser=build_provider("appraiser", settings),
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


# --- stage 1: task understanding and narrowing (S5) ---------------------------------------


async def decide_next_step(
    state: RunState,
    *,
    provider: LLMProvider,
    ctx: RunContext,
    settings: Settings | None = None,
) -> SupervisorDecision | None:
    """Research one more thing, or write the report now. Becomes `supervisor.decide()`.

    This is where a broad task becomes one session-sized question, and `session_minutes` is
    the hard input that scopes it: `plan.md` asks for a question "small enough to matter
    inside {session_minutes} minutes", not a syllabus.

    The planner has no tools and no memory of its own previous turn, so `render_progress` is
    the whole of what stops hop 2 repeating hop 1 — the spent queries, the gathered sources,
    and the appraiser's follow-up.

    `None` when the budget refused or the model drifted twice; the loop finalises.
    """
    settings = settings or get_settings()
    prompt = render_prompt(
        "plan",
        task_title=state.task.task_title,
        task_description=state.task.task_description or "(none given)",
        session_minutes=state.task.session_minutes,
        progress=render_progress(
            state, hops_remaining=ctx.budget.hops_remaining(state.hop)
        ),
    )
    decision = await _decode(
        provider,
        prompt,
        SupervisorDecision,
        budget=ctx.budget,
        settings=settings,
        reserve=_FINALISE_RESERVE,
    )
    return decision if isinstance(decision, SupervisorDecision) else None


# --- stage 2: the research step (S6) -------------------------------------------------------


async def run_research_step(
    assignment: ResearchAssignment,
    *,
    provider: LLMProvider,
    registry: ToolRegistry,
    ctx: RunContext,
    settings: Settings | None = None,
    attachment_path: str | None = None,
) -> ResearchFindings:
    """Search, read and collect for one question. Becomes `researcher.run()`.

    Up to `_MAX_RESEARCH_TURNS` model turns, because one is not enough: the model has to see
    what search returned before it can open anything. Each turn's results go back as an
    observation built from `render_tool_outcome`, which carries the error code, the message
    and whether a retry could help — everything `research_step.md`'s recovery instruction
    tells the model to act on.

    The hop ends early on any of: prose instead of tool calls (that text becomes `notes`), a
    refused budget claim, or an unreachable model.

    **Always returns findings, never raises.** A hop that gathered nothing is still a fact
    about the run, and the failures it collected are what stop the report reading like a
    confident one. `dispatch_all` is deliberately not used — a budget claim has to sit
    between one call and the next, so the calls are dispatched one at a time, still strictly
    in the order the model asked for them.
    """
    settings = settings or get_settings()
    budget = ctx.budget
    has_attachment = attachment_path is not None
    specs = advertise(registry, "researcher", has_attachment=has_attachment)
    allowed = advertised_tool_names("researcher", has_attachment=has_attachment)

    messages = [
        Message(
            role="user",
            content=render_prompt(
                "research_step",
                research_question=assignment.research_question,
                session_minutes=assignment.session_minutes,
                source_preference=assignment.source_preference,
                available_tools=render_available_tools(specs),
                allowance=render_allowance(assignment),
                already_covered=render_already_covered(assignment),
                attachment=render_attachment(attachment_path),
            ),
        )
    ]

    sources: dict[str, GatheredSource] = {}
    queries: list[str] = []
    failures: list[ToolFailure] = []
    notes = ""

    for _ in range(_MAX_RESEARCH_TURNS):
        if not _claim_reasoning_call(budget, reserve=_RESEARCH_RESERVE):
            break
        try:
            response = await provider.generate(
                messages, tools=specs, temperature=settings.temperature
            )
        except LLMError as exc:
            # Not fatal here, unlike every other stage: this hop already holds evidence
            # worth keeping, and throwing it away would make the report thinner than the
            # run actually was. If the provider is genuinely down, `finalise` says so.
            notes = notes or f"The research step stopped early: {exc}"
            break

        if response.text.strip():
            notes = response.text.strip()
        if not response.tool_calls:
            break

        outcomes: list[ToolCallOutcome] = []
        for call in response.tool_calls:
            refusal = _claim_for_tool(call, budget)
            result = (
                refusal
                if refusal is not None
                else await dispatch(call, registry=registry, ctx=ctx, allowed=allowed)
            )
            outcome = ToolCallOutcome(call=call, result=result)
            outcomes.append(outcome)
            _absorb(outcome, assignment, sources, queries, failures, settings)

        messages = [
            *messages,
            Message(role="assistant", content=response.text or _ASKED_FOR_TOOLS),
            Message(
                role="user",
                content="\n\n".join(
                    render_tool_outcome(outcome, settings=settings)
                    for outcome in outcomes
                ),
            ),
        ]

    return ResearchFindings(
        research_question=assignment.research_question,
        hop=assignment.hop,
        queries_used=queries[:32],
        sources=list(sources.values())[:64],
        failures=failures[:16],
        notes=notes[:2000],
    )


_ASKED_FOR_TOOLS = "(requested tools)"
"""Stands in for an empty assistant turn. A model that answers with tool calls and no prose
still has to appear in the transcript, or its own request goes missing from the context it
is given next."""


def _absorb(
    outcome: ToolCallOutcome,
    assignment: ResearchAssignment,
    sources: dict[str, GatheredSource],
    queries: list[str],
    failures: list[ToolFailure],
    settings: Settings,
) -> None:
    """Fold one tool outcome into the hop's evidence.

    Owns the `NormalizedSource` (+ `FetchUrlOutput`) → `GatheredSource` conversion that
    `schemas/` cannot do for itself — `search/normalize.py` imports `schemas`, so the reverse
    would be a cycle.

    Keyed on the canonical URL throughout, because that is the string
    `RunState.evidence_urls` is built from and the string S9's grounding check compares
    against. A search hit and a later fetch of the same page must land on one entry, or the
    run reports two sources where it read one.
    """
    result = outcome.result
    name = outcome.call.name

    if result.error is not None:
        failures.append(ToolFailure(tool_name=name, error=result.error))
        return

    if name == "web_search" and isinstance(result.data, WebSearchOutput):
        query = str(outcome.call.arguments.get("query", "")).strip()
        if query and query not in queries:
            queries.append(query)
        for hit in result.data.results:
            url = canonicalize_url(hit.url)
            # A lead an earlier hop already gathered is not new evidence; it is already in
            # `RunState`, and re-listing it would inflate `sources_examined`.
            if url is None or url in sources or url in assignment.avoid_urls:
                continue
            sources[url] = GatheredSource(
                url=url,
                title=hit.title[:_TITLE_CHARS],
                domain_class=hit.domain_class,
                snippet=hit.snippet[:_SNIPPET_CHARS],
            )
        return

    # `read_document` is absent on purpose. It reads the user's own attachment, which is not
    # a source anyone can cite — it has no URL and nothing to ground against. Its content
    # still reaches the model, through the observation `render_tool_outcome` builds.
    if name == "fetch_url" and isinstance(result.data, FetchUrlOutput):
        url = canonicalize_url(result.data.url)
        if url is None:
            return
        lead = sources.get(url)
        # A page the model opened without search having offered it is still evidence — it
        # was genuinely read. It keeps `domain_class="unknown"`, because no classified
        # search hit vouched for it and we do not upgrade authority on our own.
        sources[url] = GatheredSource(
            url=url,
            title=(result.data.title or (lead.title if lead else ""))[:_TITLE_CHARS],
            domain_class=lead.domain_class if lead else "unknown",
            snippet=(lead.snippet if lead else "")[:_SNIPPET_CHARS],
            excerpt=result.data.text[: settings.source_excerpt_chars],
            retrieved_at=result.data.retrieved_at,
        )


_TITLE_CHARS = 500
_SNIPPET_CHARS = 2000
"""`GatheredSource`'s own ceilings, applied where a source is built.

`NormalizedSource` and `FetchUrlOutput` bound neither field, so a long page title would
otherwise raise inside a step this module promises never to raise from — and losing a whole
hop's evidence to an over-long title is the worst possible trade.
"""


# --- stage 3: the sufficiency judgement (S7) ----------------------------------------------


async def judge_sufficiency(
    request: AppraisalRequest,
    *,
    provider: LLMProvider,
    ctx: RunContext,
    settings: Settings | None = None,
) -> AppraisalVerdict | None:
    """Do these sources support a useful session? Becomes `appraiser.judge()`.

    The one stage that must actually read the evidence, so it is handed `render_sources` —
    full excerpts at `SOURCE_EXCERPT_CHARS`, not the compact block `finalise.md` gets.

    It is told nothing about the budget or the hop count, deliberately: a judge that knows a
    follow-up is impossible stops asking for one, which would hide the real verdict instead
    of changing it. Whether a follow-up can be afforded is the loop's decision, taken after
    this returns.

    `None` when the budget refused or the model drifted twice; the loop finalises with the
    evidence it has rather than assuming a verdict nobody gave.
    """
    settings = settings or get_settings()
    prompt = render_prompt(
        "sufficiency",
        research_question=request.research_question,
        session_minutes=request.session_minutes,
        sources=render_sources(request.sources, settings=settings),
    )
    verdict = await _decode(
        provider,
        prompt,
        AppraisalVerdict,
        budget=ctx.budget,
        settings=settings,
        reserve=_FINALISE_RESERVE,
    )
    return verdict if isinstance(verdict, AppraisalVerdict) else None


# --- stage 4: finalisation (a single attempt; S10 adds the ladder) ------------------------


async def finalise(
    state: RunState,
    *,
    provider: LLMProvider,
    ctx: RunContext,
    stop_reason: StopReason = "sufficient",
    settings: Settings | None = None,
) -> FocusPreparationReport:
    """Write the report. Becomes `supervisor.finalise()`.

    One attempt today. S10 wraps this in the retry ladder — primary model, primary with the
    validation errors quoted back, then the second provider — and the shape here is already
    the one that ladder needs: the extra `Message` below is exactly the mechanism it uses,
    because `finalise.md`'s five placeholders are frozen and a sixth would break `main.py`'s
    `--no-research` path.

    Why the run stopped travels twice. Once as that message, so the model can write it into
    `unknowns` in its own words, and once as bookkeeping, because a model may ignore the
    message and a degraded run must never read like a confident one.

    Raises `PreparationFailed` rather than returning a partial report — including when the
    ledger refuses the call, which at this point means the run's deadline has passed.
    """
    settings = settings or get_settings()
    task = state.task
    note = render_stop_reason(stop_reason, exhausted=ctx.budget.exhausted_limits)

    messages = [
        Message(
            role="user",
            content=render_prompt(
                "finalise",
                task_title=task.task_title,
                task_description=task.task_description or "(none given)",
                session_minutes=task.session_minutes,
                max_topics=max_topics_for(task.session_minutes),
                research_context=render_research_context(state, settings=settings),
            ),
        )
    ]
    if note:
        messages.append(Message(role="user", content=note))

    if not ctx.budget.claim("model_call"):
        raise PreparationFailed(
            "the run ran out of time or model calls before a report could be written; "
            f"spent limits: {', '.join(ctx.budget.exhausted_limits) or 'none'}"
        )

    response = await provider.generate(
        messages, schema=FocusPreparationReport, temperature=settings.temperature
    )
    try:
        report = FocusPreparationReport.model_validate_json(response.text)
    except ValidationError as exc:
        raise PreparationFailed(
            f"the model's reply did not satisfy FocusPreparationReport:\n{_errors(exc)}"
        ) from exc

    return _apply_bookkeeping(report, state, ctx=ctx, model_used=response.model, note=note)


def _apply_bookkeeping(
    report: FocusPreparationReport,
    state: RunState,
    *,
    ctx: RunContext,
    model_used: str,
    note: str,
) -> FocusPreparationReport:
    """Overwrite everything the model does not get to decide.

    Provenance and identity are normal code, and `finalise.md` says as much so the model does
    not spend effort on them. The same reasoning as `main._apply_bookkeeping`, over a
    researched run instead of a sourceless one.

    `sources_examined` counts sources **read**, not discovered: `fetched_urls` is the set a
    citation may claim authority from, and counting leads would let a run that opened nothing
    report that it examined six things.

    `resources` is left exactly as the model wrote it. Checking that every cited URL was
    actually seen is S9's grounding rule, and doing it here as well would be a second
    definition of the project's strongest anti-hallucination guard.
    """
    unknowns = list(report.unknowns)
    if note and note not in unknowns:
        unknowns = ([note] + unknowns)[:6]

    return report.model_copy(
        update={
            "run_id": ctx.run_id,
            "generated_at": datetime.now(timezone.utc),
            "model_used": model_used,
            "original_task": state.task.task_title,
            "session_duration_minutes": state.task.session_minutes,
            "hops_used": state.hop,
            "sources_examined": len(state.fetched_urls),
            "unknowns": unknowns,
        }
    )


# --- the loop (S8) --------------------------------------------------------------------------


async def run_agent(
    task: TaskContext,
    *,
    registry: ToolRegistry,
    providers: AgentProviders | None = None,
    ctx: RunContext | None = None,
    settings: Settings | None = None,
) -> FocusPreparationReport:
    """Plan → act → observe → judge → stop, then write the report.

    Early stopping is the point, in both directions. A run stops the moment the appraiser
    says the evidence is enough, with hops still unspent — more sources is not the goal, one
    usable session is. And it stops when a further hop would be pointless or unaffordable,
    rather than spending one to look busy.

    Every exit lands on the same `finalise` call, carrying a `StopReason`. There is no path
    that returns nothing, no path that loops without a bound, and no path that lets a
    limitation go unmentioned in the report.
    """
    settings = settings or get_settings()
    providers = providers or AgentProviders.from_settings(settings)
    ctx = ctx or RunContext(budget=RunBudget.from_settings(settings))
    budget = ctx.budget
    state = RunState(task=task)
    stop: StopReason

    while True:
        if budget.hops_remaining(state.hop) == 0:
            # Deliberately before `decide_next_step`: `plan.md` is not written for a "you may
            # not research" case, and skipping it also saves a model call for the report.
            stop = "hop_cap"
            break
        if budget.remaining("search") == 0 and budget.remaining("fetch") == 0:
            stop = "budget_spent"
            break

        decision = await decide_next_step(
            state, provider=providers.supervisor, ctx=ctx, settings=settings
        )
        if decision is None:
            stop = "planner_unavailable"
            break
        if decision.action == "FINALISE":
            stop = "planner_finalised"
            break

        findings = await run_research_step(
            _assign(decision, state, budget),
            provider=providers.researcher,
            registry=registry,
            ctx=ctx,
            settings=settings,
            attachment_path=str(task.attachment_path) if task.attachment_path else None,
        )
        # Reassigned, never appended: `RunState` validates on assignment, and an in-place
        # append would slip straight past the `max_length=3` bound that the hop cap rests on.
        state.findings = [*state.findings, findings]
        state.hop = state.hop + 1

        verdict = await judge_sufficiency(
            AppraisalRequest(
                research_question=findings.research_question,
                session_minutes=task.session_minutes,
                sources=list(state.all_sources)[:64],
            ),
            provider=providers.appraiser,
            ctx=ctx,
            settings=settings,
        )
        if verdict is None:
            stop = "appraiser_unavailable"
            break
        state.verdict = verdict

        if verdict.sufficient:
            stop = "sufficient"
            break
        if not (verdict.requested_followup or "").strip():
            # "Not enough, and nothing specific would help" is an honest verdict, and the
            # answer to it is a report with populated `unknowns` — not another hop against a
            # question we would have had to invent.
            stop = "no_followup"
            break

    return await finalise(
        state,
        provider=providers.supervisor,
        ctx=ctx,
        stop_reason=stop,
        settings=settings,
    )


def _assign(
    decision: SupervisorDecision, state: RunState, budget: RunBudget
) -> ResearchAssignment:
    """The planner's decision, plus what the run can still afford, as one message.

    Self-contained because on Day 5 the Researcher receives this and nothing else. The
    allowances are sized from `remaining(...)` but do not enforce anything — `claim` stays
    the only enforcement point, and this is what the researcher is *told* it may spend.

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
