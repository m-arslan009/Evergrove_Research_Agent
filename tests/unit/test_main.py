"""The CLI: the no-research round trip, and the routing into the research loop.

The research tests stand in for `service.prepare_focus_session` rather than driving the
loop through it — `test_service.py` proves the service reaches `run_agent`, and what this
file is about is which path a set of flags lands on and what the process does with the
result. Substituting the flow is what makes "did research mode run at all" observable, and
that question is exactly the bug this subtask fixes.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from evergrove_agent import main
from evergrove_agent.agents import PreparationFailed
from evergrove_agent.config import Settings
from evergrove_agent.llm import FakeProvider
from evergrove_agent.main import (
    NO_RESEARCH_ASSUMPTION,
    build_parser,
    max_topics_for,
    prepare_without_research,
)
from evergrove_agent.schemas import FocusPreparationReport, TaskContext
from evergrove_agent.tools.base import RunBudget, RunContext


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(5, 3), (15, 3), (25, 5), (45, 8), (180, 8)],
)
def test_session_sizing_rule(minutes: int, expected: int) -> None:
    """`max(3, minutes // 5)`, capped by the schema's own limit of 8 (plan 17)."""
    assert max_topics_for(minutes) == expected


async def test_round_trip_produces_a_validated_report(
    settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    provider = FakeProvider([json.dumps(valid_report_payload)])
    task = TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25)

    report = await prepare_without_research(task, provider, settings)

    assert report.interpreted_goal == valid_report_payload["interpreted_goal"]
    assert provider.calls[0].schema_name == "FocusPreparationReport"
    assert provider.calls[0].temperature == 0.0


async def test_the_prompt_carries_the_task_and_the_topic_cap(
    settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    provider = FakeProvider([json.dumps(valid_report_payload)])
    task = TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25)

    await prepare_without_research(task, provider, settings)

    prompt = provider.calls[0].messages[0].content
    assert "Learn PostgreSQL indexing" in prompt
    assert "between 2 and 5 items" in prompt


async def test_no_research_cannot_smuggle_in_sources(
    settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    """The scripted reply cites postgresql.org. Nothing was fetched, so it must not survive."""
    provider = FakeProvider([json.dumps(valid_report_payload)])
    task = TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25)

    report = await prepare_without_research(task, provider, settings)

    assert report.resources == []
    assert report.sources_examined == 0
    assert report.hops_used == 0
    assert NO_RESEARCH_ASSUMPTION in report.assumptions
    assert report.unknowns  # honesty field must not be empty when nothing was read


