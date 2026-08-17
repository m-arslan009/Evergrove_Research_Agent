"""Day 3 acceptance: the whole run, started the way a surface starts it (S13).

Every piece of the loop is already proven on its own — the stages in
`tests/unit/test_single_agent.py`, the grounding rule in `test_validate_report.py`, the
renderers, the bridge and the ledger in their own suites. **Nothing here re-proves any of
that.** What only exists once the pieces are joined, and what this file protects, is the
composition: `service.prepare_focus_session` → the wired registry → the *committed*
`fixtures/search/` recordings → one SQLite file → `run_agent` → `finalise` →
`validate_report`.

Four things are true of every test below, and none of them can be asserted by a unit suite:

1. the run starts at `prepare_focus_session`, the same entry point the CLI and the Day 6 MCP
   server use, and the registry is the one *it* builds;
2. search answers out of the committed fixture tree at the shipped `SEARCH_FIXTURE_DIR`
   default — a fresh clone's configuration, not a recording written for the test;
3. the real database is written and the live-search counter is read back, because an
   "offline" run that quietly moved the SerpAPI quota would otherwise still look like a pass;
4. `respx` is active with only the pages a run may legitimately read routed, so any other
   request fails the test rather than leaving the machine.

Offline and model-free throughout: `FakeProvider` is injected rather than patched, and only
`DB_PATH` is moved into `tmp_path`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
import pytest
import respx

from evergrove_agent.agents import AgentProviders
from evergrove_agent.agents.prompt_context import render_stop_reason
from evergrove_agent.agents.single_agent import PreparationFailed
from evergrove_agent.config import Settings
from evergrove_agent.llm import FakeProvider
from evergrove_agent.memory import db, get_search_usage
from evergrove_agent.schemas import FocusPreparationReport, ResearchAction, TaskContext
from evergrove_agent.service import prepare_focus_session
from evergrove_agent.tools.base import RunBudget, RunContext

TASK = TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25)

# --- the committed recordings, as constants ------------------------------------------------
#
# Named here rather than inlined so that editing a fixture breaks a test instead of silently
# changing what these runs prove.

DOCS_QUERY = "postgresql b-tree index"
"""`fixtures/search/postgresql-indexing.json`, `source_type=docs`."""

INDEXES_URL = "https://www.postgresql.org/docs/current/indexes-types.html"
"""That recording's top-ranked hit — the one page these runs actually open."""

USING_EXPLAIN_URL = "https://www.postgresql.org/docs/current/using-explain.html"
"""Also in that recording, and never opened by any run here: the difference between a source
a run *found* and a source it *read*."""

EXPLAIN_QUERY = "how to read a postgresql explain plan"
"""`fixtures/search/explain-plans.json`, `source_type=general` — the second hop's own
recording, so a follow-up question resolves against different evidence."""

EXPLAIN_DOC_URL = "https://www.postgresql.org/docs/current/sql-explain.html"

ACADEMIC_QUERY = "learned index structures"
"""`fixtures/search/learned-indexes.json`, `source_type=academic` — a third source type."""

UNRECORDED_QUERY = "a question nobody has ever recorded an answer for"
"""No fixture answers this, at any source type, which is what drives the whole search failure
ladder down to `SEARCH_UNAVAILABLE`."""

UNSEEN_URL = "https://example.com/an-invented-guide"
"""A page no run in this file searches for, opens, or could have heard of."""

ARTICLE = Path(__file__).resolve().parents[2] / "fixtures" / "html" / "article.html"
"""The committed page served in place of `INDEXES_URL`. It genuinely discusses
`EXPLAIN ANALYZE`, which is what lets a second hop be *derived* from what the first one read
rather than merely scripted after it."""


# --- the workspace --------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Settings:
    """Committed fixtures for input, a temporary file for state.

    Only `DB_PATH` moves. `SEARCH_BACKEND` and `SEARCH_FIXTURE_DIR` stay exactly as a fresh
    clone has them, because "the shipped defaults run this loop offline" is part of what is
    being accepted here.
    """
    return Settings(_env_file=None, db_path=tmp_path / "agent.sqlite3")


@pytest.fixture
def ledger(workspace: Settings) -> Iterator[sqlite3.Connection]:
    """A second handle on the run's database, for reading the live-search counter back."""
    with db.open_database(workspace.db_path) as connection:
        yield connection


def serve_indexes_page() -> respx.Route:
    """`INDEXES_URL` answered locally, so a run can genuinely read something to cite."""
    return respx.get(INDEXES_URL).mock(
        return_value=httpx.Response(200, html=ARTICLE.read_text(encoding="utf-8"))
    )


# --- scripting a model ------------------------------------------------------------------------


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


