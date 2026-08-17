"""`service.py` composes a run and hands it to the loop (Day 3 S11).

Deliberately narrow. What the loop *decides* is proven in `test_single_agent.py` and the
composition end to end is S13's integration suite; these three tests exist for the ways a
thin composition layer can be wrong without either of those noticing — it can build the
right collaborators and never call the loop, it can ignore the ones it was given, and it
can resolve its own settings behind a caller's back.

Offline and model-free: `FakeProvider` is injected, and the registry is the real wired one
with `SEARCH_BACKEND=fixture`, because no test here reaches a tool.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from evergrove_agent.agents import AgentProviders
from evergrove_agent.config import Settings
from evergrove_agent.llm import FakeProvider
from evergrove_agent.schemas import FocusPreparationReport, TaskContext
from evergrove_agent.service import prepare_focus_session
from evergrove_agent.tools.base import RunBudget, RunContext
from evergrove_agent.tools.wiring import build_tool_registry

TASK = TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25)


@pytest.fixture
def report_json(valid_report_payload: dict[str, Any]) -> Callable[..., str]:
    """A report grounding accepts: it cites nothing, and these runs gather nothing.

    Same reasoning as `test_single_agent.scripted_report` — the shared payload's real
    PostgreSQL citation is correct for the grounding suite and wrong here, where it would
    fail every run for something none of these tests is about.
    """

    def _report(**overrides: Any) -> str:
        return json.dumps({**valid_report_payload, "resources": [], **overrides})

    return _report


def finalise_now() -> str:
    """A planner turn that writes the report immediately: no hop, no tool, no search."""
    return json.dumps(
        {
            "action": "FINALISE",
            "research_question": None,
            "source_preference": "docs",
            "reasoning": "because",
        }
    )


async def test_it_runs_the_loop_and_returns_a_validated_report(
    settings: Settings, report_json: Callable[..., str]
) -> None:
    """The regression this whole subtask exists for: a service that assembles a registry,
    providers and a budget, and then never reaches `run_agent` — which is indistinguishable
    from the CLI's old refusal from the outside."""
    provider = FakeProvider([finalise_now(), report_json()])

    report = await prepare_focus_session(
        TASK,
        settings=settings,
        registry=build_tool_registry(settings),
        providers=AgentProviders(provider, provider, provider),
        ctx=RunContext(budget=RunBudget.from_settings(settings)),
    )

    assert isinstance(report, FocusPreparationReport)
    assert report.original_task == "Learn PostgreSQL indexing"  # bookkeeping, not the model
    assert len(provider.calls) == 2, "the planner and the report, in that order"


async def test_the_caller_s_run_context_is_the_one_the_run_spends(
    settings: Settings, report_json: Callable[..., str]
) -> None:
    """The CLI's progress line reads this exact object while the run holds it. A service
    that quietly built its own `RunContext` would leave that line reading 0/10 for the
    length of a run and nobody would see a failing test."""
    provider = FakeProvider([finalise_now(), report_json()])
    ctx = RunContext(budget=RunBudget.from_settings(settings))

    await prepare_focus_session(
        TASK,
        settings=settings,
        registry=build_tool_registry(settings),
        providers=AgentProviders(provider, provider, provider),
        ctx=ctx,
    )

    assert ctx.budget.model_calls_used == 2


async def test_defaults_are_built_from_the_settings_it_was_given(
    report_json: Callable[..., str]
) -> None:
    """`--fully-local` and `--provider` reach the run only as a mutated `Settings`. A
    collaborator defaulted from `get_settings()` instead of the argument would ignore both,
    and on the `--fully-local` path that turns a refusal into a hosted call.

    Observed through the budget: one model call leaves no room for the planner's reserve, so
    the run skips straight to the report. Under the default 10 the planner would go first and
    consume the scripted report as a decision.
    """
    settings = Settings(_env_file=None, max_model_calls=1)
    provider = FakeProvider([report_json()])

    report = await prepare_focus_session(
        TASK,
        settings=settings,
        registry=build_tool_registry(settings),
        providers=AgentProviders(provider, provider, provider),
    )

    assert report.hops_used == 0
    assert len(provider.calls) == 1, "the report only; the planner was never affordable"
