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
from evergrove_agent.agents.prompt_context import render_stop_reason
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
from evergrove_agent.tracing import get_run, get_spans, render_trace

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


def verdict(
    sufficient: bool, followup: str | None = None, **judgement: list[Any]
) -> str:
    """A scripted `AppraisalVerdict`.

    T4's `accepted` / `rejected` arrive through `**judgement` and are omitted when unset, so
    every existing call site keeps scripting the Day 3 payload — which is what keeps those
    runs proving that a verdict without the semantic judgement is still a valid verdict.
    """
    return json.dumps(
        {
            "sufficient": sufficient,
            "missing_information": [],
            "requested_followup": followup,
            "reasoning": "because",
            **judgement,
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


# --- 5b. the Appraiser's judgement reaches the report (Day 5 T4) -----------------------------

LEAD_URL = "https://use-the-index-luke.com/sql/anatomy/the-tree"
"""A third hit in the same recording — discovered by the search, never opened. The
low-authority source T4's acceptance criterion is written about."""


@respx.mock
async def test_a_source_the_appraiser_rejected_does_not_reach_the_reports_resources(
    workspace: Settings, report
) -> None:
    """T4's acceptance criterion, and the wiring underneath it.

    The Appraiser is the only stage that reads the evidence. If a source it judged not worth
    trusting is then handed to the user as a trusted resource, the judgement changed nothing
    and the role is decoration — so this is the one end-to-end claim the task is really about.

    **Two assertions, and they are not equally strong.** The first is about the code: the
    rejection, its reason and the instruction not to cite it must actually reach the prompt
    the Supervisor sends to write the report. That is the wiring, and it fails the moment
    `render_research_context` stops carrying the verdict — which is a silent regression,
    because the run still produces a perfectly valid report.

    The second is the outcome, and it is guidance rather than enforcement by design (see
    `prompt_context._REJECTED_HEADING`): `validate_report` decides what may be cited by set
    membership against what the run actually gathered, and letting one model's reading delete
    a genuinely fetched URL from that set would spend the retry ladder undoing a true
    citation. So the rejected page here is also one nobody opened, which means the *existing*
    deterministic rule still bounds the damage — it could only ever be cited as
    `authority="unknown"`, never as a trusted one.
    """
    serve_indexes_page()
    roles = one_hop(report(resources=[]))
    roles.appraiser = FakeProvider(
        [
            verdict(
                True,
                accepted=[
                    {
                        "source": INDEXES_URL,
                        "supports": "the index types PostgreSQL provides",
                        "does_not_support": "how the planner chooses between them",
                        "authority": "official",
                    }
                ],
                rejected=[
                    {
                        "source": LEAD_URL,
                        "reason": "a tutorial site that was never opened; no version stated",
                    }
                ],
            )
        ]
    )
    roles.supervisor = FakeProvider(
        [
            plan("RESEARCH", "what does a B-tree index do"),
            report(
                resources=[
                    {
                        "title": "PostgreSQL: Index Types",
                        "url": INDEXES_URL,
                        "why_this_source": "Official documentation for the version in use",
                        "authority": "official",
                    }
                ]
            ),
        ]
    )

    result, _ = await drive(roles, workspace, "multi")

    finalise_prompt = roles.supervisor.calls[-1].messages[0].content
    assert LEAD_URL in finalise_prompt, "the run did gather the source that was rejected"
    assert "a tutorial site that was never opened" in finalise_prompt, (
        "the reason travels with the rejection, or the report cannot be honest about it"
    )
    assert "do not cite any of these in resources" in finalise_prompt

    cited = [str(resource.url) for resource in result.resources]
    assert cited == [INDEXES_URL], "the accepted source, and only it"
    assert LEAD_URL not in cited


# --- 6. each role calls the provider its configuration names (Day 5 T3) ----------------------

# Every test above injects `AgentProviders` directly, which is what makes them readable — and
# also what leaves the configuration path itself unproven: three roles pointed at one shared
# provider would pass all of them. So this section builds nothing and injects nothing. It sets
# `*_PROVIDER`, lets `service.py` resolve the run through `AgentProviders.from_settings`, and
# reads which *endpoint* each stage's request actually reached.
#
# That is the difference between storing a setting and honouring one, and it is only visible at
# the wire: `respx` answers both a local Ollama server and Google AI Studio, so a hosted role is
# exercised end to end without a real API call, a real key, or a cent of quota.

_SIGNATURE_PROPERTY: dict[str, str] = {
    "action": "SupervisorDecision",
    "tool": "ResearchAction",
    "sufficient": "AppraisalVerdict",
    "topics_to_cover": "FocusPreparationReport",
}
"""One property that appears in exactly one of the four stage schemas.

Read instead of a schema title because the two providers do not send the same thing:
`OllamaProvider` posts `format=<JSON Schema>` intact, while `to_gemini_schema` drops `title`
along with every other keyword Gemini rejects. A property both dialects keep is the one key
that identifies a stage on either endpoint.
"""

ROLE_STAGES: dict[str, tuple[str, ...]] = {
    "supervisor": ("SupervisorDecision", "FocusPreparationReport"),
    "researcher": ("ResearchAction",),
    "appraiser": ("AppraisalVerdict",),
}
"""Which stages belong to which role — the mapping `run_supervised` expresses by handing
`providers.<role>` to each stage function."""

COMBINATIONS = [
    pytest.param({}, id="all-local"),
    pytest.param({"appraiser_provider": "hosted"}, id="hosted-appraiser"),
    pytest.param(
        {"supervisor_provider": "hosted", "appraiser_provider": "hosted"},
        id="local-researcher-only",
    ),
]
"""The combinations that carry an architectural claim.

`all-local` is the committed default and the $0 guarantee, so it is the one that must never
regress. `hosted-appraiser` is the reason per-role selection exists at all: the Appraiser
judging the Researcher's evidence with a genuinely different model is an independent judgement
rather than one model agreeing with itself. The third keeps the Researcher — the role that
handles fetched source text — local while both reasoning roles are hosted, which is the shape a
privacy-conscious mixed run takes.
"""


def stage_of(request: httpx.Request) -> str:
    """Which stage sent this request, read from the schema it constrains."""
    body = json.loads(request.content)
    schema = body.get("format") or body["generationConfig"]["responseSchema"]
    properties = schema.get("properties", {})
    for prop, stage in _SIGNATURE_PROPERTY.items():
        if prop in properties:
            return stage
    raise AssertionError(f"unrecognised stage schema: {sorted(properties)}")


class Wire:
    """Both model endpoints, scripted by stage and recording which endpoint served each call.

    Keyed by stage rather than by call order because the order is the run's to choose: the
    subject here is where each request *went*, and a script that also pinned the sequence would
    fail for reasons that have nothing to do with provider selection.
    """

    def __init__(self, script: dict[str, list[str]]) -> None:
        self._script = {stage: list(replies) for stage, replies in script.items()}
        self.served: list[tuple[str, str]] = []

    def take(self, endpoint: str, request: httpx.Request) -> str:
        stage = stage_of(request)
        self.served.append((endpoint, stage))
        replies = self._script[stage]
        assert replies, f"the run asked for more {stage} replies than were scripted"
        return replies.pop(0)

    def endpoints_for(self, stage: str) -> set[str]:
        return {endpoint for endpoint, served in self.served if served == stage}


def serve_both_providers(wire: Wire, settings: Settings) -> None:
    """Ollama and Google AI Studio, answered locally in their own reply shapes."""
    respx.post(f"{settings.ollama_host}/api/chat").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json={
                "model": settings.local_model,
                "message": {"content": wire.take("local", request)},
                "done_reason": "stop",
            },
        )
    )
    respx.post(
        f"{settings.hosted_api_base}/models/{settings.hosted_model}:generateContent"
    ).mock(
        side_effect=lambda request: httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": wire.take("hosted", request)}]},
                        "finishReason": "STOP",
                    }
                ],
                "modelVersion": settings.hosted_model,
            },
        )
    )


