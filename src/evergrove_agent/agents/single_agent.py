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
`_MAX_DECODE_ATTEMPTS`, the report's retry ladder by `MAX_OUTPUT_RETRIES`, and every model
call and tool call by the ledger.

**A report is not finished until the evidence says so.** `finalise` runs S9's
`validate_report` against the run's own gathered URLs and asks again, up to
`MAX_OUTPUT_RETRIES` times, quoting back exactly what was wrong. A run that cannot produce a
valid report raises `PreparationFailed`; it never returns an invalid one.

**Memory is written best-effort, and read exactly once (Day 4 T4/T5).** Three registry calls
sit in this loop. Two write: one mirrors each finished hop into `run_memory`, one saves the
validated report to `prep_memory`. Neither changes a decision. The third *reads* — a single
`recall_previous_preparation` before the loop starts, whose answer rides on `RunState.previous`
into the planner's prompt and into `finalise`'s, so a second session for a task continues the
first instead of repeating it. All three go through the registry, so all three are traced and
none can raise; **a memory outage costs a row or a continuation, never a run**, and a run with
nothing to recall behaves exactly as it did before T5.

**Cross-run and within-run memory stay separate.** `RunState.previous` is what an *earlier*
run prepared; `RunState`'s findings, `used_queries` and `evidence_urls` are what *this* run has
seen, and they are still the only session memory the loop runs on. Nothing here reads a
decision back out of SQLite.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ValidationError