def resource(url: str, *, authority: str = "official") -> dict[str, str]:
    return {
        "title": "PostgreSQL: Documentation: Index Types",
        "url": url,
        "why_this_source": "Official documentation for the exact version in use",
        "authority": authority,
    }


@pytest.fixture
def report(valid_report_payload: dict[str, Any]):
    """A report a model could plausibly return, citing nothing unless a test says otherwise.

    The shared payload cites a real PostgreSQL page, which is right for the grounding suite
    and wrong as a default here: several of these runs never open that page, and since S10
    enforces grounding inside `finalise` the citation would fail them for a reason the test is
    not about. Citations are opted into, by the tests that are about citations.
    """

    def _report(**overrides: Any) -> str:
        return json.dumps({**valid_report_payload, "resources": [], **overrides})

    return _report


# --- driving one run ---------------------------------------------------------------------------


async def drive(
    script: list[Any], settings: Settings
) -> tuple[FocusPreparationReport, FakeProvider, RunContext]:
    """One run through the real entry point, with one `FakeProvider` answering all three
    roles.

    `registry` is deliberately not passed: letting `prepare_focus_session` build it is the
    difference between testing the loop and testing the composition.
    """
    provider = FakeProvider(script)
    ctx = RunContext(budget=RunBudget.from_settings(settings))
    result = await prepare_focus_session(
        TASK,
        settings=settings,
        providers=AgentProviders(provider, provider, provider),
        ctx=ctx,
    )
    return result, provider, ctx


def researched(*reports: Any) -> list[Any]:
    """A run that searches, opens `INDEXES_URL`, is judged sufficient, then finalises.

    Every grounding test needs real evidence behind it — a citation check against an empty
    evidence set proves only that nothing can be cited. The finalise attempts are whatever the
    test passes in.
    """
    return [
        plan("RESEARCH", "what does a B-tree index do"),
        tool_turn(("web_search", {"query": DOCS_QUERY, "source_type": "docs"})),
        tool_turn(("fetch_url", {"url": INDEXES_URL})),
        "the page explains what a B-tree index does",
        verdict(True),
        *reports,
    ]


def prompts_for(provider: FakeProvider, schema_name: str) -> list[str]:
    """Every prompt sent to one constrained stage, in order — what the loop actually put in
    front of the model, rather than what it meant to."""
    return [
        call.messages[0].content
        for call in provider.calls
        if call.schema_name == schema_name
    ]


def finalise_turns(provider: FakeProvider) -> list[Any]:
    """Every attempt at the report, in order — the ladder as the model experienced it."""
    return [
        call for call in provider.calls if call.schema_name == "FocusPreparationReport"
    ]


# --- 1. the whole run, end to end ----------------------------------------------------------------


@respx.mock
async def test_a_full_offline_run_produces_a_valid_report_in_under_two_seconds(
    workspace: Settings, ledger: sqlite3.Connection, report
) -> None:
    """The Day 3 acceptance criterion that can be checked without a model: plan, search, read,
    judge, report — through the real entry point, offline, fast.

    Three failures at once, none of which any unit test can see. The composition can simply
    not fit: a service that builds a registry the loop cannot use, or a fixture tree that
    moved out from under the shipped defaults. The offline default can reach a metered
    backend, which costs real money and looks exactly like success — hence the counter read at
    the end and the single registered route. And the loop can become slow enough to stop being
    usable as a development gate, which is the whole reason `FakeProvider` exists.
    """
    page = serve_indexes_page()
    script = researched(report(resources=[resource(INDEXES_URL)]))

    started = perf_counter()
    result, provider, _ = await drive(script, workspace)
    elapsed = perf_counter() - started

    assert isinstance(result, FocusPreparationReport)
    assert result.hops_used == 1
    assert result.sources_examined == 1, "the page it opened, not the leads it merely found"
    assert [str(item.url) for item in result.resources] == [INDEXES_URL]
    assert result.original_task == "Learn PostgreSQL indexing"  # bookkeeping, not the model
    assert provider.remaining == 0

    assert page.call_count == 1
    assert get_search_usage(ledger) == 0, "the offline default must not move the live counter"
    assert elapsed < 2.0, f"the offline run took {elapsed:.2f}s; the budget is 2s"


# --- 2. the genuine second hop ---------------------------------------------------------------------