@pytest.mark.parametrize("configured", COMBINATIONS)
@respx.mock
async def test_each_role_calls_the_provider_its_configuration_names(
    configured: dict[str, str], workspace: Settings, report
) -> None:
    """Changing a role's provider is a configuration change, and it reaches the model calls.

    Three failures this is the only guard against, every one of which still returns a
    perfectly valid report:

    * a role wired to another role's provider — a transposition in `from_settings`, or in the
      `providers.<role>` argument at a stage's call site;
    * every role collapsing onto one provider, which is what a mixed configuration silently
      becomes if the three instances are resolved but one of them is passed everywhere;
    * the all-local default quietly reaching the hosted endpoint, which would break the $0
      guarantee and send task text to Google without anyone asking for it.

    Nothing is injected: `prepare_focus_session` is given settings only, so this runs against
    the same `AgentProviders.from_settings` path a real run composes itself from.

    `GOOGLE_API_KEY` is set on every combination, including `all-local`, deliberately. It makes
    the hosted path genuinely available in each case, so a local role landing on it would be a
    successful HTTP call rather than a missing-key error — the failure has to be caught by
    routing, not by an absent credential.
    """
    settings = Settings(
        _env_file=None,
        db_path=workspace.db_path,
        google_api_key="test-key",
        **configured,
    )
    serve_indexes_page()
    wire = Wire(
        {
            "SupervisorDecision": [plan("RESEARCH", "what does a B-tree index do")],
            "ResearchAction": [
                tool_turn("web_search", {"query": DOCS_QUERY, "source_type": "docs"}),
                tool_turn("fetch_url", {"url": INDEXES_URL}),
                done(),
            ],
            "AppraisalVerdict": [verdict(True)],
            "FocusPreparationReport": [report(resources=[])],
        }
    )
    serve_both_providers(wire, settings)

    result = await prepare_focus_session(TASK, mode="multi", settings=settings)

    for role, stages in ROLE_STAGES.items():
        expected = settings.provider_for(role)
        for stage in stages:
            assert wire.endpoints_for(stage) == {expected}, (
                f"{role} is configured as {expected}, so every {stage} call must reach it"
            )

    assert isinstance(result, FocusPreparationReport)
    assert result.hops_used == 1, "the run really did delegate a hop under this combination"
    assert len(wire.served) == 6, (
        "one decision, three research turns, one verdict, one report — and nothing else. A "
        "seventh call would mean the finalise ladder's alternate provider fired on a run whose "
        "first report was already valid"
    )


