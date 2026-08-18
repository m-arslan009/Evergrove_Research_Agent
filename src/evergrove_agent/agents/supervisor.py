"""The Supervisor: decide, coordinate, and write the report (Day 5 T1).

Two stages moved verbatim from `single_agent.py` — `decide_next_step` (Day 3 S5) and
`finalise` with its retry ladder (S10) — plus `run_supervised`, the orchestration that makes
this role a supervisor rather than a third worker.

**What this role owns.** The run's state (`RunState`), the decision about what happens next,
the coordination of the two workers, and the final report. Everything the run knows lives
here; the workers are handed exactly one Pydantic message each and hand one back.

**What the workers may not do**, enforced by where the code lives rather than by a rule
someone has to remember: the Researcher never decides the run is over and never writes a
report (neither function exists in `researcher.py`), and the Appraiser never touches a tool
(it imports neither the registry nor `dispatch`).

**The workers never talk to each other.** `researcher.py` and `appraiser.py` import
`runtime` and downward only — never each other, never this module. Every hand-off is a return
value that passes through `_delegate_hop` below, so there is exactly one place where one
worker's output becomes another's input.

**This is not the only loop.** `single_agent.run_agent` runs the same four stages as one
agent, and `service.prepare_focus_session(mode=...)` chooses between them. That path is a
required Week 4 deliverable in its own right, so both stay green; the per-hop mechanics they
share (`_assign`, the memory calls) live once in `runtime.py`, and what is written twice is
only the control skeleton.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from pydantic import ValidationError

from evergrove_agent.agents.appraiser import judge_sufficiency
from evergrove_agent.agents.prompt_context import (
    max_topics_for,
    render_continuation_note,
    render_previous_preparation,
    render_progress,
    render_research_context,
    render_stop_reason,
)
from evergrove_agent.agents.researcher import run_research_step
from evergrove_agent.agents.runtime import (
    AgentProviders,
    PreparationFailed,
    StopReason,
    _assign,
    _decode,
    _errors,
    _FINALISE_RESERVE,
    _mirror_session_memory,
    _recall_previous_preparation,
    _remember_preparation,
)
from evergrove_agent.config import Settings, get_settings
from evergrove_agent.llm import LLMError, LLMProvider, Message
from evergrove_agent.llm.prompts import render_prompt
from evergrove_agent.schemas import (
    AppraisalRequest,
    AppraisalVerdict,
    FocusPreparationReport,
    ResearchFindings,
    RunState,
    SupervisorDecision,
    TaskContext,
)
from evergrove_agent.tools.base import RunBudget, RunContext
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tools.validate_report import ReportIssue, validate_report

# --- deciding what happens next (S5) ------------------------------------------------------


async def decide_next_step(
    state: RunState,
    *,
    provider: LLMProvider,
    ctx: RunContext,
    settings: Settings | None = None,
) -> SupervisorDecision | None:
    """Research one more thing, or write the report now. The Supervisor's decision.

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

    `None` when the budget refused or the model drifted twice; the caller finalises.
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


# --- finalisation and the retry ladder (S10) ----------------------------------------------


async def finalise(
    state: RunState,
    *,
    provider: LLMProvider,
    ctx: RunContext,
    stop_reason: StopReason = "sufficient",
    settings: Settings | None = None,
    fallback_provider: LLMProvider | None = None,
) -> FocusPreparationReport:
    """Write the report, and keep asking until it is a valid one. The Supervisor's stage.

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


# --- the orchestration: supervisor drives, workers answer (Day 5 T1) -----------------------


async def run_supervised(
    task: TaskContext,
    *,
    registry: ToolRegistry,
    providers: AgentProviders | None = None,
    ctx: RunContext | None = None,
    settings: Settings | None = None,
) -> FocusPreparationReport:
    """One run, coordinated by the Supervisor: decide → delegate → judge → stop → report.

    The multi-agent topology. Identical in behaviour to `single_agent.run_agent` — the same
    stop reasons, the same bounds, the same memory calls, the same report — and different in
    who performs each step. Here the Supervisor holds `RunState` and every decision, and the
    two workers are reached only through `_delegate_hop`, which is the single point where the
    Researcher's output becomes the Appraiser's input. Neither worker can see the other.

    That behavioural identity is deliberate and is the point of the split: Day 3 was written
    as four separately-prompted functions exchanging Pydantic models precisely so this could
    be a rearrangement rather than a rewrite. It is also what makes the two modes comparable —
    a difference between them is a difference in topology, never in what the run is allowed to
    do.

    **Memory is recalled once, here, before anything is decided (T5)**, exactly as the single
    loop does it: the Supervisor's planner and its report must not be able to disagree about
    what the last session prepared.

    Raises `PreparationFailed` when no valid report could be produced, and lets `LLMError`
    propagate. Both are the caller's to render.
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
        blocked = _stop_before_planning(state, budget)
        if blocked is not None:
            stop = blocked
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

        findings, verdict = await _delegate_hop(
            decision,
            state,
            registry=registry,
            providers=providers,
            ctx=ctx,
            settings=settings,
        )
        # Reassigned, never appended: `RunState` validates on assignment, and an in-place
        # append would slip straight past the `max_length=3` bound that the hop cap rests on.
        state.findings = [*state.findings, findings]
        state.hop = state.hop + 1
        if verdict is not None:
            state.verdict = verdict

        judged = _stop_after_hop(verdict)
        if judged is not None:
            stop = judged
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