async def test_bookkeeping_fields_are_ours_not_the_models(
    settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    provider = FakeProvider([json.dumps(valid_report_payload)])
    task = TaskContext(task_title="Index tuning in practice", session_minutes=50)

    report = await prepare_without_research(task, provider, settings)

    assert report.run_id != valid_report_payload["run_id"]
    assert report.run_id.startswith("run_")
    assert report.model_used == "fake-model"
    assert report.original_task == "Index tuning in practice"
    assert report.session_duration_minutes == 50  # the model must not rescope


class TestCli:
    def test_no_research_has_a_no_search_alias(self) -> None:
        """Plan 21 calls it --no-research; plan 30 calls it --no-search. Both work."""
        parser = build_parser()

        assert parser.parse_args(["--task", "x", "--no-research"]).no_research
        assert parser.parse_args(["--task", "x", "--no-search"]).no_research

    def test_task_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--minutes", "25"])

    def test_defaults_to_twenty_five_minutes(self) -> None:
        assert build_parser().parse_args(["--task", "x"]).minutes == 25


# --- routing into the research loop ---------------------------------------------------------


class ResearchFlow:
    """A stand-in for `service.prepare_focus_session` that records what it was handed."""

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.tasks: list[TaskContext] = []

    async def __call__(self, task: TaskContext, **kwargs: Any) -> Any:
        self.tasks.append(task)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def research(
    monkeypatch: pytest.MonkeyPatch, valid_report_payload: dict[str, Any]
) -> Any:
    """Hermetic settings plus a substituted research flow, returning it for inspection.

    `main.get_settings` is replaced rather than left to the real `lru_cache`d one because
    `run()` mutates the object `--provider` targets; a cached, `.env`-derived instance would
    both leak that mutation into later tests and make the outcome depend on the developer's
    own `.env`.
    """
    monkeypatch.setattr(main, "get_settings", lambda: Settings(_env_file=None))

    def _install(outcome: Any = None) -> ResearchFlow:
        if outcome is None:
            outcome = FocusPreparationReport.model_validate(valid_report_payload)
        flow = ResearchFlow(outcome)
        monkeypatch.setattr(main, "prepare_focus_session", flow)
        return flow

    return _install


async def run_cli(*argv: str) -> int:
    return await main.run(build_parser().parse_args(list(argv)))


class TestResearchMode:
    async def test_research_mode_runs_the_research_flow(
        self, research: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The reported bug: research mode answered with "not built yet — it is the Day 3
        loop" and exit 2 while the loop had been finished for four subtasks."""
        flow = research()

        code = await run_cli("--task", "Learn PostgreSQL indexing", "--indent", "0")
        captured = capsys.readouterr()

        assert code == 0
        assert flow.tasks, "research mode never reached the research flow"
        assert json.loads(captured.out)["original_task"] == "Learn PostgreSQL indexing"
        assert "not built yet" not in captured.err

    async def test_the_attachment_reaches_the_research_flow(
        self, research: Any, settings: Settings
    ) -> None:
        """`--attachment` used to be refused outright. It has to arrive on `TaskContext`,
        because that is the single fact that both advertises `read_document` to the
        researcher and fills the `{attachment}` block in its prompt."""
        flow = research()
        # A relative path is resolved inside ALLOWED_ATTACHMENT_DIR, not the working
        # directory — the same rule an MCP client with no useful cwd will rely on.
        given = "documents/indexing-brief.md"

        code = await run_cli("--task", "Summarise the brief", "--attachment", given)

        assert code == 0
        assert flow.tasks[0].attachment_path is not None
        assert flow.tasks[0].attachment_path.as_posix() == given
        assert (settings.allowed_attachment_dir / given).is_file()

    async def test_a_missing_attachment_is_refused_before_the_run_starts(
        self, research: Any
    ) -> None:
        """Without the pre-flight this surfaces as a NOT_FOUND tool failure several model
        calls in, so a typo costs a whole run on a machine where a run takes minutes."""
        flow = research()

        code = await run_cli("--task", "x", "--attachment", "no-such-file.md")

        assert code == 2
        assert flow.tasks == [], "the run started despite an unreadable attachment"

    async def test_attachment_and_no_research_are_refused_together(
        self, research: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Reading an attachment is a tool call and the no-research path makes none, so the
        combination has to be refused rather than silently ignoring one of the two flags."""
        research()

        code = await run_cli("--task", "x", "--no-research", "--attachment", "brief.md")

        assert code == 2
        assert capsys.readouterr().out == ""

    async def test_a_failed_run_is_not_downgraded_to_a_sourceless_one(
        self, research: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The two modes make different promises about what a report rests on. Falling back
        to `--no-research` on failure would answer a request for sources with a plan that
        has none, under flags that asked for them."""
        research(PreparationFailed("no valid report after 3 attempts", run_id="run_x"))

        code = await run_cli("--task", "x")
        captured = capsys.readouterr()

        assert code == 1
        assert captured.out == "", "a failed research run must not print a report"
        assert "no valid report after 3 attempts" in captured.err

    async def test_fully_local_still_refuses_a_hosted_role(self, research: Any) -> None:
        """The $0-local guarantee is checkable only if it refuses. `--provider hosted` also
        proves the override still reaches `Settings` on the research path."""
        flow = research()

        code = await run_cli("--task", "x", "--provider", "hosted", "--fully-local")

        assert code == 2
        assert flow.tasks == []


class TestProgress:
    async def test_nothing_is_written_when_the_stream_is_not_a_terminal(
        self, settings: Settings
    ) -> None:
        """A redirected or piped run must be byte-identical to a silent one — a status line
        redrawn every 0.4 s into a log file is noise, and into a JSON pipe it is corruption
        the moment anyone points it at stdout."""
        stream = io.StringIO()
        ctx = RunContext(budget=RunBudget.from_settings(settings))

        async with main.progress(ctx, enabled=True, stream=stream):
            pass

        assert stream.getvalue() == ""

    def test_the_status_line_reports_the_live_ledger(self, settings: Settings) -> None:
        """The line reads the run's own counters rather than an estimate of its own, which
        is what lets it exist without a callback from `run_agent`."""
        ctx = RunContext(budget=RunBudget.from_settings(settings))
        ctx.budget.claim("model_call")
        ctx.budget.claim("search")

        line = main._status_line(ctx, "|")

        assert f"model calls 1/{settings.max_model_calls}" in line
        assert f"searches 1/{settings.max_search_calls}" in line
        assert f"page reads 0/{settings.max_fetch_calls}" in line