@respx.mock
async def test_an_insufficient_verdict_buys_exactly_one_more_hop_drawn_from_hop_one(
    workspace: Settings, report
) -> None:
    """The multi-hop claim, end to end: hop 2 asks something hop 1 *taught the run to ask*.

    This is the failure that hides best. A loop that performs a second hop against a question
    nothing discovered still shows `hops_used == 2` and still looks like an agent — it is a
    retry wearing an agent's clothes. So the chain is asserted link by link: the page hop 1
    opened reaches the appraiser (it is the only place `EXPLAIN ANALYZE` can come from), the
    appraiser's follow-up reaches the planner, hop 1's spent query reaches it too so hop 2
    cannot simply repeat it, and hop 2's answer comes back from a *different* committed
    recording. "Exactly one" matters as much as "one": three hops here would mean the
    sufficient verdict was ignored.
    """
    serve_indexes_page()
    script = [
        plan("RESEARCH", "what does a B-tree index do"),
        tool_turn(("web_search", {"query": DOCS_QUERY, "source_type": "docs"})),
        tool_turn(("fetch_url", {"url": INDEXES_URL})),
        "the page ends by checking the query plan",
        verdict(False, EXPLAIN_QUERY, ("how to read the plan the page mentions",)),
        plan("RESEARCH", EXPLAIN_QUERY, preference="general"),
        tool_turn(("web_search", {"query": EXPLAIN_QUERY, "source_type": "general"})),
        "the second hop found how to read a plan",
        verdict(True),
        report(resources=[resource(INDEXES_URL)]),
    ]

    result, provider, ctx = await drive(script, workspace)

    assert result.hops_used == 2

    plans = prompts_for(provider, "SupervisorDecision")
    appraisals = prompts_for(provider, "AppraisalVerdict")
    assert len(plans) == 2, "one planning turn per hop, and none after the run stopped"
    assert "EXPLAIN ANALYZE" in appraisals[0], (
        "the appraiser must be shown what hop 1 actually read, or its follow-up cannot have "
        "come from the evidence"
    )
    assert EXPLAIN_QUERY in plans[1]
    assert "what does a B-tree index do" in plans[1], (
        "hop 1's question must be listed as already spent, or hop 2 may repeat it"
    )
    assert EXPLAIN_DOC_URL in prompts_for(provider, "FocusPreparationReport")[0], (
        "hop 2 must have resolved against its own recording, not re-read hop 1's"
    )

    # The shipped budget affords this run and nothing more: two researched hops plus one
    # report is exactly `MAX_MODEL_CALLS`. Pinned because it is the offline half of what S14
    # has to measure — on a run like this the retry ladder has no room left at all.
    assert ctx.budget.remaining("model_call") == 0
    assert provider.remaining == 0


# --- 3. the cap ---------------------------------------------------------------------------------


@respx.mock
async def test_the_hop_cap_holds_when_the_planner_keeps_asking(
    workspace: Settings, tmp_path: Path, report
) -> None:
    """A model that answers RESEARCH forever must not research forever.

    `MAX_HOPS` is the only bound between a drifting model and an unbounded run, and it has to
    hold from the loop rather than from the model's cooperation. `max_model_calls` is raised
    on purpose: under the shipped 10 this run would stop because the *ledger* ran out, and the
    test could no longer tell which bound stopped it — a hop cap that never fires would pass.

    Three hops against three different recordings and three different source types, which is
    also the only place the routing rule ("`fixture` stays `fixture` for every source type")
    is exercised inside a real run rather than at the tool.
    """
    settings = Settings(
        _env_file=None, db_path=tmp_path / "agent.sqlite3", max_model_calls=16
    )
    script: list[Any] = []
    for question, query, preference in (
        ("what does a B-tree index do", DOCS_QUERY, "docs"),
        ("how do I read the plan", EXPLAIN_QUERY, "general"),
        ("what does the research say", ACADEMIC_QUERY, "academic"),
    ):
        script += [
            plan("RESEARCH", question, preference=preference),
            tool_turn(("web_search", {"query": query, "source_type": preference})),
            f"notes on {question}",
            verdict(False, "keep going"),
        ]
    script.append(report())

    result, provider, ctx = await drive(script, settings)

    assert result.hops_used == 3
    assert len(prompts_for(provider, "SupervisorDecision")) == 3, (
        "the planner was asked again after the last hop; the cap must skip it entirely"
    )
    assert any("hop limit" in unknown for unknown in result.unknowns), (
        "a run cut short by a limit must not read like one that finished"
    )
    assert ctx.budget.remaining("search") == 0
    assert provider.remaining == 0


# --- 4. a broken search, honestly reported ---------------------------------------------------------