# --- 7. the multi-hop decision belongs to the Appraiser (Day 5 T5) ---------------------------
#
# Sections 1-6 establish that the roles exist, stay separate and are reached through typed
# messages. None of them says who decides that the run continues — and until T5 the answer was
# "both": the Appraiser judged, and then the Supervisor's planner was asked again and could
# answer FINALISE straight over the top of an insufficient verdict. The plan is explicit that
# it may not (section 8.3: "The Supervisor's stop/continue decision depends entirely on the
# Appraiser's verdict"), and that is what this section holds.
#
# The scripts are the load-bearing part. Each supervisor below is scripted with exactly one
# planning reply and one report, whatever the hop count — so a run that consulted the planner
# a second time would consume its report as a decision and fail. "The hop came from the
# verdict" is therefore not something these tests assert around; it is the only way they can
# pass at all.

FOLLOWUP_QUERY = "how to read a postgresql explain plan"
"""`fixtures/search/explain-plans.json`, `source_type=general` — hop 2's own recording.

It is also a subject that exists *only* in what hop 1 read: `fixtures/html/article.html`
discusses `EXPLAIN ANALYZE`, and neither the task title nor the planner's prompt mentions it.
That is what makes a hop-2 query containing it evidence-derived rather than something the
original task could have produced on its own."""


def two_accepted() -> list[dict[str, str]]:
    """The judgement a `sufficient` verdict needs to actually stop the run.

    Since T5 the stop condition is `sufficient` **and at least two accepted sources**, so a
    bare `verdict(True)` is `thin_evidence` — a different stop, for a different reason. Tests
    that mean "the evidence was enough" have to say so in the verdict.
    """
    return [
        {"source": INDEXES_URL, "supports": "what a B-tree index does"},
        {"source": LEAD_URL, "supports": "how the tree is laid out"},
    ]


def second_hop_researcher() -> list[Any]:
    """The Researcher's turns for a run that hops twice."""
    return [
        tool_turn("web_search", {"query": DOCS_QUERY, "source_type": "docs"}),
        tool_turn("fetch_url", {"url": INDEXES_URL}),
        done(),
        tool_turn("web_search", {"query": FOLLOWUP_QUERY, "source_type": "general"}),
        done(),
    ]


