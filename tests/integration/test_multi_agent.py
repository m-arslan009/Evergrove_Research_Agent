"""Day 5 T1 acceptance: the Supervisor/Researcher/Appraiser split, and the two modes.

The split moved code between modules without changing what a run does. That is a claim the
existing suites cannot check on their own — they all drive the single-agent path, so they
prove the *stages* still work while saying nothing about whether the new topology reaches
them or whether the old one still exists. Four things live only here:

1. **Both modes still produce a valid report from the same inputs.** The single-agent path is
   a deliverable in its own right and is now the one nobody exercises by default; a test that
   runs both from one script is what stops it rotting silently between here and the demo.
2. **The multi path's tools still go through the registry.** A worker that reached a tool
   directly would keep working and quietly lose hooks, spans, budget claims and the trace log
   — a regression with no symptom until someone reads a trace.
3. **The roles stay separate.** The Researcher must never write a report and the Appraiser
   must never be handed a tool. With one provider per role, what each model was actually asked
   for is readable, and role bleed becomes a failing assertion instead of a design intention.
4. **The workers cannot talk to each other.** Enforced structurally, by reading the imports —
   the one form of "they never communicate directly" that survives a future edit.

Offline and model-free throughout, on the same terms as `test_single_loop.py`: `FakeProvider`
injected rather than patched, the **committed** `fixtures/search/` tree at the shipped
default, `respx` active so any unrouted request fails the test, and only `DB_PATH` moved into
`tmp_path`.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from evergrove_agent.agents import AgentProviders
from evergrove_agent.agents import supervisor as supervisor_module
from evergrove_agent.config import AgentMode, Settings
from evergrove_agent.llm import FakeProvider
from evergrove_agent.memory import db, get_search_usage
from evergrove_agent.schemas import (
    AppraisalRequest,
    AppraisalVerdict,
    FocusPreparationReport,
    ResearchAction,
    ResearchAssignment,
    ResearchFindings,
    TaskContext,
)
from evergrove_agent.service import prepare_focus_session
from evergrove_agent.tools.base import RunBudget, RunContext
from evergrove_agent.tracing import get_spans

TASK = TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25)

DOCS_QUERY = "postgresql b-tree index"
"""`fixtures/search/postgresql-indexing.json`, `source_type=docs`."""

INDEXES_URL = "https://www.postgresql.org/docs/current/indexes-types.html"
"""That recording's top-ranked hit — the one page these runs open."""

ARTICLE = Path(__file__).resolve().parents[2] / "fixtures" / "html" / "article.html"
"""The committed page served in place of `INDEXES_URL`."""

MODES: tuple[AgentMode, ...] = ("single", "multi")


# --- the workspace ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Settings:
    """Committed fixtures for input, a temporary file for state — as `test_single_loop.py`.

    Only `DB_PATH` moves. `SEARCH_BACKEND` and `SEARCH_FIXTURE_DIR` stay exactly as a fresh
    clone has them, because "both modes run offline on the shipped defaults" is part of what
    is being accepted here.
    """
    return Settings(_env_file=None, db_path=tmp_path / "agent.sqlite3")


@pytest.fixture
def ledger(workspace: Settings) -> Iterator[sqlite3.Connection]:
    """A second handle on the run's database, for reading spans and the search counter back."""
    with db.open_database(workspace.db_path) as connection:
        yield connection


def serve_indexes_page() -> respx.Route:
    """`INDEXES_URL` answered locally, so a run can genuinely read something."""
    return respx.get(INDEXES_URL).mock(
        return_value=httpx.Response(200, html=ARTICLE.read_text(encoding="utf-8"))
    )


# --- scripting one model per role ---------------------------------------------------------------
#
# Three providers rather than the one `test_single_loop.py` shares, because the whole subject
# here is *which role was asked what*. A single provider answering all three makes that
# unreadable: every call lands in one list and role bleed looks exactly like normal traffic.


def plan(action: str, question: str | None = None, preference: str = "docs") -> str:
    return json.dumps(
        {
            "action": action,
            "research_question": question,
            "source_preference": preference,
            "reasoning": "because",
        }
    )


def verdict(sufficient: bool, followup: str | None = None) -> str:
    return json.dumps(
        {
            "sufficient": sufficient,
            "missing_information": [],
            "requested_followup": followup,
            "reasoning": "because",
        }
    )