async def _delegate_hop(
    decision: SupervisorDecision,
    state: RunState,
    *,
    registry: ToolRegistry,
    providers: AgentProviders,
    ctx: RunContext,
    settings: Settings,
) -> tuple[ResearchFindings, AppraisalVerdict | None]:
    """One hop, delegated: assign to the Researcher, then hand its evidence to the Appraiser.

    **The only place a worker's output reaches the other worker**, and it does so through the
    Supervisor's own frame. `researcher.py` and `appraiser.py` cannot import each other, so
    this function is structurally the whole channel between them — which is what "the workers
    never communicate directly" means in practice rather than as a slogan.

    Each worker is handed exactly the Pydantic message its role is defined by:
    `ResearchAssignment` in, `ResearchFindings` back; `AppraisalRequest` in,
    `AppraisalVerdict` back. Neither is given the run state, the budget, or the other's
    provider.

    The Appraiser judges everything the run has gathered (`state.all_sources` plus this hop's
    findings), not only this hop's — a verdict on one hop in isolation would keep asking for
    material an earlier hop already found.

    The hop is mirrored into `run_memory` whether or not it was judged: a hop that gathered
    evidence and then lost its appraiser is exactly the run someone will want the history of.
    """
    findings = await run_research_step(
        _assign(decision, state, ctx.budget),
        provider=providers.researcher,
        registry=registry,
        ctx=ctx,
        settings=settings,
        attachment_path=(
            str(state.task.attachment_path) if state.task.attachment_path else None
        ),
    )

    verdict = await judge_sufficiency(
        AppraisalRequest(
            research_question=findings.research_question,
            session_minutes=state.task.session_minutes,
            sources=[*state.all_sources, *findings.sources][:64],
        ),
        provider=providers.appraiser,
        ctx=ctx,
        settings=settings,
    )

    await _mirror_session_memory(
        registry, ctx, decision=decision, findings=findings, verdict=verdict
    )
    return findings, verdict


def _stop_before_planning(state: RunState, budget: RunBudget) -> StopReason | None:
    """Whether this run may plan another hop at all, checked before the planner is asked.

    Deliberately before `decide_next_step`: `plan.md` is not written for a "you may not
    research" case, and skipping it also saves a model call for the report.
    """
    if budget.hops_remaining(state.hop) == 0:
        return "hop_cap"
    if budget.remaining("search") == 0 and budget.remaining("fetch") == 0:
        return "budget_spent"
    return None


def _stop_after_hop(verdict: AppraisalVerdict | None) -> StopReason | None:
    """What the Appraiser's answer means for the run, or `None` to keep going.

    `None` from the Appraiser is not a verdict of "insufficient" — it means the stage could
    not answer, and the honest response is to stop and finalise with what was gathered rather
    than to spend another hop against a question nobody asked for.
    """
    if verdict is None:
        return "appraiser_unavailable"
    if verdict.sufficient:
        return "sufficient"
    if not (verdict.requested_followup or "").strip():
        # "Not enough, and nothing specific would help" is an honest verdict, and the answer
        # to it is a report with populated `unknowns` — not another hop against a question we
        # would have had to invent.
        return "no_followup"
    return None
