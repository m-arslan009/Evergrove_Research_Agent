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

**The topology is recorded, not merely asserted (Day 5 T6).** Every claim above is a
statement about where code lives, and the trace is where it becomes checkable after the
fact: `supervisor.run` holds the whole coordination, `supervisor.decide`, `researcher.loop`,
`appraiser.judge` and `supervisor.finalise` are its children, and a worker's tool calls nest
under that worker because `Tracer.open_span` derives a parent from `RunContext`'s stack.
Nothing is passed down to make that happen and `tools/hooks.py` is untouched — the spans
opened here are the only new thing. The `tracer` is a parameter rather than a field on
`RunContext`, which holds identifiers and never I/O; `service.py` owns it and hands it in.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from pydantic import ValidationError

from evergrove_agent.agents.appraiser import judge_sufficiency
from evergrove_agent.agents.prompt_context import (
    max_topics_for,
    render_continuation_note,
    render_missing_context,
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
    _RESEARCH_RESERVE,
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
from evergrove_agent.tracing import Tracer, agent_span

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
    # Third and last of the conditional opening turns, and it rides in the opening for the
    # reason the other two do: every rung of the retry ladder must argue with a model that
    # was told the task is underspecified. A correction attempt that lost this instruction
    # would be free to invent exactly what the run stopped in order not to invent.
    gaps = render_missing_context(state.missing_context)
    if gaps:
        opening.append(Message(role="user", content=gaps))

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
    # What the task never said, recorded whatever the model wrote. The same stance the stop
    # note takes and for the same reason: the instruction to report these is a request, and a
    # request a model declined must not be the only thing standing between a user and the
    # fact that this plan was built without knowing their setup. Prepended after the note, so
    # the six-item cap keeps the most load-bearing lines rather than the model's own.
    for gap in reversed(state.missing_context):
        line = f"Not stated in the task, so this plan does not assume it: {gap}"
        if line not in unknowns:
            unknowns = ([line] + unknowns)[:6]
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
    tracer: Tracer | None = None,
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

    **Whether the run continues is the Appraiser's answer, not the Supervisor's opinion.**
    `_stop_after_hop` reads the verdict and either names a stop reason or says to hop again;
    when it says to hop again the planner is skipped entirely and the assignment is built from
    `AppraisalVerdict.requested_followup`, so there is no second model call in which the
    coordinator could talk itself out of the judge's finding. The planner still decides the
    *first* question — there is no verdict to defer to before any evidence exists — and every
    bound still applies to a hop the Appraiser asked for: `_stop_before_planning` runs first,
    so `MAX_HOPS` and the search and fetch ceilings cap a forced hop exactly as they cap a
    planned one.

    **Memory is recalled once, here, before anything is decided**, exactly as the single
    loop does it: the Supervisor's planner and its report must not be able to disagree about
    what the last session prepared.

    **The whole coordination is one `supervisor.run` span (T6)**, and every other span this
    run produces is inside it — the two workers' spans, the Supervisor's own two stages, and
    the three memory tool calls, which sit directly under this one because filing a hop away
    is the coordinator's bookkeeping rather than any worker's work. That is what makes "the
    Supervisor is the coordination point" a property of the recorded tree instead of a
    sentence in a docstring.

    Raises `PreparationFailed` when no valid report could be produced, and lets `LLMError`
    propagate. Both are the caller's to render.
    """
    settings = settings or get_settings()
    providers = providers or AgentProviders.from_settings(settings)
    ctx = ctx or RunContext(budget=RunBudget.from_settings(settings))
    budget = ctx.budget
    stop: StopReason

    with agent_span(
        tracer, ctx, "supervisor.run", input_summary=task.task_title
    ) as coordination:
        state = RunState(
            task=task,
            previous=await _recall_previous_preparation(registry, ctx, task, settings),
        )

        while True:
            blocked = _stop_before_planning(state, budget)
            if blocked is not None:
                stop = blocked
                break

            followup = _outstanding_followup(state.verdict)
            if followup is not None:
                # The Appraiser asked for this hop, so the Supervisor does not re-decide
                # whether to take it. The planner is not consulted at all here — there is no
                # model call in which a `FINALISE` could be produced over the top of the
                # verdict (T5). It is therefore also not a `supervisor.decide` span: the
                # trace would then show a decision that was never taken.
                if not _can_afford_a_hop(budget):
                    stop = "budget_spent"
                    break
                decision = _followup_decision(followup)
            else:
                with agent_span(
                    tracer,
                    ctx,
                    "supervisor.decide",
                    input_summary=f"hop {state.hop + 1}: {task.task_title}",
                ) as span:
                    decision = await decide_next_step(
                        state, provider=providers.supervisor, ctx=ctx, settings=settings
                    )
                    span.summarise(_decision_summary(decision))
                if decision is None:
                    stop = "planner_unavailable"
                    break
                # Before the FINALISE check, so a decision that names missing context stops
                # the run whichever action it carried. Recorded on the state rather than
                # passed to `finalise`, which reads it from the same object it already has.
                unsupported = _stop_for_missing_context(decision)
                if unsupported is not None:
                    state.missing_context = list(decision.missing_context) or [
                        UNSPECIFIED_SITUATION
                    ]
                    stop = unsupported
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
                tracer=tracer,
            )
            # Reassigned, never appended: `RunState` validates on assignment, and an in-place
            # append would slip straight past the `max_length=3` bound the hop cap rests on.
            state.findings = [*state.findings, findings]
            state.hop = state.hop + 1
            if verdict is not None:
                state.verdict = verdict

            judged = _stop_after_hop(verdict)
            if judged is not None:
                stop = judged
                break

        with agent_span(
            tracer, ctx, "supervisor.finalise", input_summary=f"stop reason: {stop}"
        ) as span:
            report = await finalise(
                state,
                provider=providers.supervisor,
                ctx=ctx,
                stop_reason=stop,
                settings=settings,
                fallback_provider=providers.fallback,
            )
            span.summarise(
                f"{len(report.resources)} resources cited, written by {report.model_used}"
            )
        # The one place a validated report exists: `finalise` returns one or raises, so this
        # line is what makes "an invalid preparation is never remembered" structural rather
        # than a rule somebody has to follow.
        await _remember_preparation(registry, ctx, report)
        coordination.summarise(f"{state.hop} hop(s), stopped on {stop}")
        return report


def _decision_summary(decision: SupervisorDecision | None) -> str:
    """The planner's answer as one trace line (T6).

    `None` is recorded as what it means — the stage could not answer — rather than left
    blank, because a `supervisor.decide` span with no summary reads as a decision nobody
    bothered to write down, and this is the exact shape that ends a run on
    `planner_unavailable`.
    """
    if decision is None:
        return "no decision: the planner could not answer"
    if decision.missing_context:
        # Recorded ahead of the action, because it is what the run actually did: a decision
        # naming missing context never becomes a hop whatever its `action` said, and a trace
        # showing `RESEARCH` for a run that researched nothing describes the wrong run.
        return "declined to guess; not stated: " + "; ".join(decision.missing_context)
    return f"{decision.action}: {decision.research_question or '(no question)'}"


async def _delegate_hop(
    decision: SupervisorDecision,
    state: RunState,
    *,
    registry: ToolRegistry,
    providers: AgentProviders,
    ctx: RunContext,
    settings: Settings,
    tracer: Tracer | None = None,
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

    **This is also where the trace's two worker spans open (T6)**, one around each delegation
    and neither inside the other. Every tool the Researcher reaches nests under
    `researcher.loop` with nothing passed down, because the registry's tracing pre-hook takes
    its parent from `RunContext`'s stack — and `appraiser.judge` has no tool children for the
    same reason it has no `dispatch` import: there is no path from that stage to a tool. The
    mirror below sits outside both, under `supervisor.run`, because recording the hop is the
    coordinator's act rather than either worker's.
    """
    assignment = _assign(decision, state, ctx.budget)
    with agent_span(
        tracer, ctx, "researcher.loop", input_summary=assignment.research_question
    ) as span:
        findings = await run_research_step(
            assignment,
            provider=providers.researcher,
            registry=registry,
            ctx=ctx,
            settings=settings,
            attachment_path=(
                str(state.task.attachment_path) if state.task.attachment_path else None
            ),
        )
        span.summarise(
            f"{len(findings.sources)} sources from {len(findings.queries_used)} "
            f"queries, {len(findings.failures)} tool failures"
        )

    request = AppraisalRequest(
        research_question=findings.research_question,
        session_minutes=state.task.session_minutes,
        sources=[*state.all_sources, *findings.sources][:64],
    )
    with agent_span(
        tracer,
        ctx,
        "appraiser.judge",
        input_summary=f"{len(request.sources)} sources: {request.research_question}",
    ) as span:
        verdict = await judge_sufficiency(
            request,
            provider=providers.appraiser,
            ctx=ctx,
            settings=settings,
        )
        span.summarise(_verdict_summary(verdict))

    await _mirror_session_memory(
        registry, ctx, decision=decision, findings=findings, verdict=verdict
    )
    return findings, verdict


def _verdict_summary(verdict: AppraisalVerdict | None) -> str:
    """The Appraiser's answer as one trace line (T6).

    Deliberately the three fields the loop acts on — `sufficient`, how many sources were
    accepted (the second half of the stop condition since T5), and whether a follow-up was
    asked for. The full semantic judgement already reaches `run_memory` through
    `runtime._appraisal_line`, and a span summary is bounded at `TRACE_SUMMARY_CHARS`: this
    line exists so a reader can see *why the run continued or stopped* at the point it was
    decided, not to hold a second copy of the verdict.
    """
    if verdict is None:
        return "no verdict: the appraiser could not answer"
    followup = (verdict.requested_followup or "").strip()
    return (
        f"sufficient={verdict.sufficient}, accepted={len(verdict.accepted)}"
        + (f", follow-up: {followup}" if followup else "")
    )


def _stop_for_missing_context(decision: SupervisorDecision) -> StopReason | None:
    """Whether the planner declined to guess, in which case no worker is given the task.

    **This is the whole of the fix for assumption-based research, and it is deliberately in
    code rather than in a prompt.** `plan.md` asks the planner not to invent a setting the
    task never stated; this is what makes the refusal *binding*. A decision that names
    missing context never reaches `_assign`, so it cannot become a `ResearchAssignment`, a
    `web_search` query, a set of sources, or a report written as though the guess were given.

    Read **regardless of `action`**, and that asymmetry is the point. A model that fills
    `missing_context` and still answers `RESEARCH` has told us two things and only one of
    them is safe to act on: the honest half wins, because the cost of stopping is a report
    that says what it needs, while the cost of continuing is a plan built on a premise
    nobody supplied. The run still finalises — `unknowns` carries the gap — so nothing is
    lost except a hop that would have researched the wrong question.

    **Either signal is enough.** `context_check == "MISSING"` and a non-empty
    `missing_context` are one judgement expressed twice, and a model that answers only one of
    them has still answered. Requiring both would discard the honest half of a reply for
    being incomplete — and it is the *list* a small model drops, which is why the binary is
    the one that must not be ignored.

    `ENOUGH` with nothing listed is the ordinary case, which is what keeps every
    well-specified task on exactly the path it took before this existed.
    """
    if decision.context_check == "MISSING" or decision.missing_context:
        return "missing_context"
    return None


UNSPECIFIED_SITUATION = (
    "what this task applies to — the situation it is about was never described"
)
"""What a run reports when the planner said `MISSING` but listed nothing.

A neutral statement of the shape of the gap, never a guess at its content: it says only that
something was not described, which is exactly what the model told us. The alternative is a
report that stops without saying why, and "the run declined to guess" is not information a
user can act on."""


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

    **This is the whole of the stop/continue decision, and it is the Appraiser's (T5).** The
    plan states the condition precisely (section 8.3): the run stops when the verdict is
    `sufficient` with at least `_MIN_ACCEPTED` accepted sources, when the hop cap is reached,
    or when the budget is spent; it continues for exactly one more hop when the verdict is
    *not* sufficient, `requested_followup` is non-empty, and a hop is still allowed. The first
    two of those live here; the other two are `_stop_before_planning`'s, checked before this
    answer is acted on. Nothing else may end a run that this function said to continue — see
    `_outstanding_followup`.

    `None` from the Appraiser is not a verdict of "insufficient" — it means the stage could
    not answer, and the honest response is to stop and finalise with what was gathered rather
    than to spend another hop against a question nobody asked for.
    """
    if verdict is None:
        return "appraiser_unavailable"
    if verdict.sufficient:
        # `sufficient` alone is not the stop condition, and this is the one place T4's
        # "the semantic lists inform, they never decide" no longer holds. A model that says
        # "yes" while accepting one source or none has not met the plan's bar, and the honest
        # answer is a report that says so — not another hop, because the verdict named no
        # follow-up to spend one on.
        return "sufficient" if len(verdict.accepted) >= _MIN_ACCEPTED else "thin_evidence"
    if not (verdict.requested_followup or "").strip():
        # "Not enough, and nothing specific would help" is an honest verdict, and the answer
        # to it is a report with populated `unknowns` — not another hop against a question we
        # would have had to invent.
        return "no_followup"
    return None


_MIN_ACCEPTED = 2
"""Accepted sources a `sufficient` verdict needs before the run may stop on it.

The plan's own number ("`sufficient == true` **and at least 2 accepted sources exist**"). Two
rather than one because a single accepted source is exactly the shape of the failure the
Appraiser exists to catch — the system believing its own first search result."""


def _outstanding_followup(verdict: AppraisalVerdict | None) -> str | None:
    """The question the Appraiser asked for, or `None` when it asked for nothing.

    **Non-`None` exactly when `_stop_after_hop` returned `None`.** The two are two readings of
    one verdict and must never disagree: the loop stops on the first and hops on the second,
    so a gap between them either strands the run or lets a stale verdict reach the planner.
    They are deliberately kept as separate small functions rather than one that returns both,
    because each has exactly one caller and one meaning; the equivalence is pinned by a test
    (`tests/unit/test_supervisor.py`) rather than by being structurally impossible to break.

    Whitespace is stripped and an all-blank follow-up counts as none, for the same reason
    `_stop_after_hop` strips it: a model that answers `" "` has not asked a question.
    """
    if verdict is None or verdict.sufficient:
        return None
    return (verdict.requested_followup or "").strip() or None


def _followup_decision(followup: str) -> SupervisorDecision:
    """The Appraiser's follow-up, as the assignment for the next hop (T5).

    **The Supervisor does not get a second opinion on a hop the Appraiser asked for.** The
    planner is not consulted at all on this path, which is what makes "the multi-hop decision
    is derived from the verdict" structural: there is no model call in which a `FINALISE`
    could be produced, so none can override the judgement. It also saves a model call on a
    ten-call budget, which is the difference between two fully-judged hops and a run that
    cannot afford its own report.

    **The question is still never written by our code.** `research_question` is the
    Appraiser's own text, drawn from content that did not exist before the hop ran — which is
    what makes the second hop agentic rather than a scripted retry. Only the two fields the
    Appraiser has no opinion about are supplied here: `source_preference` takes
    `SupervisorDecision`'s own default rather than inheriting the previous hop's, because a
    follow-up is by definition about something the last question did not cover; and
    `reasoning` is a trace line, read by a human and never parsed.

    `requested_followup` is bounded at 200 characters and `research_question` allows 300, so
    this cannot fail validation on length. It cannot fail on emptiness either: the only caller
    passes `_outstanding_followup`'s output, which is never blank.
    """
    return SupervisorDecision(
        action="RESEARCH",
        research_question=followup,
        reasoning="the appraiser judged the evidence insufficient and asked for this",
    )


def _can_afford_a_hop(budget: RunBudget) -> bool:
    """Whether a hop the Appraiser asked for can still be paid for in model calls.

    Only the follow-up path needs this, and only because it skips the planner. On the planned
    path `decide_next_step` answers `None` when the ledger refuses it and the loop stops with
    `planner_unavailable`, so an exhausted budget is caught before any tool runs. Skipping the
    planner skips that guard too — and without a replacement a forced hop would spend real
    searches and fetches and then have nothing left to judge them with, which is the one
    outcome `_RESEARCH_RESERVE` exists to prevent.

    The same reserve, asked as a question instead of claimed: `claim` remains the only
    enforcement point, and this only decides whether to reach it.
    """
    return budget.remaining("model_call") > _RESEARCH_RESERVE