@respx.mock
async def test_an_unavailable_search_still_reaches_a_report_that_admits_it(
    workspace: Settings, ledger: sqlite3.Connection, report
) -> None:
    """A hop that gathered nothing still has to finish, and still has to say so.

    The query is one nothing recorded, so the *whole* failure ladder runs for real — cache
    miss, the retryable rung, the widening to `general`, and finally `SEARCH_UNAVAILABLE`.
    (The unit suite empties the fixture directory instead, which cannot show the widening
    rung.) Two halves matter equally: the run must reach a report rather than crash, and the
    limitation must survive into both the finalise prompt and the report itself — a run whose
    search was broken and whose plan does not mention it is indistinguishable from one that
    simply found little, and nobody re-checks a plan that reads confidently.
    """
    script = [
        plan("RESEARCH", "what does a B-tree index do"),
        tool_turn(("web_search", {"query": UNRECORDED_QUERY, "source_type": "docs"})),
        "the search would not answer",
        verdict(False, None, ("anything at all",)),
        report(),
    ]

    result, provider, ctx = await drive(script, workspace)

    assert result.sources_examined == 0
    assert result.resources == []
    assert "SEARCH_UNAVAILABLE" in prompts_for(provider, "FocusPreparationReport")[0], (
        "the model has to be told the search failed, with the code, or it writes a confident "
        "report about evidence that never arrived"
    )
    # Asserted through the renderer rather than as a literal, because the *wording* of a stop
    # reason is safe to change and the *plumbing* is not.
    assert ctx.budget.exhausted_limits == (), "this run degraded on search, not on budget"
    assert render_stop_reason("no_followup") in result.unknowns

    assert get_search_usage(ledger) == 0, "a failed search must not spend quota either"


# --- 5. the retry ladder, on the shipped path -------------------------------------------------------


@respx.mock
async def test_a_rejected_report_is_asked_again_and_the_correction_is_returned(
    workspace: Settings, report
) -> None:
    """A report that breaks the grounding rule is refused and *fixed*, not simply refused.

    The correction has to name the offending URL, because a model told only "that was wrong"
    replaces one invented source with another. And the retry must correct the report rather
    than the research: the evidence is frozen when finalisation starts, so nothing may search
    or fetch again between attempts. That guard is cheap here and expensive in production — a
    ladder that re-entered the loop would spend SerpAPI quota, 250 calls a month for the whole
    project, rewriting a paragraph.
    """
    page = serve_indexes_page()
    script = researched(
        report(resources=[resource(UNSEEN_URL)]),
        report(resources=[resource(INDEXES_URL)]),
    )

    result, provider, ctx = await drive(script, workspace)

    attempts = finalise_turns(provider)
    assert len(attempts) == 2
    assert UNSEEN_URL in attempts[1].messages[-1].content
    assert [str(item.url) for item in result.resources] == [INDEXES_URL]

    assert page.call_count == 1
    assert ctx.budget.remaining("search") == workspace.max_search_calls - 1
    assert ctx.budget.remaining("fetch") == workspace.max_fetch_calls - 1


@respx.mock
async def test_three_invalid_reports_end_the_run_in_preparation_failed(
    workspace: Settings, report
) -> None:
    """The rule the ladder exists for: never return a report that failed the check.

    A plausible-looking plan nobody re-checks is the one outcome worse than no plan. The
    failure also has to arrive at the *surface* intact — `service.py` passing
    `PreparationFailed` through unwrapped is what keeps `issues` and `run_id` available to the
    CLI, and a well-meaning wrapper there would strand both at exactly the moment someone
    needs them.
    """
    serve_indexes_page()
    script = researched(*[report(resources=[resource(UNSEEN_URL)])] * 3)

    with pytest.raises(PreparationFailed) as raised:
        await drive(script, workspace)

    assert raised.value.attempts == 3, "MAX_OUTPUT_RETRIES counts attempts, not retries"
    assert raised.value.run_id is not None
    assert raised.value.issues, "what was wrong must survive the failure"


# --- 6. grounding, both tiers of it -------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    ("url", "authority", "code"),
    [
        (UNSEEN_URL, "official", "ungrounded_url"),
        (USING_EXPLAIN_URL, "official", "authority_overclaim"),
    ],
    ids=["never-seen", "found-but-never-opened"],
)
async def test_a_citation_outside_the_runs_evidence_is_refused(
    workspace: Settings, report, url: str, authority: str, code: str
) -> None:
    """The project's strongest anti-hallucination guard, enforced on the path a user runs.

    The allowed evidence set has two tiers and both are load-bearing. A URL the run never saw
    cannot be cited at all. A URL search *offered* but the run never *opened* may be
    mentioned, but not vouched for — "it appeared in a search result" is not the same
    permission as "I read it", and a model that conflates them produces a report whose
    citations look checked and are not.

    `test_validate_report.py` proves the rule against the validator; this proves it is
    actually reached when a real run finalises, which is the difference between a rule that
    exists and a rule that holds.
    """
    serve_indexes_page()
    script = researched(*[report(resources=[resource(url, authority=authority)])] * 3)

    with pytest.raises(PreparationFailed) as raised:
        await drive(script, workspace)

    assert [issue.code for issue in raised.value.issues] == [code]
