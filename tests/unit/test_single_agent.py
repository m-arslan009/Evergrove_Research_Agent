"""The loop: what makes it stop, and what it does when something goes wrong.

The four stages are thin — they render a prompt, spend a model call and validate a reply —
and every piece they lean on is already proven elsewhere: the schemas in
`test_agent_schemas.py`, the bridge in `test_tool_calling.py`, the renderers in
`test_prompt_context.py`, the ledger in `test_run_budget.py`. None of that is re-proven here.

What this suite protects is the part that only exists once the pieces are joined, and where
the failure is expensive and silent:

* every exit from the loop, because a loop a model can drive is a loop a model can drive
  forever;
* the reserve that keeps a report affordable, because spending the last model call on a
  research turn loses the entire run;
* a mistake staying recoverable — a guessed tool name, a refused budget, an unavailable
  search — rather than ending the run or quietly costing it a limit;
* a limitation reaching the report, because a degraded run that reads like a confident one
  is the one failure nobody can see afterwards.

Offline and model-free throughout: `FakeProvider` is injected rather than patched, `respx`
is active so any unrouted HTTP call fails the test, the search fixtures and database live
under `tmp_path`, and the timeout is driven by an injected clock rather than a real wait.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from evergrove_agent.agents.single_agent import (
    AgentProviders,
    PreparationFailed,
    run_agent,
)
from evergrove_agent.config import Settings
from evergrove_agent.llm import FakeProvider
from evergrove_agent.llm.base import LLMResponse, ToolCall
from evergrove_agent.schemas import TaskContext
from evergrove_agent.tools import RunBudget, RunContext, ToolRegistry
from evergrove_agent.tools.wiring import build_tool_registry

QUERY = "postgresql b-tree index"
DOCS_URL = "https://www.postgresql.org/docs/current/indexes-types.html"
PAGE = (
    "<html><head><title>Index Types</title></head><body><p>"
    + "A B-tree index handles equality and range queries on sortable data. " * 20
    + "</p></body></html>"
)


class FakeClock:
    """The injected clock from `test_run_budget.py`, so a 900-second timeout costs no wait."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def offline_settings(tmp_path: Path) -> Settings:
    """Defaults pointed at temporary paths — never the real `DB_PATH` or fixture set."""
    return Settings(
        _env_file=None,
        db_path=tmp_path / "agent.sqlite3",
        search_fixture_dir=tmp_path / "search",
    )