@respx.mock
async def test_the_second_hop_is_the_appraisers_decision_not_the_planners(
    workspace: Settings, report
) -> None:
    """The headline claim: an insufficient verdict with a follow-up buys exactly one hop.

    The Supervisor here is scripted to have *nothing to say* after its first decision — its
    next reply is the report. Before T5 this run could not have reached two hops: the loop
    would have asked the planner again, been handed the report JSON as a `SupervisorDecision`,
    failed to parse it twice and stopped with `planner_unavailable` at one hop. So
    `hops_used == 2` is only reachable if the second hop was taken on the verdict alone.

    The follow-up subject is then traced into hop 2's assignment, because "a second hop
    happened" and "a second hop asked what the evidence asked for" are different claims and
    only the second one is multi-hop research. `FOLLOWUP_QUERY` appears in no prompt the
    planner ever saw, which is what rules out a question the original task could have produced
    by itself.
    """
    serve_indexes_page()
    roles = Roles(
        supervisor=[
            plan("RESEARCH", "what does a B-tree index do"),
            report(resources=[]),
        ],
        researcher=second_hop_researcher(),
        appraiser=[
            verdict(False, FOLLOWUP_QUERY),
            verdict(True, accepted=two_accepted()),
        ],
    )

    result, _ = await drive(roles, workspace, "multi")

    assert result.hops_used == 2, "the appraiser's follow-up bought exactly one more hop"
    assert roles.schemas(roles.supervisor) == [
        "SupervisorDecision",
        "FocusPreparationReport",
    ], "one planning turn for a two-hop run: the second hop was never re-decided"

    planned = roles.supervisor.calls[0].messages[0].content
    assert FOLLOWUP_QUERY not in planned, (
        "the follow-up subject must not be derivable from the task alone — nothing had read "
        "the page that mentions it when the planner was asked"
    )
    hop_two = roles.researcher.calls[-2].messages[0].content
    assert FOLLOWUP_QUERY in hop_two, (
        "hop 2's assignment must carry AppraisalVerdict.requested_followup verbatim"
    )
    assert roles.supervisor.remaining == 0 and roles.appraiser.remaining == 0


@respx.mock
async def test_a_sufficient_verdict_finalises_without_another_hop(
    workspace: Settings, report
) -> None:
    """The other half of the same rule: "enough" stops the run immediately.

    A loop that spends every hop it is allowed is not early-stopping, and on a live run each
    unnecessary hop costs SerpAPI quota and minutes of a user's session. The Researcher is
    scripted with one hop's turns only, so a second delegation cannot quietly succeed — it
    would exhaust the script and raise.
    """
    serve_indexes_page()
    roles = one_hop(report(resources=[]))
    roles.appraiser = FakeProvider([verdict(True, accepted=two_accepted())])

    result, _ = await drive(roles, workspace, "multi")

    assert result.hops_used == 1
    assert roles.researcher.remaining == 0, "one hop's turns, all of them spent"
    assert len(roles.schemas(roles.appraiser)) == 1, "judged once, then the run stopped"


@respx.mock
async def test_repeated_insufficient_verdicts_still_stop_at_the_hop_cap(
    tmp_path: Path, report
) -> None:
    """`MAX_HOPS` bounds a hop the Appraiser demanded exactly as it bounds one the planner chose.

    This is the failure mode T5 introduces and the reason the cap is checked *before* the
    follow-up is read: a verdict that always asks for more is now an instruction with no
    planner in between to decline it, so without `_stop_before_planning` running first the loop
    would keep hopping for as long as the Appraiser kept asking.

    `max_model_calls` is raised so the run is stopped by the hop cap rather than by the ledger
    — under the shipped 10 a cap that never fired would still pass.
    """
    settings = Settings(
        _env_file=None, db_path=tmp_path / "agent.sqlite3", max_model_calls=16
    )
    serve_indexes_page()
    roles = Roles(
        supervisor=[
            plan("RESEARCH", "what does a B-tree index do"),
            report(resources=[]),
        ],
        researcher=[
            *second_hop_researcher(),
            tool_turn(
                "web_search",
                {"query": "learned index structures", "source_type": "academic"},
            ),
            done(),
        ],
        appraiser=[verdict(False, "keep going") for _ in range(3)],
    )

    result, _ = await drive(roles, settings, "multi")

    assert result.hops_used == 3, "MAX_HOPS, and not one hop more"
    assert len(roles.schemas(roles.supervisor)) == 2, (
        "one decision and one report: the planner was not consulted for hops 2 and 3, and "
        "the cap stopped the loop before it could be consulted for a fourth"
    )
    assert any("hop limit" in unknown for unknown in result.unknowns), (
        "a run cut short by the cap must not read like one that finished"
    )