def tool_turn(name: str, arguments: dict[str, Any]) -> str:
    """One research turn, as S14's constrained `ResearchAction`."""
    return ResearchAction(
        tool=name, arguments=arguments, reasoning="scripted turn"
    ).model_dump_json()


def done() -> str:
    """A research turn that asks for nothing — the model saying the hop has enough.

    Preferred over an unparseable reply for ending the hop: both break the turn loop, but this
    one ends it the way the contract describes rather than by tripping a validation error.
    """
    return ResearchAction(tool="", reasoning="enough gathered").model_dump_json()


@pytest.fixture
def report(valid_report_payload: dict[str, Any]):
    """A report a model could plausibly return, citing nothing unless a test says otherwise.

    The shared payload cites a real page these runs do open, but grounding is not this file's
    subject and an unopened citation would fail a run for a reason none of these tests is
    about. Citations are opted into.
    """

    def _report(**overrides: Any) -> str:
        return json.dumps({**valid_report_payload, "resources": [], **overrides})

    return _report


class Roles:
    """One `FakeProvider` per reasoning role, plus what each was asked for.

    `RecordedCall` already carries `schema_name`, so nothing here inspects a prompt: the schema
    a role was asked to fill *is* its job.

    `RecordedCall.tool_names` is deliberately **not** used. Since the S14 contingency swap the
    researcher's menu travels inside the rendered prompt rather than as `tools=`, so that field
    is empty for every role — an assertion on it would pass for all three and prove nothing.
    What a role may reach is asserted statically instead, in section 4.
    """

    def __init__(self, *, supervisor: list[Any], researcher: list[Any], appraiser: list[Any]):
        self.supervisor = FakeProvider(supervisor)
        self.researcher = FakeProvider(researcher)
        self.appraiser = FakeProvider(appraiser)

    @property
    def providers(self) -> AgentProviders:
        return AgentProviders(self.supervisor, self.researcher, self.appraiser)

    def schemas(self, provider: FakeProvider) -> list[str | None]:
        return [call.schema_name for call in provider.calls]


def one_hop(report_json: str) -> Roles:
    """A run that searches, opens `INDEXES_URL`, is judged sufficient, then reports.

    The same shape for both modes, which is the point: identical inputs, and any difference in
    the outcome is a difference in topology rather than in what the run was told to do.
    """
    return Roles(
        supervisor=[plan("RESEARCH", "what does a B-tree index do"), report_json],
        researcher=[
            tool_turn("web_search", {"query": DOCS_QUERY, "source_type": "docs"}),
            tool_turn("fetch_url", {"url": INDEXES_URL}),
            done(),
        ],
        appraiser=[verdict(True)],
    )


async def drive(
    roles: Roles, settings: Settings, mode: AgentMode
) -> tuple[FocusPreparationReport, RunContext]:
    """One run through the real entry point, in one mode.

    `registry` is deliberately not passed: letting `prepare_focus_session` build it is the
    difference between testing a loop and testing the composition — and it is also what puts
    the tracer and the hooks on the registry the run actually uses.
    """
    ctx = RunContext(budget=RunBudget.from_settings(settings))
    result = await prepare_focus_session(
        TASK, mode=mode, settings=settings, providers=roles.providers, ctx=ctx
    )
    return result, ctx


# --- 1. both modes still work ---------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
@respx.mock
async def test_both_modes_produce_a_valid_report_from_the_same_inputs(
    mode: AgentMode, workspace: Settings, ledger: sqlite3.Connection, report
) -> None:
    """The drift guard the plan asks for by name, and the reason it is parameterized.

    Two failures, one test. The multi path could be wired wrong and never reach a worker at
    all — a supervisor that finalises immediately still returns a report, so only the hop count
    and the sources catch it. And the single path, now that nothing else runs it by default,
    could be broken by any Day 5-6 edit and stay broken until the demo. Asserting the same
    outcome from the same script is what makes those two the same failing test rather than a
    discovery on the day.

    The search counter is read because an "offline" run that quietly reached a metered backend
    would otherwise look exactly like a pass.
    """
    page = serve_indexes_page()
    roles = one_hop(report(resources=[]))

    result, _ = await drive(roles, workspace, mode)

    assert isinstance(result, FocusPreparationReport)
    assert result.hops_used == 1, "the worker ran; the supervisor did not finalise immediately"
    assert result.sources_examined == 1, "the page it opened, not the leads it merely found"
    assert result.original_task == "Learn PostgreSQL indexing"  # bookkeeping, not the model
    assert page.call_count == 1
    assert get_search_usage(ledger) == 0, "the offline default must not move the live counter"