from evergrove_agent.agents.prompt_context import (
    max_topics_for,
    render_allowance,
    render_already_covered,
    render_attachment,
    render_available_tools,
    render_continuation_note,
    render_previous_preparation,
    render_progress,
    render_research_context,
    render_sources,
    render_stop_reason,
    render_tool_outcome,
    render_turn_state,
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
from evergrove_agent.memory.run_memory import entries_from
from evergrove_agent.schemas import (
    AppraisalRequest,
    AppraisalVerdict,
    FocusPreparationReport,
    GatheredSource,
    PreviousPreparation,
    ResearchAction,
    ResearchAssignment,
    ResearchFindings,
    RunState,
    SupervisorDecision,
    TaskContext,
    ToolFailure,
)
from evergrove_agent.search.normalize import canonicalize_url
from evergrove_agent.tools.base import RunBudget, RunContext
from evergrove_agent.tools.fetch_url import FetchUrlOutput
from evergrove_agent.tools.memory_tools import (
    RecallInput,
    RecallOutput,
    RecordRunMemoryInput,
    SavePreparationInput,
)
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tools.validate_report import ReportIssue, validate_report
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

    **Two memories reach this stage, and they answer different questions (T5).**
    `render_progress` says what *this* run has already done; `render_previous_preparation`
    says what an *earlier* run prepared, so a hop is not spent re-establishing material a
    previous session settled. The second block is empty whenever nothing was recalled, which
    is what keeps a fresh task's prompt the one it has always been.

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
        previous_preparation=render_previous_preparation(state.previous),
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

    **A turn is one constrained `ResearchAction`, not free-form tool calling** — Day 3's
    contingency option (2), spent during S14 on measurement rather than on a reliability
    failure: free-form calls were *correct* on `qwen3:4b` but cost 361 s a turn against 46 s
    for a constrained call, because nothing stopped the model prefixing ~4 000 characters of
    reasoning. `dispatch` still receives a `ToolCall` and arguments still travel as a raw
    mapping, so this is the same tool path with a cheaper decision in front of it.

    The hop ends early on any of: an empty `tool` (the model saying it has enough), a reply
    that does not fit the schema, a refused budget claim, or an unreachable model.

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
                messages, schema=ResearchAction, temperature=settings.temperature
            )
        except LLMError as exc:
            # Not fatal here, unlike every other stage: this hop already holds evidence
            # worth keeping, and throwing it away would make the report thinner than the
            # run actually was. If the provider is genuinely down, `finalise` says so.
            notes = notes or f"The research step stopped early: {exc}"
            break

        try:
            action = ResearchAction.model_validate_json(response.text)
        except ValidationError:
            # Drift ends the hop, exactly as prose instead of a tool call used to. There is
            # no re-ask here on purpose: `_decode`'s retry exists for stages whose answer
            # steers control flow, while a hop that stops early still returns its findings.
            break

        if action.reasoning.strip():
            notes = action.reasoning.strip()
        if not action.tool.strip():
            break

        # One call per turn. `dispatch` still receives a `ToolCall` and the arguments still
        # travel as the raw mapping they arrived in, so the registry remains the only
        # argument validator and this is not a second tool path.
        calls = [ToolCall(name=action.tool.strip(), arguments=dict(action.arguments))]

        outcomes: list[ToolCallOutcome] = []
        for call in calls:
            # The registry's pre-hook claims the counter and refuses the call when the run
            # cannot afford it (Day 4 T3), so the loop no longer pays for tools itself.
            result = await dispatch(call, registry=registry, ctx=ctx, allowed=allowed)
            outcome = ToolCallOutcome(call=call, result=result)
            outcomes.append(outcome)
            _absorb(outcome, assignment, sources, queries, failures, settings)

        # The observation carries the hop's *current* state, not the opening state the
        # prompt was rendered with. Without this the model re-asks the question it just
        # asked: S14 measured three identical `web_search` calls in one hop, which the
        # cache answered for free while still spending all three search claims and leaving
        # `fetch_url` untouched.
        observation = "\n\n".join(
            render_tool_outcome(outcome, settings=settings) for outcome in outcomes
        )
        messages = [
            *messages,
            Message(role="assistant", content=response.text or _ASKED_FOR_TOOLS),
            Message(
                role="user",
                content=observation
                + "\n\n"
                + render_turn_state(
                    searches_left=budget.remaining("search"),
                    fetches_left=budget.remaining("fetch"),
                    queries=queries,
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


# --- stage 4: finalisation and the retry ladder (S10) -------------------------------------


async def finalise(
    state: RunState,
    *,
    provider: LLMProvider,
    ctx: RunContext,
    stop_reason: StopReason = "sufficient",
    settings: Settings | None = None,
    fallback_provider: LLMProvider | None = None,
) -> FocusPreparationReport:
    """Write the report, and keep asking until it is a valid one. Becomes
    `supervisor.finalise()`.

    **`MAX_OUTPUT_RETRIES` counts total attempts, not retries** — the shipped 3 is one
    initial call plus two corrections. The name is the plan's and is kept deliberately;
    this paragraph is where the mismatch is recorded, because changing the meaning silently
    would move a budget nobody re-approved.

    Each attempt is checked three times over: by constrained decoding against the JSON
    schema, by `model_validate_json`, and then by S9's `validate_report`, the only one of the
    three that can see what the run actually gathered. A failure of either of the last two is
    the same kind of event — *this report is not acceptable* — so both feed one ladder, and
    the reason is quoted back as an extra `Message`. Never as a placeholder: `finalise.md`'s
    five are frozen, and a sixth is a `KeyError` on `main.py`'s `--no-research` path.

    **The retry corrects the report, never the research.** No search, no fetch, no new
    evidence: a validation failure means the report broke our contract, not that the run
    needs redoing. Task, narrowed goal, sources, findings, verdict, failures and the
    grounding set are identical on every attempt; the only thing that changes is what the
    model has been told about its own last answer.

    The last attempt goes to `fallback_provider` when one exists, on the reasoning that a
    model which has already failed twice against the same evidence will fail a third time.

    Why the run stopped travels twice. Once as an extra message, so the model can write it
    into `unknowns` in its own words, and once as bookkeeping, because a model may ignore the
    message and a degraded run must never read like a confident one.

    **A recalled preparation travels once, and only as a request (T5).** It reaches the model
    as a further extra message and is never enforced afterwards: `_apply_bookkeeping`
    overwrites provenance, never `interpreted_goal` or `topics_to_cover`. A continuation the
    model declined is a decision worth reading in the trace, and a report we rewrote would
    hide it — the same reason `resources` is left exactly as the model wrote it.

    Raises `PreparationFailed` rather than returning a partial report: when the ledger
    refuses an attempt, when the alternate provider cannot be reached, and when every attempt
    was spent without a valid report.
    """
    settings = settings or get_settings()
    task = state.task
    note = render_stop_reason(stop_reason, exhausted=ctx.budget.exhausted_limits)

    opening = [
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
        opening.append(Message(role="user", content=note))
    # The same extra-message mechanism, for the same reason: `finalise.md`'s five
    # placeholders are frozen. It rides in the *opening* turns so every rung of the retry
    # ladder argues with a model that was told about the earlier session (T5).
    continuation = render_continuation_note(state.previous)
    if continuation:
        opening.append(Message(role="user", content=continuation))

    messages = list(opening)
    attempts = settings.max_output_retries
    issues: tuple[ReportIssue, ...] = ()
    last_failure = ""

    for attempt in range(1, attempts + 1):
        # Only the *last* attempt switches, and only when there is more than one: the first
        # attempt belongs to the configured provider whatever `MAX_OUTPUT_RETRIES` says.
        use_fallback = (
            fallback_provider is not None and attempts > 1 and attempt == attempts
        )
        current = fallback_provider if use_fallback else provider

        if not ctx.budget.claim("model_call"):
            raise PreparationFailed(
                _exhausted_message(ctx, attempt - 1, issues),
                run_id=ctx.run_id,
                attempts=attempt - 1,
                issues=issues,
            )

        try:
            response = await current.generate(
                messages, schema=FocusPreparationReport, temperature=settings.temperature
            )
        except LLMError as exc:
            if not use_fallback:
                # The primary being unreachable is a broken run, and that asymmetry with
                # `ToolError` is deliberate project-wide.
                raise
            raise PreparationFailed(
                f"the alternate provider could not be reached on the final attempt: {exc}"
                + _issue_block(issues),
                run_id=ctx.run_id,
                attempts=attempt,
                issues=issues,
            ) from exc

        try:
            parsed = FocusPreparationReport.model_validate_json(response.text)
        except ValidationError as exc:
            # A drifted shape and a broken rule are the same event to the caller, so they
            # share the ladder. The shape errors are not `ReportValidation` issues, though —
            # they are quoted to the model but never claimed as a verdict on a report that
            # never existed. **Both records are cleared**, because an earlier attempt's
            # verdict is not this one's: reporting it as final would send a caller looking
            # for a citation problem in a reply that never parsed.
            issues = ()
            state.validation_errors = []
            last_failure = "the model's reply did not fit FocusPreparationReport"
            messages = _with_correction(opening, response.text, _errors(exc))
            continue

        report = _apply_bookkeeping(
            parsed, state, ctx=ctx, model_used=response.model, note=note
        )
        # Validated *after* bookkeeping, on purpose: `original_task`,
        # `session_duration_minutes` and the appended `unknowns` note are all fields
        # `validate_report` reads, and all fields `finalise.md` tells the model not to bother
        # filling in. Judging the raw reply would reject reports for our own omissions.
        validation = validate_report(
            report,
            evidence_urls=state.evidence_urls,
            fetched_urls=state.fetched_urls,
            max_topics=max_topics_for(task.session_minutes),
            research_performed=bool(state.findings),
        )
        if validation.ok:
            return report

        issues = tuple(validation.issues)
        state.validation_errors = validation.as_lines()[:20]
        last_failure = "the report did not match the evidence this run gathered"
        messages = _with_correction(
            opening, response.text, "\n".join(validation.as_lines())
        )

    raise PreparationFailed(
        f"no valid report after {_attempt_count(attempts)}: {last_failure}"
        + _issue_block(issues),
        run_id=ctx.run_id,
        attempts=attempts,
        issues=issues,
    )


def _attempt_count(n: int) -> str:
    """`1 attempt` / `3 attempts`.

    The ladder's bound is configurable and ends up in two different failure messages, so
    `1 attempts` is reachable rather than hypothetical.
    """
    return f"{n} attempt{'' if n == 1 else 's'}"


def _with_correction(opening: list[Message], reply: str, errors: str) -> list[Message]:
    """The next attempt's turns: what was asked, what came back, and what was wrong with it.

    The same shape `_decode` uses for a drifted decision, for the same reason — a model
    cannot correct an answer it cannot see. Built from the *opening* turns each time rather
    than appended to the previous attempt, so the third attempt argues with the second answer
    instead of wading through the first one as well. The evidence and the stop reason live in
    those opening turns and travel unchanged, which is what keeps every attempt grounded in
    exactly the same run.
    """
    return [
        *opening,
        Message(role="assistant", content=reply),
        Message(role="user", content=_VALIDATION_INSTRUCTION.format(errors=errors)),
    ]


_VALIDATION_INSTRUCTION = (
    "That report is not acceptable:\n{errors}\n"
    "Fix exactly these problems and answer again with the whole report as JSON matching "
    "the schema you were given. Change nothing else, and do not cite any source that is "
    "not listed in the research section above."
)
"""The correction turn. It says *fix these* rather than *try again* because the model has
already produced its best unaided attempt; and it repeats the citation rule because the
issue it most often has to fix is a URL the run never saw, which a model will otherwise
replace with a second invented one."""


def _issue_block(issues: Sequence[ReportIssue]) -> str:
    """The final validation errors, for the exception's own message.

    `PreparationFailed.issues` carries them structurally; this is so a bare `print(exc)` in a
    terminal still says what was wrong, which is all a failing run gets until S12.
    """
    if not issues:
        return ""
    lines = "\n".join(f"- {issue.field}: {issue.message}" for issue in issues)
    return f"\nlast validation errors:\n{lines}"


def _exhausted_message(
    ctx: RunContext, made: int, issues: Sequence[ReportIssue]
) -> str:
    """Why the ladder stopped early, distinguishing "no room" from "no valid report".

    A run that never got to ask and a run that asked and was refused fail for different
    reasons, and the difference is the first thing anyone reading the failure needs.
    """
    spent = ", ".join(ctx.budget.exhausted_limits) or "none"
    if made == 0:
        return (
            "the run ran out of time or model calls before a report could be written; "
            f"spent limits: {spent}"
        )
    return (
        f"the run ran out of time or model calls after {_attempt_count(made)} at the "
        f"report; spent limits: {spent}" + _issue_block(issues)
    )


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
    definition of the project's strongest anti-hallucination guard. `finalise` runs that
    check on the report this returns — after these fields are set, because three of the rules
    read them.
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

    **Memory is recalled once, here, before anything is decided (T5).** One lookup for the
    whole run: the planner and the report must not be able to disagree about what the last
    session did, and a per-stage lookup would also charge a 9-15 minute run several database
    reads to answer one question. It happens before the first `decide_next_step` because the
    very first decision — what this session is even about — is the one a continuation changes
    most.
    """
    settings = settings or get_settings()
    providers = providers or AgentProviders.from_settings(settings)
    ctx = ctx or RunContext(budget=RunBudget.from_settings(settings))
    budget = ctx.budget
    state = RunState(
        task=task,
        previous=await _recall_previous_preparation(registry, ctx, task, settings),
    )
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
        if verdict is not None:
            state.verdict = verdict

        # The hop is mirrored whether or not it was judged: a hop that gathered evidence and
        # then lost its appraiser is exactly the run someone will want the history of.
        await _mirror_session_memory(
            registry, ctx, decision=decision, findings=findings, verdict=verdict
        )

        if verdict is None:
            stop = "appraiser_unavailable"
            break

        if verdict.sufficient:
            stop = "sufficient"
            break
        if not (verdict.requested_followup or "").strip():
            # "Not enough, and nothing specific would help" is an honest verdict, and the
            # answer to it is a report with populated `unknowns` — not another hop against a
            # question we would have had to invent.
            stop = "no_followup"
            break

    report = await finalise(
        state,
        provider=providers.supervisor,
        ctx=ctx,
        stop_reason=stop,
        settings=settings,
        fallback_provider=providers.fallback,
    )
    # The one place a validated report exists: `finalise` returns one or raises, so this line
    # is what makes "an invalid preparation is never remembered" structural rather than a rule
    # somebody has to follow.
    await _remember_preparation(registry, ctx, report)
    return report


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
