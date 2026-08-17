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
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from evergrove_agent.agents.single_agent import (
    AgentProviders,
    PreparationFailed,
    finalise,
    run_agent,
)
from evergrove_agent.config import Settings
from evergrove_agent.llm import FakeProvider
from evergrove_agent.memory import db, prep_memory, run_memory
from evergrove_agent.schemas import (
    FocusPreparationReport,
    ResearchAction,
    RunState,
    TaskContext,
)
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


def recording_tool(registry: ToolRegistry, name: str) -> list[Any]:
    """A wired tool that also reports every call that reached the tool *itself*.

    Since Day 4 T3 the budget is enforced by the registry's own pre-hook, so a refused call
    reaches `ToolRegistry.call` and is stopped inside it. Registry entry therefore no
    longer distinguishes an enforced budget from a decorative one — tool execution does,
    and that is what this observes.
    """
    tool = registry.get(name)
    assert tool is not None, f"{name} is not registered"
    executed: list[Any] = []
    inner = tool.run

    async def recording(args, ctx):
        executed.append(args)
        return await inner(args, ctx)

    tool.run = recording
    return executed


def recording_registry(settings: Settings) -> tuple[ToolRegistry, list[str]]:
    """A wired registry that also reports every name that reached `call`.

    The list is how a test proves a call was stopped *before* the registry — which, since
    the budget moved into the registry, means the menu guard rather than the ledger.
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


def tool_turn(*calls: tuple[str, dict[str, Any]]) -> str:
    """A model turn that asks for a tool, as S14's constrained `ResearchAction`.

    The research step stopped using free-form tool calling when S14 measured it at 361 s a
    turn against 46 s for a constrained call, so a turn is now a JSON reply rather than a
    whole `LLMResponse` — and `FakeProvider` returns a `str` as the reply text, which is
    exactly what `run_research_step` parses.

    Still varargs so the call sites read unchanged, but the loop dispatches **one** call per
    turn now: more than one here is a scripting mistake, not a second dispatch.
    """
    if len(calls) != 1:
        raise ValueError("a research turn is one tool call since the S14 contingency swap")
    name, arguments = calls[0]
    return ResearchAction(
        tool=name, arguments=arguments, reasoning="scripted turn"
    ).model_dump_json()


@pytest.fixture
def scripted_report(
    valid_report_payload: dict[str, Any],
) -> Callable[..., str]:
    """A report a model could plausibly return, that grounding accepts: it cites nothing.

    The shared payload cites a real PostgreSQL docs page, which is right for
    `test_validate_report.py` and wrong here — most of these runs gather no evidence at all,
    so since S10 enforces grounding inside `finalise`, that citation would fail every one of
    them for a reason none of these tests is about. An empty `resources` is valid
    (`min_length=0`) and grounds vacuously, which keeps each test failing only for the thing
    it exists to catch. Citations are proven deliberately, further down.
    """

    def _report(**overrides: Any) -> str:
        return json.dumps({**valid_report_payload, "resources": [], **overrides})

    return _report


def resource(url: str, *, authority: str = "official") -> dict[str, str]:
    """One citation, for the tests that are about citations."""
    return {
        "title": "PostgreSQL: Index Types",
        "url": url,
        "why_this_source": "Official documentation for the exact version in use",
        "authority": authority,
    }


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
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """A model that answers RESEARCH forever must not research forever.

    The planner is asked once per hop, so an uncapped loop calls it a fourth time and keeps
    going. `MAX_HOPS` is the only bound between a drifting model and an unbounded run, and
    it has to hold from the loop, not from a model's cooperation.
    """
    script: list[Any] = []
    for hop in range(3):
        script += [plan("RESEARCH", f"question {hop}"), "notes", verdict(False, "more")]
    script.append(scripted_report())

    report, provider, _ = await drive(script, offline_settings)

    assert report.hops_used == 3
    assert len(prompts_for(provider, "SupervisorDecision")) == 3, (
        "the planner was asked again after the last hop; the cap must skip it entirely"
    )
    assert provider.remaining == 0


@respx.mock
async def test_a_sufficient_verdict_stops_with_hops_to_spare(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """Early stopping is the feature, not an optimisation.

    Catches a loop that spends every hop it is allowed because it can. One usable session is
    the goal; a run that keeps researching after the appraiser said "enough" burns SerpAPI
    quota and session time for nothing.
    """
    script = [plan("RESEARCH", "q"), "notes", verdict(True), scripted_report()]

    report, _, ctx = await drive(script, offline_settings)

    assert report.hops_used == 1
    assert ctx.budget.hops_remaining(report.hops_used) == 2


@respx.mock
async def test_a_later_hop_asks_the_appraisers_follow_up(
    offline_settings: Settings, scripted_report: Callable[..., str]
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
        scripted_report(),
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
    offline_settings: Settings, scripted_report: Callable[..., str]
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
        scripted_report(),
    ]

    report, provider, _ = await drive(script, offline_settings)

    assert report.hops_used == 1
    assert len(prompts_for(provider, "SupervisorDecision")) == 1


# --- the budget ------------------------------------------------------------------------------


@respx.mock
async def test_a_report_is_still_affordable_after_a_greedy_loop(
    tmp_path: Path, scripted_report: Callable[..., str]
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
    script = [plan("RESEARCH", "q"), "notes", verdict(False, "more"), scripted_report()]

    report, provider, ctx = await drive(script, settings)

    assert report is not None
    assert ctx.budget.remaining("model_call") == 0
    assert provider.remaining == 0, "the reserve must be spent on the report, not on a hop"


@respx.mock
async def test_a_refused_budget_stops_a_tool_before_it_runs(
    tmp_path: Path, scripted_report: Callable[..., str]
) -> None:
    """A budget that is only reported is not a budget.

    The claim happens before the tool runs precisely so an exhausted counter cannot be spent
    anyway; this proves the second search never reached the search tool, rather than reaching
    it and being counted twice. Both calls now reach `ToolRegistry.call` — that is where the
    refusal lives since Day 4 T3 — so the assertion is about execution, not entry, and the
    loop carries on to a report either way.
    """
    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "agent.sqlite3",
        search_fixture_dir=tmp_path / "search",
        max_search_calls=1,
    )
    recorded_search(settings)
    registry, seen = recording_registry(settings)
    executed = recording_tool(registry, "web_search")
    script = [
        plan("RESEARCH", "q"),
        tool_turn(("web_search", {"query": QUERY, "source_type": "docs"})),
        tool_turn(("web_search", {"query": "a second query", "source_type": "docs"})),
        "notes",
        verdict(True),
        scripted_report(),
    ]

    report, _, ctx = await drive(script, settings, registry=registry)

    assert len(executed) == 1, "the second search was refused before the tool ran"
    assert seen.count("web_search") == 2, "the refusal happens inside the registry now"
    assert ctx.budget.remaining("search") == 0
    assert report is not None


async def test_an_expired_deadline_fails_loudly_rather_than_partially(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """A run out of time cannot write a report, and must not return half of one.

    `claim` refuses once the deadline passes, finalise included. A partial report is the
    worst outcome available here: it looks like a plan, so nobody checks it.
    """
    clock = FakeClock()
    budget = RunBudget.from_settings(offline_settings, clock=clock)
    clock.now = float(offline_settings.total_run_timeout_s) + 1.0

    with pytest.raises(PreparationFailed) as raised:
        await drive([scripted_report()], offline_settings, budget=budget)

    assert raised.value.attempts == 0, (
        "the deadline refused the first attempt, so the ladder never ran; a non-zero count "
        "here would mean a retry was made after the budget said no"
    )


# --- mistakes stay recoverable ---------------------------------------------------------------


@respx.mock
async def test_a_guessed_tool_name_costs_nothing_and_the_hop_continues(
    offline_settings: Settings, scripted_report: Callable[..., str]
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
        scripted_report(),
    ]

    report, _, ctx = await drive(script, offline_settings, registry=registry)

    assert "browse_web" not in seen
    assert ctx.budget.remaining("search") == offline_settings.max_search_calls - 1
    assert report is not None


@respx.mock
async def test_output_that_drifts_twice_degrades_to_a_report(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """`extra="forbid"` turns drift into a retry — it must not turn it into a loop.

    One re-ask quoting the errors, then the stage gives up and the run finalises. Catches a
    re-ask that never terminates, which on a 4B model is the likeliest way this burns a whole
    budget for nothing.
    """
    script = [
        json.dumps({"unexpected": "shape"}),
        json.dumps({"still": "wrong"}),
        scripted_report(),
    ]

    report, provider, _ = await drive(script, offline_settings)

    assert report.hops_used == 0
    assert len(prompts_for(provider, "SupervisorDecision")) == 2
    assert provider.remaining == 0


# --- a limitation has to reach the report -----------------------------------------------------


@respx.mock
async def test_an_unavailable_search_degrades_the_report_instead_of_crashing(
    offline_settings: Settings, scripted_report: Callable[..., str]
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
        scripted_report(),
    ]

    report, provider, _ = await drive(script, offline_settings)

    assert report.sources_examined == 0
    assert "SEARCH_UNAVAILABLE" in prompts_for(provider, "FocusPreparationReport")[0]


@respx.mock
async def test_the_hop_cap_is_named_in_the_reports_unknowns(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """Why a run stopped short is bookkeeping, not something the model is trusted to add.

    It travels as an extra message too, but a model may ignore it. Appending it is what
    guarantees a run cut off by a limit never reads like one that finished.
    """
    script: list[Any] = []
    for hop in range(3):
        script += [plan("RESEARCH", f"q{hop}"), "notes", verdict(False, "more")]
    script.append(scripted_report(unknowns=[]))

    report, _, _ = await drive(script, offline_settings)

    assert any("hop limit" in unknown for unknown in report.unknowns)


@respx.mock
async def test_a_fetched_page_becomes_read_evidence(
    offline_settings: Settings, scripted_report: Callable[..., str]
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
        scripted_report(),
    ]

    report, provider, ctx = await drive(script, offline_settings)

    assert report.sources_examined == 1, "the fetch must upgrade its lead, not add a second"
    assert ctx.budget.remaining("fetch") == offline_settings.max_fetch_calls - 1
    assert "status: read" in prompts_for(provider, "AppraisalVerdict")[0]


# --- the retry ladder (S10) --------------------------------------------------------------
#
# S9 wrote the grounding rule and proved it in `test_validate_report.py`; nothing here
# re-proves the rule itself. What is new — and what only exists once the validator is wired
# into `finalise` — is that a report which breaks it is *refused and asked again*, that the
# asking is bounded, and that a run which cannot produce a valid report fails instead of
# returning one anyway.


UNGROUNDED_URL = "https://example.com/an-invented-guide"
"""A page no run in this suite ever searches for or opens."""


def serve_docs_page() -> None:
    """`DOCS_URL` answered locally, so a run can genuinely *read* something to cite."""
    respx.get(DOCS_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text=PAGE
        )
    )


def researched(*reports: str) -> list[Any]:
    """A run that searches, opens `DOCS_URL`, is judged sufficient, and then finalises.

    Every ladder test needs real evidence behind it — a grounding check against an empty
    evidence set proves only that nothing can be cited. The finalise attempts are whatever
    the test passes in.
    """
    return [
        plan("RESEARCH", "what does a B-tree index do"),
        tool_turn(("web_search", {"query": QUERY, "source_type": "docs"})),
        tool_turn(("fetch_url", {"url": DOCS_URL})),
        "notes",
        verdict(True),
        *reports,
    ]


def finalise_turns(provider: FakeProvider) -> list[Any]:
    """Every attempt at the report, in order — the ladder as the model experienced it."""
    return [
        call for call in provider.calls if call.schema_name == "FocusPreparationReport"
    ]


@respx.mock
async def test_an_ungrounded_citation_is_refused_and_the_correction_is_returned(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """The reason S10 exists: the grounding rule is now enforced, not merely available.

    Until the validator was wired into `finalise`, a report citing a page the run never saw
    was returned as though it were true — the precise failure set membership was written to
    make impossible. Two halves matter equally: the bad report is rejected, and the retry
    names the offending URL, because a model told only "that was wrong" replaces one
    invented source with another.
    """
    recorded_search(offline_settings)
    serve_docs_page()
    script = researched(
        scripted_report(resources=[resource(UNGROUNDED_URL)]),
        scripted_report(resources=[resource(DOCS_URL)]),
    )

    report, provider, _ = await drive(script, offline_settings)

    assert [str(item.url) for item in report.resources] == [DOCS_URL]
    attempts = finalise_turns(provider)
    assert len(attempts) == 2
    assert UNGROUNDED_URL in attempts[1].messages[-1].content


@respx.mock
async def test_a_grounded_report_costs_exactly_one_attempt(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """A validator that rejects good work is worse than no validator at all.

    The ladder must not fire on a report that cites a page the run actually opened. If it
    did, every healthy run would pay two or three extra model calls — on this hardware, a
    minute each — and the reserve that keeps a report affordable would stop being enough.
    """
    recorded_search(offline_settings)
    serve_docs_page()
    script = researched(scripted_report(resources=[resource(DOCS_URL)]))

    report, provider, _ = await drive(script, offline_settings)

    assert len(finalise_turns(provider)) == 1
    assert report.sources_examined == 1


@respx.mock
async def test_a_report_that_never_grounds_fails_loudly_with_its_errors(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """The rule the whole subtask exists for: never return a report that failed the check.

    Three attempts, three ungrounded reports, and the run must end in a failure the caller
    can see rather than the last invalid report — a plausible-looking plan nobody checks is
    the one outcome worse than no plan. The errors travel with the exception because that is
    all a failed run has to explain itself with until S12 builds a CLI.
    """
    recorded_search(offline_settings)
    serve_docs_page()
    script = researched(
        *[scripted_report(resources=[resource(UNGROUNDED_URL)])] * 3
    )

    with pytest.raises(PreparationFailed) as raised:
        await drive(script, offline_settings)

    assert raised.value.attempts == 3, "MAX_OUTPUT_RETRIES counts attempts, not retries"
    assert [issue.code for issue in raised.value.issues] == ["ungrounded_url"]
    assert raised.value.run_id is not None


@respx.mock
async def test_a_drifted_report_costs_one_attempt_and_the_next_one_is_returned(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """A reply that misses the schema is the same event as one that breaks a rule.

    Both mean *this report is not acceptable*, so both take one rung of the same ladder.
    Before S10 a drift here ended the run on the first occurrence — an expensive way to lose
    a fully-researched run to a 4B model's stray field, when a re-ask quoting the error
    usually fixes it.
    """
    recorded_search(offline_settings)
    serve_docs_page()
    script = researched(
        json.dumps({"definitely": "not a report"}),
        scripted_report(resources=[resource(DOCS_URL)]),
    )

    report, provider, _ = await drive(script, offline_settings)

    attempts = finalise_turns(provider)
    assert len(attempts) == 2
    assert "not acceptable" in attempts[1].messages[-1].content
    assert report.sources_examined == 1


@respx.mock
async def test_the_ladder_stops_when_the_ledger_refuses_another_attempt(
    tmp_path: Path, scripted_report: Callable[..., str]
) -> None:
    """A retry is a model call, and no retry path may go around `claim`.

    The ladder wanting another attempt is exactly the pressure that would justify a special
    case, so this pins the opposite: budget exhaustion stops it, as ordinary control flow,
    and the count says how far it actually got. Six calls is one plan, three research turns,
    one appraisal and precisely one report.
    """
    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "agent.sqlite3",
        search_fixture_dir=tmp_path / "search",
        max_model_calls=6,
    )
    recorded_search(settings)
    serve_docs_page()
    script = researched(*[scripted_report(resources=[resource(UNGROUNDED_URL)])] * 2)

    with pytest.raises(PreparationFailed) as raised:
        await drive(script, settings)

    assert raised.value.attempts == 1
    assert raised.value.issues, "the errors from the one attempt made must survive"


@respx.mock
async def test_the_final_attempt_goes_to_the_configured_alternate(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """Both halves of the provider rule, which is one decision with two outcomes.

    A model that has failed twice on the same evidence will fail a third time, so the last
    attempt changes model when a second one is genuinely configured. "Genuinely" is the load
    -bearing word: `HostedProvider` constructs happily without a key and only fails when
    called, so an alternate assumed rather than checked would spend the final attempt
    discovering there was never one.
    """
    recorded_search(offline_settings)
    serve_docs_page()
    primary = FakeProvider(
        researched(*[scripted_report(resources=[resource(UNGROUNDED_URL)])] * 2)
    )
    alternate = FakeProvider(
        [scripted_report(resources=[resource(DOCS_URL)])], model="alternate-model"
    )

    report = await run_agent(
        TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25),
        registry=build_tool_registry(offline_settings),
        providers=AgentProviders(primary, primary, primary, fallback=alternate),
        ctx=RunContext(budget=RunBudget.from_settings(offline_settings)),
        settings=offline_settings,
    )

    assert report.model_used == "alternate-model"
    assert primary.remaining == 0 and alternate.remaining == 0

    # Both keys passed explicitly, never inherited from the developer's environment: whether
    # an alternate exists is the switch this test turns, so it cannot be ambient.
    keyless = Settings(_env_file=None, google_api_key=None)
    keyed = Settings(_env_file=None, google_api_key="a-key")
    assert AgentProviders.from_settings(keyless).fallback is None, (
        "no GOOGLE_API_KEY means no second model, and the ladder must stay on the primary "
        "rather than spend its last attempt on a provider that raises when called"
    )
    assert AgentProviders.from_settings(keyed).fallback is not None


@respx.mock
async def test_a_retry_rewrites_the_report_without_researching_again(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """A validation failure means the report broke our contract, not that the run was wrong.

    The cheapest possible guard against the most expensive mistake available here: a ladder
    that re-entered the loop would spend SerpAPI quota — 250 calls a month, for the whole
    project — correcting a paragraph. The evidence is fixed the moment finalisation starts.

    Filtered to the two tools that reach the outside world rather than asserted over the whole
    call list, because Day 4 T4 added local memory writes to a finished run. What must not
    happen is a *search or a fetch* during finalisation; a SQLite write costs no quota, no
    network and no new evidence, and folding both concerns into one list equality would make
    this guard fail for reasons that have nothing to do with research.
    """
    recorded_search(offline_settings)
    serve_docs_page()
    registry, seen = recording_registry(offline_settings)
    script = researched(
        scripted_report(resources=[resource(UNGROUNDED_URL)]),
        scripted_report(resources=[resource(DOCS_URL)]),
    )

    _, _, ctx = await drive(script, offline_settings, registry=registry)

    assert [name for name in seen if name in {"web_search", "fetch_url"}] == [
        "web_search",
        "fetch_url",
    ]
    assert ctx.budget.remaining("search") == offline_settings.max_search_calls - 1
    assert ctx.budget.remaining("fetch") == offline_settings.max_fetch_calls - 1


async def test_a_later_drift_does_not_report_the_earlier_verdict(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """The last attempt's failure is the one reported, not the most recent one that had a
    verdict.

    A rejection followed by two drifts used to end with the *first* attempt's issues
    attached to the exception, which sends whoever reads it hunting for a citation problem
    in replies that never parsed. Both records are cleared on a drift for that reason, and
    `RunState.validation_errors` is cleared with them — S11 is going to surface that field,
    and a stale one is worse than an empty one.

    Driven through `finalise` rather than `run_agent` so the state is observable: the field
    reaches no caller yet, which is exactly why nothing else would catch it going stale.
    """
    state = RunState(
        task=TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25)
    )
    provider = FakeProvider(
        [
            # Rejected: no research was performed, so nothing may be cited.
            scripted_report(resources=[resource(UNGROUNDED_URL)]),
            json.dumps({"drifted": True}),
            json.dumps({"drifted": "again"}),
        ]
    )

    with pytest.raises(PreparationFailed) as raised:
        await finalise(
            state,
            provider=provider,
            ctx=RunContext(budget=RunBudget.from_settings(offline_settings)),
            stop_reason="planner_finalised",
            settings=offline_settings,
        )

    assert raised.value.issues == ()
    assert state.validation_errors == []
    assert "did not fit" in str(raised.value), (
        "the message must name the failure that actually ended the ladder"
    )


# --- memory: written by the loop, never consulted by it (Day 4 T4) --------------------------
#
# `test_memory.py` proves the storage and `test_memory_tools.py` proves the guards. What only
# exists once the loop is wired to them is *when* a write happens: after validation and never
# before it, once per finished hop, and never at the cost of the run.


@respx.mock
async def test_a_validated_run_remembers_itself(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """A run that produced a valid report leaves both kinds of memory behind.

    Two claims in one run because they are one wiring: the finished preparation is findable
    under a reworded title, and the hop that produced it left its own history. Catches the
    wire being absent altogether — every unit below it can pass while `run_agent` never calls
    either tool, and the only symptom would be a second session that starts from scratch.
    """
    recorded_search(offline_settings)
    script = [
        plan("RESEARCH", "what does a B-tree index do"),
        tool_turn(("web_search", {"query": QUERY, "source_type": "docs"})),
        "notes",
        verdict(True),
        scripted_report(),
    ]

    report, _, ctx = await drive(script, offline_settings)

    with db.open_database(offline_settings.db_path) as connection:
        previous = prep_memory.recall_previous_preparation(
            connection, task_title="Continue postgresql indexing"
        )
        remembered = run_memory.get_run_memory(connection, ctx.run_id)
        queries = run_memory.seen_queries(connection, ctx.run_id)

    assert previous is not None
    assert previous.run_id == report.run_id
    assert previous.topics_covered == report.topics_to_cover
    assert previous.topics_deferred == report.topics_to_skip

    assert {row.kind for row in remembered} == {
        "goal",
        "decision",
        "finding",
        "appraisal",
        "seen_query",
        "seen_url",
    }
    assert {row.hop for row in remembered} == {1}
    assert queries == {QUERY}, "the hop's own query must be recoverable from disk"


@respx.mock
async def test_a_run_that_never_produced_a_valid_report_remembers_nothing(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """Three ungrounded attempts, a `PreparationFailed`, and an empty `prep_memory`.

    The rule that keeps cross-run memory worth trusting: a preparation nobody could validate
    must never come back tomorrow as "what we did last time". It holds because the save is
    reached only through the value `finalise` returns — this is what would catch someone
    moving that call somewhere a failure also passes through, such as a `finally`.

    The hop still leaves its `run_memory` rows, and should: the run genuinely happened, and
    its history is the only remaining evidence of why it failed.
    """
    recorded_search(offline_settings)
    serve_docs_page()
    script = researched(*[scripted_report(resources=[resource(UNGROUNDED_URL)])] * 3)

    with pytest.raises(PreparationFailed):
        await drive(script, offline_settings)

    with db.open_database(offline_settings.db_path) as connection:
        saved = connection.execute("SELECT COUNT(*) AS n FROM prep_memory").fetchone()
        hops = connection.execute("SELECT COUNT(*) AS n FROM run_memory").fetchone()

    assert saved["n"] == 0, "an invalid preparation must never be remembered"
    assert hops["n"] > 0, "the hop that ran is still part of the run's own history"


@respx.mock
async def test_a_broken_memory_layer_does_not_cost_the_run_its_report(
    offline_settings: Settings, scripted_report: Callable[..., str]
) -> None:
    """With every memory write failing, the run still returns its validated report.

    The guarantee that makes memory safe to add at all, asserted at the level where it
    matters — the run, not the tool. A closed connection makes both the per-hop mirror and the
    final save fail for real, and neither may raise, retry, or change what the run produces.
    Losing nine to fifteen minutes of research because a diagnostic could not be written would
    be a far worse failure than having no memory.
    """
    connection = db.connect(offline_settings.db_path)
    db.initialize_schema(connection)
    connection.close()
    registry = build_tool_registry(offline_settings, connection=connection)
    script = [plan("RESEARCH", "q"), "notes", verdict(True), scripted_report()]

    report, _, _ = await drive(script, offline_settings, registry=registry)

    assert report.hops_used == 1
    assert report.interpreted_goal


# --- memory: read once, and only as guidance (Day 4 T5) --------------------------------------
#
# `test_prompt_context.py` proves what the two continuation blocks say and that a previous run's
# URLs never appear in one. What only exists once the loop is wired to them is that a recalled
# preparation *reaches* the two stages that can act on it — and, just as load-bearing, that a run
# with nothing to recall is the run this project shipped before T5.
#
# The script is `FINALISE` on the first decision throughout: what these tests are about is the
# planning prompt and the report prompt, and a hop would add two model calls and a search without
# adding a claim.


def remember_a_previous_session(
    settings: Settings, payload: dict[str, Any], **overrides: Any
) -> None:
    """One validated preparation already on disk, as an earlier run would have left it.

    Written through `prep_memory` rather than by hand: the row a recall has to find is
    whatever `save_preparation` actually writes, and a hand-built INSERT would keep passing
    after the two drifted apart.
    """
    report = FocusPreparationReport.model_validate({**payload, **overrides})
    with db.open_database(settings.db_path) as connection:
        prep_memory.save_preparation(connection, report=report)


def messages_at(provider: FakeProvider, schema_name: str) -> list[str]:
    """Every message of the first call to one stage — the whole turn, not just the prompt.

    `prompts_for` reads `messages[0]`, which is all a planner ever gets. The report stage is
    the one that also receives extra messages (the stop reason, and now the continuation), so
    a test about what it was told has to look past the first one.
    """
    call = next(call for call in provider.calls if call.schema_name == schema_name)
    return [message.content for message in call.messages]


@respx.mock
async def test_a_recalled_preparation_reaches_the_planner_and_the_report(
    offline_settings: Settings,
    valid_report_payload: dict[str, Any],
    scripted_report: Callable[..., str],
) -> None:
    """A second session for a remembered task is told what the first one did.

    The wire T4 left unbuilt, at the only level that can see it: `recall_previous_preparation`
    was registered, tested and never called, so every unit beneath this passed while a second
    session still started from scratch. Both stages are asserted because they act on different
    halves — the planner must not spend a hop re-researching settled material, and the report
    is where `interpreted_goal` and `topics_to_cover` are actually written.

    What is pinned is the *memory's own content* arriving, never the sentence around it:
    wording is the part of a prompt S3 keeps free to change.
    """
    remember_a_previous_session(offline_settings, valid_report_payload)
    script = [plan("FINALISE"), scripted_report()]

    report, provider, _ = await drive(script, offline_settings)

    planner = prompts_for(provider, "SupervisorDecision")[0]
    assert "Understand B-tree indexes" in planner, "the previous goal must reach the planner"
    assert "B-tree basics" in planner, "so a hop is not spent on settled material"
    assert "GIN" in planner, "the deferred topics are where a continuation usually starts"

    finalising = "\n".join(messages_at(provider, "FocusPreparationReport"))
    assert "Understand B-tree indexes" in finalising
    assert "B-tree basics" in finalising
    assert "GIN" in finalising
    assert report.interpreted_goal, "and the run still produces its report"


@respx.mock
async def test_a_task_with_nothing_remembered_is_prepared_exactly_as_before(
    offline_settings: Settings,
    valid_report_payload: dict[str, Any],
    scripted_report: Callable[..., str],
) -> None:
    """The same script with an empty `prep_memory`: no continuation anywhere.

    The regression with the widest blast radius in T5 — memory is an *enhancement*, and a
    first session for a task is still the overwhelming majority of runs. The report stage is
    checked by message count rather than by content, because that is the assertion a stray
    empty block would fail: with nothing recalled and nothing exhausted, `finalise` sends the
    prompt and nothing else.
    """
    script = [plan("FINALISE"), scripted_report()]

    report, provider, _ = await drive(script, offline_settings)

    # The same strings the test above finds, read off the preparation this run did *not* have.
    never_recalled = valid_report_payload["topics_to_cover"]
    planner = prompts_for(provider, "SupervisorDecision")[0]
    assert not any(topic in planner for topic in never_recalled), (
        "nothing was remembered, so nothing may be claimed"
    )
    assert len(messages_at(provider, "FocusPreparationReport")) == 1, (
        "a fresh run's report turn is the prompt alone"
    )
    assert report.interpreted_goal


@respx.mock
async def test_a_failed_recall_leaves_the_run_working_as_a_fresh_task(
    offline_settings: Settings,
    valid_report_payload: dict[str, Any],
    scripted_report: Callable[..., str],
) -> None:
    """A preparation exists, the database is broken, and the run finishes anyway.

    Distinct from the broken-*write* case above: this failure happens before the loop starts,
    on the one memory call whose answer a decision depends on. The guarantee is that it has no
    special status — a recall that cannot be served is worth exactly as much as a task nobody
    has prepared, and both leave the run to proceed normally. Catches anyone later making the
    recall raise, retry, or gate the run on its result.
    """
    remember_a_previous_session(offline_settings, valid_report_payload)
    connection = db.connect(offline_settings.db_path)
    db.initialize_schema(connection)
    connection.close()
    registry = build_tool_registry(offline_settings, connection=connection)
    script = [plan("FINALISE"), scripted_report()]

    report, provider, _ = await drive(script, offline_settings, registry=registry)

    assert report.interpreted_goal, "a memory outage must never cost a run its report"
    assert "B-tree basics" not in prompts_for(provider, "SupervisorDecision")[0]
    assert len(messages_at(provider, "FocusPreparationReport")) == 1