# --- 2. the multi path still goes through the registry ----------------------------------------------


@respx.mock
async def test_the_multi_path_reaches_its_tools_through_the_registry(
    workspace: Settings, ledger: sqlite3.Connection, report
) -> None:
    """A worker that called a tool directly would still work — and lose Day 4 entirely.

    That is the regression with no symptom: the report comes back, the run looks healthy, and
    the hooks, the budget claims, the JSON log line and the trace are simply gone. Spans are
    the readable end of all four, because only `ToolRegistry.call` produces them: a span per
    tool means the call went through the registry, and the budget counters moving means the
    pre-hook that charges for it ran too.
    """
    serve_indexes_page()
    roles = one_hop(report(resources=[]))

    _, ctx = await drive(roles, workspace, "multi")

    spans = get_spans(ledger, ctx.run_id)
    tool_spans = [span.name for span in spans if span.kind == "tool"]

    assert "web_search" in tool_spans and "fetch_url" in tool_spans
    assert sorted(tool_spans) == [
        "fetch_url",
        "recall_previous_preparation",
        "record_run_memory",
        "save_preparation",
        "web_search",
    ], "every tool the run touched, memory included, went through the registry"
    assert all(span.ended_at is not None for span in spans), "every span was closed"
    assert (ctx.budget.searches_used, ctx.budget.fetches_used) == (1, 1), (
        "the registry's budget pre-hook charged both calls"
    )


# --- 3. the roles stay in their lanes --------------------------------------------------------------


@respx.mock
async def test_the_researcher_never_reports_and_the_appraiser_never_gets_a_tool(
    workspace: Settings, report
) -> None:
    """Role bleed, asserted from what each model was actually asked for.

    The split is only worth anything if it holds under a model that would happily do the wrong
    job when offered it, so each role's calls are checked against the one schema its job is
    defined by. The Researcher is never asked for a `FocusPreparationReport` — writing the
    deliverable is the Supervisor's, and a gatherer that could also write it would be citing
    its own work. The Appraiser is never asked for a `ResearchAction`, which is the only schema
    that can name a tool.

    That the Appraiser could not reach a tool *even if it emitted one* is the stronger claim,
    and it is structural rather than observable in one run: section 4 asserts the module
    imports neither the dispatcher nor the registry.
    """
    serve_indexes_page()
    roles = one_hop(report(resources=[]))

    await drive(roles, workspace, "multi")

    assert roles.schemas(roles.supervisor) == [
        "SupervisorDecision",
        "FocusPreparationReport",
    ], "the supervisor decides and writes the report, and does nothing else"
    assert set(roles.schemas(roles.researcher)) == {"ResearchAction"}, (
        "the researcher only ever acts; it never decides the run is over and never reports"
    )
    assert set(roles.schemas(roles.appraiser)) == {"AppraisalVerdict"}, (
        "the appraiser only ever judges; ResearchAction is the only schema naming a tool"
    )


# --- 4. the workers cannot reach each other ---------------------------------------------------------

AGENTS = Path(__file__).resolve().parents[2] / "src" / "evergrove_agent" / "agents"


