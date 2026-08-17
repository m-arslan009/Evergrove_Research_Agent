"""`service.py` composes a run and hands it to the loop (Day 3 S11).

Deliberately narrow. What the loop *decides* is proven in `test_single_agent.py` and the
composition end to end is S13's integration suite; these three tests exist for the ways a
thin composition layer can be wrong without either of those noticing — it can build the
right collaborators and never call the loop, it can ignore the ones it was given, and it
can resolve its own settings behind a caller's back.

Offline and model-free: `FakeProvider` is injected, and the registry is the real wired one
with `SEARCH_BACKEND=fixture`, because no test here reaches a tool.

Since Day 4 T2 this layer also owns the run's database handle and writes the run's trace
header, so every test here overrides `settings` onto a temporary `DB_PATH` — a suite that
composes a real run must not write to the developer's own database.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from evergrove_agent.agents import AgentProviders
from evergrove_agent.agents.single_agent import PreparationFailed
from evergrove_agent.config import Settings
from evergrove_agent.llm import FakeProvider
from evergrove_agent.memory import db
from evergrove_agent.schemas import FocusPreparationReport, TaskContext
from evergrove_agent.service import prepare_focus_session
from evergrove_agent.tools.base import RunBudget, RunContext
from evergrove_agent.tools.wiring import build_tool_registry
from evergrove_agent.tracing import get_run

TASK = TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Defaults, but pointed at a temporary database — overrides `conftest.settings`."""
    return Settings(_env_file=None, db_path=tmp_path / "agent.sqlite3")


class FakeClock:
    """The injected clock from `test_run_budget.py`, so an expired deadline costs no wait."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


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
    tmp_path: Path, report_json: Callable[..., str]
) -> None:
    """`--fully-local` and `--provider` reach the run only as a mutated `Settings`. A
    collaborator defaulted from `get_settings()` instead of the argument would ignore both,
    and on the `--fully-local` path that turns a refusal into a hosted call.

    Observed through the budget: one model call leaves no room for the planner's reserve, so
    the run skips straight to the report. Under the default 10 the planner would go first and
    consume the scripted report as a decision.
    """
    settings = Settings(
        _env_file=None, db_path=tmp_path / "agent.sqlite3", max_model_calls=1
    )
    provider = FakeProvider([report_json()])

    report = await prepare_focus_session(
        TASK,
        settings=settings,
        registry=build_tool_registry(settings),
        providers=AgentProviders(provider, provider, provider),
    )

    assert report.hops_used == 0
    assert len(provider.calls) == 1, "the report only; the planner was never affordable"


# --- the run's trace header (Day 4 T2) --------------------------------------------------------


async def test_a_completed_run_writes_its_header_and_closes_it(
    settings: Settings, report_json: Callable[..., str]
) -> None:
    """Someone reading a trace has to be able to tell a finished run from a killed one.

    The header is written before the loop and updated after it, so the two halves are the
    regression: a run that only appears once it has succeeded is missing at exactly the
    moment a trace is wanted, and one that is never closed reads as still running forever.
    The counters are asserted because they are the snapshot the live ledger leaves behind.
    """
    provider = FakeProvider([finalise_now(), report_json()])
    ctx = RunContext(budget=RunBudget.from_settings(settings))

    report = await prepare_focus_session(
        TASK,
        settings=settings,
        providers=AgentProviders(provider, provider, provider),
        ctx=ctx,
    )

    with db.open_database(settings.db_path) as connection:
        run = get_run(connection, ctx.run_id)

    assert run is not None
    assert run.status == "ok"
    assert run.task_title == TASK.task_title
    assert run.ended_at is not None
    assert (run.hops_used, run.model_calls) == (report.hops_used, 2)


async def test_a_run_that_ran_out_of_allowance_says_so_rather_than_just_failing(
    tmp_path: Path, report_json: Callable[..., str]
) -> None:
    """`failed` and `budget_exhausted` are different diagnoses and only the trace keeps them
    apart — `PreparationFailed` carries the attempts made, never the reason there were none.
    Mistaking an exhausted run for a broken one is what sends someone debugging a model that
    was never called.
    """
    settings = Settings(_env_file=None, db_path=tmp_path / "agent.sqlite3")
    provider = FakeProvider([report_json()])
    clock = FakeClock()
    ctx = RunContext(budget=RunBudget.from_settings(settings, clock=clock))
    clock.now = float(settings.total_run_timeout_s) + 1.0

    with pytest.raises(PreparationFailed):
        await prepare_focus_session(
            TASK,
            settings=settings,
            providers=AgentProviders(provider, provider, provider),
            ctx=ctx,
        )

    with db.open_database(settings.db_path) as connection:
        run = get_run(connection, ctx.run_id)

    assert run is not None
    assert run.status == "budget_exhausted"
    assert run.ended_at is not None