@respx.mock
async def test_a_sufficient_verdict_backed_by_one_source_finalises_honestly(
    workspace: Settings, report
) -> None:
    """The plan's stop condition is `sufficient` **and at least two accepted sources**.

    A judge that says "yes" while endorsing a single page has not met it, and the failure this
    guards against is the quiet one: the run returns a perfectly valid report whose reader has
    no way to tell that the session was planned on one source the Appraiser itself only
    half-committed to. So it finalises — one accepted source is not a reason to research more,
    since the verdict named no follow-up to spend a hop on — but it finalises *honestly*, with
    the reason in `unknowns` where an honest report puts its limits.
    """
    serve_indexes_page()
    roles = one_hop(report(resources=[]))
    roles.appraiser = FakeProvider(
        [verdict(True, accepted=[{"source": INDEXES_URL, "supports": "index types"}])]
    )

    result, _ = await drive(roles, workspace, "multi")

    assert result.hops_used == 1, "a thin yes is still a stop, not another hop"
    assert render_stop_reason("thin_evidence") in result.unknowns
    assert roles.researcher.remaining == 0


# --- 8. the trace records the topology (Day 5 T6) --------------------------------------------
#
# Sections 3-5 prove the roles are separate by reading what each model was asked for, what
# crossed each boundary, and what each module may import. All three are claims about the code.
# This section is the claim about the *recorded run*: after the fact, from the rows alone,
# someone must be able to see which agent did what and which tool calls belonged to whom.
#
# That is what an `agent` span buys, and the parenting is not passed anywhere — `Tracer.
# open_span` reads `RunContext.span_stack`, so a tool call made while a role's span is open
# nests under it with no argument threaded through the researcher and no change to
# `tools/hooks.py`. These tests are what stop that derivation being silently lost.


def span_tree(
    ledger: sqlite3.Connection, run_id: str
) -> tuple[list[str], dict[str, list[str]]]:
    """One run's spans as `(root names, {span name: child names})`, in start order.

    Names rather than ids, because an id proves the shape to a test and nothing to a reader:
    `researcher.loop → web_search` is the assertion this section is actually about. Uniqueness
    is asserted rather than assumed, so a run that opened two spans with one name cannot
    silently collapse into a tree that looks right.
    """
    spans = get_spans(ledger, run_id)
    names = [span.name for span in spans]
    assert len(set(names)) == len(names), f"span names repeat in this run: {names}"

    name_of = {span.span_id: span.name for span in spans}
    roots: list[str] = []
    children: dict[str, list[str]] = {name: [] for name in names}
    for span in spans:
        parent = name_of.get(span.parent_span_id) if span.parent_span_id else None
        if parent is None:
            roots.append(span.name)
        else:
            children[parent].append(span.name)
    return roots, children


def indent_of(lines: list[str], name: str) -> int:
    """How far into its line a span's name starts — the tree prefix's width, in characters."""
    line = next(line for line in lines if name in line)
    return len(line) - len(line.lstrip("│├└─ "))


