"""The single research agent: plan → act → observe → judge → stop (Day 3 S5-S8).

**One agent runs all four stages.** That is the whole difference between this module and
`supervisor.py`: the same four stage functions, the same bounds, the same memory calls and
the same report, driven by one loop that calls each stage directly instead of by a Supervisor
that delegates two of them to workers. `service.prepare_focus_session(mode="single")` selects
this path; `mode="multi"` selects the other.

**It is retained deliberately, not left behind.** "A single agent answers multi-hop questions
using all of the above" is a Week 4 deliverable in its own right, distinct from the Week 5
supervisor requirement, and both must be demonstrable. Keeping it also gives the multi-agent
path something to be compared against, and a fallback if that path misbehaves.

**Day 5 T1 moved the stages out; the loop below is unchanged.** `decide_next_step` and
`finalise` now live in `supervisor.py`, `run_research_step` in `researcher.py`,
`judge_sufficiency` in `appraiser.py`, and everything they share in `runtime.py`. They are
imported back here and re-exported through `__all__`, so every existing importer of
`agents.single_agent` — `main.py`, three test modules, `agents/__init__.py` — keeps working
against the names it already used. Nothing about what this loop *does* changed with the move.

**A later hop's question is never scripted, and since T5 it is never re-litigated either.**
`judge_sufficiency` reads the excerpts and answers with `AppraisalVerdict.requested_followup`,
drawn from what the sources actually said; the loop carries that string straight into the next
`ResearchAssignment` and skips the planner entirely, so no model call stands between the
judgement and the hop it asked for. It never writes a question itself, and a run whose
appraiser has nothing specific to ask stops rather than inventing one.

**The stop/continue decision is the Appraiser's, and it has one definition.**
`supervisor._stop_after_hop` and `supervisor._outstanding_followup` are imported rather than
restated below: this loop plays all three roles, but it must not answer a verdict differently
from the way the Supervisor answers it, and two copies of that rule is exactly how that would
happen.

**A refusal is control flow, not an error.** `RunBudget.claim` answers `False` and knows
nothing about prompts, tools or error envelopes; deciding what that means is the loop's job,
and it means the same thing everywhere: stop, and finalise honestly with what was already
gathered. `None` from a stage function means the same — that stage could not answer, so the
run stops rather than pretending.

**Nothing here is allowed to loop without a bound.** Hops are bounded by `MAX_HOPS` through
`RunState.hop`, turns within a hop by `researcher._MAX_RESEARCH_TURNS`, re-asks by
`runtime._MAX_DECODE_ATTEMPTS`, the report's retry ladder by `MAX_OUTPUT_RETRIES`, and every
model call and tool call by the ledger.

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

from evergrove_agent.agents.appraiser import judge_sufficiency
from evergrove_agent.agents.researcher import run_research_step
from evergrove_agent.agents.runtime import (
    AgentProviders,
    PreparationFailed,
    StopReason,
    _assign,
    _mirror_session_memory,
    _recall_previous_preparation,
    _remember_preparation,
)
from evergrove_agent.agents.supervisor import (
    _can_afford_a_hop,
    _followup_decision,
    _outstanding_followup,
    _stop_after_hop,
    _stop_before_planning,
    decide_next_step,
    finalise,
)
from evergrove_agent.config import Settings, get_settings
from evergrove_agent.schemas import (
    AppraisalRequest,
    FocusPreparationReport,
    RunState,
    TaskContext,
)
from evergrove_agent.tools.base import RunBudget, RunContext
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tracing import Tracer, agent_span

__all__ = [
    "AgentProviders",
    "PreparationFailed",
    "StopReason",
    "decide_next_step",
    "finalise",
    "judge_sufficiency",
    "run_agent",
    "run_research_step",
]
"""The loop, plus the names Day 5 T1 moved out and this module re-exports.

Re-exported rather than relocated at every call site because `main.py` and three test
modules import them from here, and a refactor whose whole claim is "behaviour is unchanged"
should not also churn every importer. Listing them here is also what tells ruff these
imports are intentional rather than unused.
"""


async def run_agent(
    task: TaskContext,
    *,
    registry: ToolRegistry,
    providers: AgentProviders | None = None,
    ctx: RunContext | None = None,
    settings: Settings | None = None,
    tracer: Tracer | None = None,
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

    **One agent, so exactly one agent span (Day 5 T6).** The whole run is `agent.run` and
    every tool call nests under it. There are deliberately no `supervisor.decide` /
    `researcher.loop` / `appraiser.judge` spans here: the four stages are the same functions
    the multi path calls, but nothing is *delegated* — one agent performs all of them — and
    emitting role spans would make a single-agent trace indistinguishable from a multi-agent
    one, which is the exact distinction T6 exists to record. The difference between the two
    modes is therefore readable straight off the tree: one agent span, or a Supervisor with
    workers underneath it.
    """
    settings = settings or get_settings()
    providers = providers or AgentProviders.from_settings(settings)
    ctx = ctx or RunContext(budget=RunBudget.from_settings(settings))
    budget = ctx.budget
    stop: StopReason

    with agent_span(tracer, ctx, "agent.run", input_summary=task.task_title) as run_span:
        state = RunState(
            task=task,
            previous=await _recall_previous_preparation(registry, ctx, task, settings),
        )

        while True:
            # Deliberately before `decide_next_step`: `plan.md` is not written for a "you may
            # not research" case, and skipping it also saves a model call for the report.
            # Shared with `run_supervised` rather than restated, so the two loops cannot drift
            # on what stops a run — the bound applies to a hop the Appraiser asked for just as
            # it does to one the planner chose.
            blocked = _stop_before_planning(state, budget)
            if blocked is not None:
                stop = blocked
                break

            followup = _outstanding_followup(state.verdict)
            if followup is not None:
                # The Appraiser asked for this hop, so it is not re-decided here. The planner
                # is not consulted at all — there is no model call in which a `FINALISE` could
                # be produced over the top of the verdict (T5).
                if not _can_afford_a_hop(budget):
                    stop = "budget_spent"
                    break
                decision = _followup_decision(followup)
            else:
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
                attachment_path=(
                    str(task.attachment_path) if task.attachment_path else None
                ),
            )
            # Reassigned, never appended: `RunState` validates on assignment, and an in-place
            # append would slip straight past the `max_length=3` bound the hop cap rests on.
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

            # The hop is mirrored whether or not it was judged: a hop that gathered evidence
            # and then lost its appraiser is exactly the run someone will want the history of.
            await _mirror_session_memory(
                registry, ctx, decision=decision, findings=findings, verdict=verdict
            )

            # The stop/continue decision itself, shared with `run_supervised`: one definition
            # of what a verdict means, so the two loops cannot answer one verdict differently.
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
        # The one place a validated report exists: `finalise` returns one or raises, so this
        # line is what makes "an invalid preparation is never remembered" structural rather
        # than a rule somebody has to follow.
        await _remember_preparation(registry, ctx, report)
        run_span.summarise(f"{state.hop} hop(s), stopped on {stop}")
        return report