def recorded_search(settings: Settings) -> None:
    """One committed-shape recording, so the fixture backend can answer `QUERY`."""
    settings.search_fixture_dir.mkdir(parents=True, exist_ok=True)
    (settings.search_fixture_dir / "recorded.json").write_text(
        json.dumps(
            {
                "query": QUERY,
                "source_type": "docs",
                "recorded_from": "handwritten",
                "results": [
                    {
                        "url": DOCS_URL,
                        "title": "PostgreSQL: Index Types",
                        "snippet": "B-tree is the default index type.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def recording_registry(settings: Settings) -> tuple[ToolRegistry, list[str]]:
    """A wired registry that also reports every name that reached `call`.

    The list is how a test proves a call was stopped *before* the registry — the only
    difference between a budget that is enforced and one that is merely reported.
    """
    registry = build_tool_registry(settings)
    seen: list[str] = []
    inner = registry.call

    async def recording(name, args, ctx):
        seen.append(name)
        return await inner(name, args, ctx)

    registry.call = recording
    return registry, seen


# --- scripting a model -------------------------------------------------------------------


def plan(action: str, question: str | None = None) -> str:
    return json.dumps(
        {
            "action": action,
            "research_question": question,
            "source_preference": "docs",
            "reasoning": "because",
        }
    )


def verdict(
    sufficient: bool, followup: str | None = None, missing: tuple[str, ...] = ()
) -> str:
    return json.dumps(
        {
            "sufficient": sufficient,
            "missing_information": list(missing),
            "requested_followup": followup,
            "reasoning": "because",
        }
    )


def tool_turn(*calls: tuple[str, dict[str, Any]]) -> LLMResponse:
    """A model turn that asks for tools. `FakeProvider` turns a `str` into plain text, so a
    tool call has to be scripted as a whole `LLMResponse`."""
    return LLMResponse(
        text="",
        model="fake-model",
        provider="fake",
        tool_calls=[ToolCall(name=name, arguments=args) for name, args in calls],
    )


async def drive(
    script: list[Any],
    settings: Settings,
    *,
    registry: ToolRegistry | None = None,
    budget: RunBudget | None = None,
) -> tuple[Any, FakeProvider, RunContext]:
    """One run, with one `FakeProvider` answering for all three roles."""
    provider = FakeProvider(script)
    ctx = RunContext(budget=budget or RunBudget.from_settings(settings))
    report = await run_agent(
        TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25),
        registry=registry or build_tool_registry(settings),
        providers=AgentProviders(provider, provider, provider),
        ctx=ctx,
        settings=settings,
    )
    return report, provider, ctx


def prompts_for(provider: FakeProvider, schema_name: str) -> list[str]:
    """Every prompt sent to one constrained stage, in order — how a test reads what the
    loop actually put in front of the model, rather than what it meant to."""
    return [
        call.messages[0].content
        for call in provider.calls
        if call.schema_name == schema_name
    ]


# --- how the loop stops --------------------------------------------------------------------


@respx.mock
async def test_hop_cap_holds_against_a_model_that_always_wants_more(
    offline_settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    """A model that answers RESEARCH forever must not research forever.

    The planner is asked once per hop, so an uncapped loop calls it a fourth time and keeps
    going. `MAX_HOPS` is the only bound between a drifting model and an unbounded run, and
    it has to hold from the loop, not from a model's cooperation.
    """
    script: list[Any] = []
    for hop in range(3):
        script += [plan("RESEARCH", f"question {hop}"), "notes", verdict(False, "more")]
    script.append(json.dumps(valid_report_payload))

    report, provider, _ = await drive(script, offline_settings)

    assert report.hops_used == 3
    assert len(prompts_for(provider, "SupervisorDecision")) == 3, (
        "the planner was asked again after the last hop; the cap must skip it entirely"
    )
    assert provider.remaining == 0


@respx.mock
async def test_a_sufficient_verdict_stops_with_hops_to_spare(
    offline_settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    """Early stopping is the feature, not an optimisation.

    Catches a loop that spends every hop it is allowed because it can. One usable session is
    the goal; a run that keeps researching after the appraiser said "enough" burns SerpAPI
    quota and session time for nothing.
    """
    script = [plan("RESEARCH", "q"), "notes", verdict(True), json.dumps(valid_report_payload)]

    report, _, ctx = await drive(script, offline_settings)

    assert report.hops_used == 1
    assert ctx.budget.hops_remaining(report.hops_used) == 2


@respx.mock
async def test_a_later_hop_asks_the_appraisers_follow_up(
    offline_settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    """The second hop must come from what hop 1 read, not from a scripted sequence.

    This is the whole multi-hop claim. If `requested_followup` does not reach the planner's
    prompt, the loop still performs a second hop and still looks correct — it is just
    re-asking a question nothing discovered, which is a retry wearing an agent's clothes.
    """
    followup = "When is a partial index preferable to a full B-tree index?"
    script = [
        plan("RESEARCH", "what does a B-tree index do"),
        "notes from hop 1",
        verdict(False, followup, ("partial indexes",)),
        plan("RESEARCH", followup),
        "notes from hop 2",
        verdict(True),
        json.dumps(valid_report_payload),
    ]

    report, provider, _ = await drive(script, offline_settings)

    second_plan = prompts_for(provider, "SupervisorDecision")[1]
    assert followup in second_plan
    assert "what does a B-tree index do" in second_plan, (
        "hop 1's question must be listed as already spent, or hop 2 may repeat it"
    )
    assert report.hops_used == 2


@respx.mock
async def test_no_follow_up_ends_the_run_rather_than_inventing_one(
    offline_settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    """"Not enough, and nothing specific would help" is a real verdict.

    No validator forces a follow-up when `sufficient` is false, deliberately. A loop that
    treated the missing question as an error would either retry forever or make one up —
    both worse than a short, honest report.
    """
    script = [
        plan("RESEARCH", "q"),
        "notes",
        verdict(False, None, ("something unobtainable",)),
        json.dumps(valid_report_payload),
    ]

    report, provider, _ = await drive(script, offline_settings)

    assert report.hops_used == 1
    assert len(prompts_for(provider, "SupervisorDecision")) == 1


# --- the budget ------------------------------------------------------------------------------


@respx.mock
async def test_a_report_is_still_affordable_after_a_greedy_loop(
    tmp_path: Path, valid_report_payload: dict[str, Any]
) -> None:
    """`claim("model_call")` does not know which stage is asking.

    Without the finalise reserve, a loop that researches until the ledger says no spends the
    last model call on a research turn and the run produces nothing at all — the single most
    expensive way this design can fail, because every search and fetch is already paid for
    by then.
    """
    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "agent.sqlite3",
        search_fixture_dir=tmp_path / "search",
        max_model_calls=4,
    )
    script = [plan("RESEARCH", "q"), "notes", verdict(False, "more"), json.dumps(valid_report_payload)]

    report, provider, ctx = await drive(script, settings)

    assert report is not None
    assert ctx.budget.remaining("model_call") == 0
    assert provider.remaining == 0, "the reserve must be spent on the report, not on a hop"


@respx.mock
async def test_a_refused_budget_stops_a_tool_before_the_registry(
    tmp_path: Path, valid_report_payload: dict[str, Any]
) -> None:
    """A budget that is only reported is not a budget.

    The claim happens before dispatch precisely so an exhausted counter cannot be spent
    anyway; this proves the second search never reached the registry, rather than reaching it
    and being counted twice.
    """
    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "agent.sqlite3",
        search_fixture_dir=tmp_path / "search",
        max_search_calls=1,
    )
    recorded_search(settings)
    registry, seen = recording_registry(settings)
    script = [
        plan("RESEARCH", "q"),
        tool_turn(("web_search", {"query": QUERY, "source_type": "docs"})),
        tool_turn(("web_search", {"query": "a second query", "source_type": "docs"})),
        "notes",
        verdict(True),
        json.dumps(valid_report_payload),
    ]

    report, _, ctx = await drive(script, settings, registry=registry)

    assert seen.count("web_search") == 1
    assert ctx.budget.remaining("search") == 0
    assert report is not None


async def test_an_expired_deadline_fails_loudly_rather_than_partially(
    offline_settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    """A run out of time cannot write a report, and must not return half of one.

    `claim` refuses once the deadline passes, finalise included. A partial report is the
    worst outcome available here: it looks like a plan, so nobody checks it.
    """
    clock = FakeClock()
    budget = RunBudget.from_settings(offline_settings, clock=clock)
    clock.now = float(offline_settings.total_run_timeout_s) + 1.0

    with pytest.raises(PreparationFailed):
        await drive([json.dumps(valid_report_payload)], offline_settings, budget=budget)


# --- mistakes stay recoverable ---------------------------------------------------------------


@respx.mock
async def test_a_guessed_tool_name_costs_nothing_and_the_hop_continues(
    offline_settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    """A model reaching for a tool it was never offered is an ordinary turn, not a failure.

    Two regressions in one: the name must not reach the registry (registered would become
    reachable), and it must not consume a search — a run whose budget can be drained by
    guessing names has no budget.
    """
    recorded_search(offline_settings)
    registry, seen = recording_registry(offline_settings)
    script = [
        plan("RESEARCH", "q"),
        tool_turn(("browse_web", {"q": "anything"})),
        tool_turn(("web_search", {"query": QUERY, "source_type": "docs"})),
        "notes",
        verdict(True),
        json.dumps(valid_report_payload),
    ]

    report, _, ctx = await drive(script, offline_settings, registry=registry)

    assert "browse_web" not in seen
    assert ctx.budget.remaining("search") == offline_settings.max_search_calls - 1
    assert report is not None


@respx.mock
async def test_output_that_drifts_twice_degrades_to_a_report(
    offline_settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    """`extra="forbid"` turns drift into a retry — it must not turn it into a loop.

    One re-ask quoting the errors, then the stage gives up and the run finalises. Catches a
    re-ask that never terminates, which on a 4B model is the likeliest way this burns a whole
    budget for nothing.
    """
    script = [
        json.dumps({"unexpected": "shape"}),
        json.dumps({"still": "wrong"}),
        json.dumps(valid_report_payload),
    ]

    report, provider, _ = await drive(script, offline_settings)

    assert report.hops_used == 0
    assert len(prompts_for(provider, "SupervisorDecision")) == 2
    assert provider.remaining == 0


# --- a limitation has to reach the report -----------------------------------------------------


@respx.mock
async def test_an_unavailable_search_degrades_the_report_instead_of_crashing(
    offline_settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    """A hop that gathered nothing still has to produce an honest report.

    The fixture directory is empty, so `web_search` answers `SEARCH_UNAVAILABLE`. The failure
    must survive as far as the finalise prompt: a run whose search was broken and whose
    report does not say so is indistinguishable from one that simply found little.
    """
    script = [
        plan("RESEARCH", "q"),
        tool_turn(("web_search", {"query": QUERY, "source_type": "docs"})),
        "the search failed",
        verdict(True),
        json.dumps(valid_report_payload),
    ]

    report, provider, _ = await drive(script, offline_settings)

    assert report.sources_examined == 0
    assert "SEARCH_UNAVAILABLE" in prompts_for(provider, "FocusPreparationReport")[0]


@respx.mock
async def test_the_hop_cap_is_named_in_the_reports_unknowns(
    offline_settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    """Why a run stopped short is bookkeeping, not something the model is trusted to add.

    It travels as an extra message too, but a model may ignore it. Appending it is what
    guarantees a run cut off by a limit never reads like one that finished.
    """
    script: list[Any] = []
    for hop in range(3):
        script += [plan("RESEARCH", f"q{hop}"), "notes", verdict(False, "more")]
    script.append(json.dumps({**valid_report_payload, "unknowns": []}))

    report, _, _ = await drive(script, offline_settings)

    assert any("hop limit" in unknown for unknown in report.unknowns)


@respx.mock
async def test_a_fetched_page_becomes_read_evidence(
    offline_settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    """`retrieved_at` is what separates evidence from a lead, and it is set here or nowhere.

    Three things rest on the distinction: the authority rule in `finalise.md`, S9's grounding
    check, and `sources_examined`. A fetch that does not upgrade its lead leaves the run
    claiming it examined nothing, and leaves every citation ungroundable.
    """
    recorded_search(offline_settings)
    respx.get(DOCS_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text=PAGE
        )
    )
    script = [
        plan("RESEARCH", "q"),
        tool_turn(("web_search", {"query": QUERY, "source_type": "docs"})),
        tool_turn(("fetch_url", {"url": DOCS_URL})),
        "notes",
        verdict(True),
        json.dumps(valid_report_payload),
    ]

    report, provider, ctx = await drive(script, offline_settings)

    assert report.sources_examined == 1, "the fetch must upgrade its lead, not add a second"
    assert ctx.budget.remaining("fetch") == offline_settings.max_fetch_calls - 1
    assert "status: read" in prompts_for(provider, "AppraisalVerdict")[0]