@respx.mock
async def test_the_trace_shows_which_agent_made_every_tool_call(
    workspace: Settings, ledger: sqlite3.Connection, report
) -> None:
    """The whole of T6, as one recorded run.

    Four failures, each of which leaves a perfectly healthy run and a useless trace:

    * **no agent spans at all** — the Day 4 shape, where every tool call hangs off the run and
      a reader cannot tell a Supervisor from a Researcher;
    * **a flat list of agent spans** — the roles appear but nothing is nested, so "the
      Supervisor coordinates" is unrecorded and the workers look like peers of it;
    * **tool calls parented to the wrong role**, which is what happens the moment a span is
      opened outside the frame that does the work, or closed before it;
    * **a tool call under the Appraiser** — the one that is not a display problem. The
      Appraiser judging its own fresh evidence is the failure the split exists to prevent, and
      an empty child list is where that becomes checkable on a real run rather than by reading
      imports.

    The memory tools sitting under `supervisor.run` rather than under a worker is the same
    claim from the other side: filing a hop away is the coordinator's bookkeeping.
    """
    serve_indexes_page()
    roles = one_hop(report(resources=[]))

    _, ctx = await drive(roles, workspace, "multi")

    roots, children = span_tree(ledger, ctx.run_id)

    assert roots == ["supervisor.run"], "one root: the Supervisor holds the whole run"
    assert children["supervisor.run"] == [
        "recall_previous_preparation",
        "supervisor.decide",
        "researcher.loop",
        "appraiser.judge",
        "record_run_memory",
        "supervisor.finalise",
        "save_preparation",
    ], "the coordination, in the order it happened, with both workers underneath it"
    assert children["researcher.loop"] == ["web_search", "fetch_url"], (
        "every tool the Researcher reached nests under the Researcher"
    )
    assert children["appraiser.judge"] == [], "the Appraiser performs no research"
    assert children["supervisor.decide"] == []
    assert children["supervisor.finalise"] == []

    spans = get_spans(ledger, ctx.run_id)
    agents = {span.name for span in spans if span.kind == "agent"}
    assert agents == {
        "supervisor.run",
        "supervisor.decide",
        "researcher.loop",
        "appraiser.judge",
        "supervisor.finalise",
    }
    assert all(span.ok for span in spans), "a successful run closes every span ok"


@respx.mock
async def test_the_existing_renderer_displays_the_agent_hierarchy(
    workspace: Settings, ledger: sqlite3.Connection, report
) -> None:
    """`scripts/show_trace.py`'s renderer, unchanged, over a real multi-agent run.

    The renderer was proven at depth on Day 4 against synthesised rows, which left one thing
    open: whether the rows a genuine run *writes* form the tree it was proven against. This
    closes it, and it is the assertion that catches a regression the previous test cannot —
    spans correctly parented in SQLite but flattened on the way to the screen, which is the
    only form in which anybody actually reads a trace.

    Indentation is asserted as a relation rather than an exact width, so the tree stays
    readable evidence without pinning `render.py`'s column layout to this test.
    """
    serve_indexes_page()
    roles = one_hop(report(resources=[]))

    _, ctx = await drive(roles, workspace, "multi")
    lines = render_trace(get_run(ledger, ctx.run_id), get_spans(ledger, ctx.run_id))

    assert indent_of(lines, "supervisor.run") < indent_of(lines, "researcher.loop")
    assert indent_of(lines, "researcher.loop") < indent_of(lines, "web_search")
    assert indent_of(lines, "researcher.loop") == indent_of(lines, "appraiser.judge"), (
        "the two workers are siblings under the Supervisor, never nested in each other"
    )
    assert indent_of(lines, "fetch_url") > indent_of(lines, "appraiser.judge")


@respx.mock
async def test_a_single_agent_run_traces_as_one_agent(
    workspace: Settings, ledger: sqlite3.Connection, report
) -> None:
    """The mode that has no delegation records none — and still records its tool calls.

    Two regressions in one run. Emitting role spans here would make a single-agent trace
    indistinguishable from a multi-agent one, which is the distinction the whole subtask
    exists to create. And a `tracer` that reached only `run_supervised` would leave this
    mode's tools hanging off the run with no owner — Day 4's shape, kept by accident rather
    than by decision.
    """
    serve_indexes_page()
    roles = one_hop(report(resources=[]))

    _, ctx = await drive(roles, workspace, "single")

    roots, children = span_tree(ledger, ctx.run_id)

    assert roots == ["agent.run"], "one agent, so one agent span"
    assert children["agent.run"] == [
        "recall_previous_preparation",
        "web_search",
        "fetch_url",
        "record_run_memory",
        "save_preparation",
    ], "every tool call belongs to the one agent that made it"
    assert {span.name for span in get_spans(ledger, ctx.run_id) if span.kind == "agent"} == {
        "agent.run"
    }, "no supervisor, researcher or appraiser span: nothing was delegated"