def imported_modules(module: str) -> set[str]:
    """Every module `module` imports, read from its source rather than from `sys.modules`.

    Static on purpose: importing the module and inspecting its namespace would answer a
    different question — what happens to be loaded — and would pass for a worker that reached
    its sibling through a function-local import.
    """
    tree = ast.parse((AGENTS / f"{module}.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize(
    ("worker", "forbidden"),
    [
        ("researcher", "evergrove_agent.agents.appraiser"),
        ("researcher", "evergrove_agent.agents.supervisor"),
        ("appraiser", "evergrove_agent.agents.researcher"),
        ("appraiser", "evergrove_agent.agents.supervisor"),
        # The Appraiser performs no research, which is a statement about what it can *reach*,
        # not only about what it happened to emit in one scripted run. Without the dispatcher
        # or the registry there is no path from that module to a tool at all.
        ("appraiser", "evergrove_agent.agents.tool_calling"),
        ("appraiser", "evergrove_agent.tools.registry"),
        # Not a worker rule but the same guard: reaching a sibling through the package makes
        # `agents/__init__.py` re-enter itself, so every module here imports by module path.
        ("researcher", "evergrove_agent.agents"),
        ("appraiser", "evergrove_agent.agents"),
        ("supervisor", "evergrove_agent.agents"),
        ("runtime", "evergrove_agent.agents"),
    ],
)
def test_a_worker_cannot_import_its_way_around_the_supervisor(
    worker: str, forbidden: str
) -> None:
    """Coordination stays with the Supervisor, enforced where a future edit would break it.

    "The workers never communicate directly" is otherwise a sentence in a docstring that the
    first convenient import quietly repeals — and the failure it leads to is subtle: a
    Researcher that could read a verdict would start deciding when to stop, which is the one
    responsibility the split exists to take away from it. The same check covers the import
    cycle, since a sibling reached through the package is how this package would re-enter
    itself.
    """
    assert forbidden not in imported_modules(worker)


# --- 5. every hand-off is a typed message, routed by the Supervisor (Day 5 T2) ---------------


@respx.mock
async def test_each_hand_off_is_the_typed_message_its_role_is_defined_by(
    workspace: Settings, report, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The messages are not merely defined — they are what actually crosses each boundary.

    Section 3 reads what each *model* was asked for, which is a claim about prompting. This
    is the claim about the code between the prompts: the Supervisor converts its own
    `SupervisorDecision` into a `ResearchAssignment`, receives a `ResearchFindings`, builds
    an `AppraisalRequest` from it, and receives an `AppraisalVerdict` — four typed values,
    no dictionaries, no loose JSON, no role-specific ad hoc payload.

    The chain of `research_question` assertions is what makes it a *chain* rather than four
    isolated type checks: one question, chosen by the planner, travelling supervisor →
    researcher → supervisor → appraiser. The sources assertion is the architectural rule
    stated as a value — the Appraiser judges exactly what the Researcher gathered, yet
    receives it from the Supervisor's own frame, because `_delegate_hop` is the only path
    between the two workers. Section 4 proves no other path can be built; this proves the
    one that exists is the one being used.

    The real stage functions still run — the spies record and delegate — so this is the
    behaviour of an actual run rather than of a stubbed one.
    """
    serve_indexes_page()
    roles = one_hop(report(resources=[]))
    seen: dict[str, Any] = {}

    real_research = supervisor_module.run_research_step
    real_judge = supervisor_module.judge_sufficiency

    async def spy_research(assignment: Any, **kwargs: Any) -> Any:
        seen["assignment"] = assignment
        seen["findings"] = await real_research(assignment, **kwargs)
        return seen["findings"]

    async def spy_judge(request: Any, **kwargs: Any) -> Any:
        seen["request"] = request
        seen["verdict"] = await real_judge(request, **kwargs)
        return seen["verdict"]

    monkeypatch.setattr(supervisor_module, "run_research_step", spy_research)
    monkeypatch.setattr(supervisor_module, "judge_sufficiency", spy_judge)

    await drive(roles, workspace, "multi")

    assert isinstance(seen["assignment"], ResearchAssignment)
    assert isinstance(seen["findings"], ResearchFindings)
    assert isinstance(seen["request"], AppraisalRequest)
    assert isinstance(seen["verdict"], AppraisalVerdict)

    question = "what does a B-tree index do"
    assert seen["assignment"].research_question == question, "the planner's question"
    assert seen["findings"].research_question == question, "the Researcher answered it"
    assert seen["request"].research_question == question, "the Appraiser judged it"

    assert [source.url for source in seen["request"].sources] == [
        source.url for source in seen["findings"].sources
    ], "the Appraiser judged what the Researcher gathered, handed over by the Supervisor"

    # The assignment is the decision *plus* what the run can still afford — the half that
    # makes the message self-contained, and the reason the Researcher needs nothing else.
    assert seen["assignment"].hop == 1
    assert seen["assignment"].source_preference == "docs"
    assert seen["assignment"].max_searches > 0
