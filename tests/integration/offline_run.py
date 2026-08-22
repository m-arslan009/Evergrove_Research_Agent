"""Driving one offline multi-agent run, from whichever surface a test wants to start it.

Extracted from `test_multi_agent.py` when the MCP suite needed the same scripted run: the
requirement "an MCP-triggered run traces exactly like a CLI-triggered one" is only checkable
if both surfaces are handed *the same* run, and a second copy of the script would have made
that claim untestable at the moment it started to drift.

A plain module rather than `conftest.py`, deliberately. Conftest shares **fixtures**
automatically and module-level functions not at all, and `import conftest` is ambiguous once
two conftest files exist — so the helpers live here and the two fixtures that must be fixtures
(`workspace`, `ledger`, `report`) live in `tests/integration/conftest.py`. pytest's prepend
import mode puts this directory on `sys.path` for every module it collects here, and this file
is not named `test_*`, so it is imported and never collected.

Offline and model-free throughout, on the same terms as `test_single_loop.py`: `FakeProvider`
injected rather than patched, the **committed** `fixtures/search/` tree at the shipped default,
`respx` active so any unrouted request fails the test, and only `DB_PATH` moved into `tmp_path`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from evergrove_agent import service
from evergrove_agent.agents import AgentProviders
from evergrove_agent.config import AgentMode, Settings
from evergrove_agent.llm import FakeProvider
from evergrove_agent.schemas import FocusPreparationReport, ResearchAction, TaskContext
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


class Roles:
    """One `FakeProvider` per reasoning role, plus what each was asked for.

    `RecordedCall` already carries `schema_name`, so nothing here inspects a prompt: the schema
    a role was asked to fill *is* its job.

    `RecordedCall.tool_names` is deliberately **not** used. Since the S14 contingency swap the
    researcher's menu travels inside the rendered prompt rather than as `tools=`, so that field
    is empty for every role — an assertion on it would pass for all three and prove nothing.
    What a role may reach is asserted statically instead, in `test_multi_agent.py` section 4.
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


# --- starting the run ---------------------------------------------------------------------------


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


def script_the_models(monkeypatch: pytest.MonkeyPatch, roles: Roles) -> Roles:
    """Hand `roles` to a run that was started by a **surface** rather than by a test.

    `drive` passes `providers=` straight in, which a surface cannot do: the MCP tool gives the
    service only `settings`, because a surface has no business assembling a run. That leaves
    `AgentProviders.from_settings` as the one seam — the same one
    `scripts/mcp_offline_server.py` uses to make the shipped demo run in seconds — and
    substituting it keeps the *whole real path* under test: the service still builds the
    registry, still opens the database, still traces, and still saves the preparation.

    Returns `roles` so a call site can script and bind in one line and still reach the
    providers afterwards to assert what each was asked for.
    """
    monkeypatch.setattr(
        service.AgentProviders,
        "from_settings",
        classmethod(lambda cls, settings=None: roles.providers),
    )
    return roles


# --- reading the trace back ----------------------------------------------------------------------


SUPERVISED_SPAN_TREE: tuple[list[str], dict[str, list[str]]] = (
    ["supervisor.run"],
    {
        "supervisor.run": [
            "recall_previous_preparation",
            "supervisor.decide",
            "researcher.loop",
            "appraiser.judge",
            "record_run_memory",
            "supervisor.finalise",
            "save_preparation",
        ],
        "recall_previous_preparation": [],
        "supervisor.decide": [],
        "researcher.loop": ["web_search", "fetch_url"],
        "web_search": [],
        "fetch_url": [],
        "appraiser.judge": [],
        "record_run_memory": [],
        "supervisor.finalise": [],
        "save_preparation": [],
    },
)
"""What `one_hop` records in `"multi"` mode, as `span_tree` returns it.

**One definition, asserted from two surfaces.** `test_multi_agent.py` checks it for a run
started through `service.prepare_focus_session` and `test_mcp_server.py` checks it for the same
run started through the MCP tool; equal trees are then structural rather than two literals that
happen to agree today. A second copy of this list is precisely how "tracing works the same
whichever surface started the run" would stop being true without a test noticing.

Every claim the shape makes is load-bearing: one root, so the Supervisor holds the whole run;
both workers underneath it and neither inside the other; every tool the Researcher reached
nested under the Researcher; and an empty child list for `appraiser.judge`, because the
Appraiser judging its own fresh evidence is the failure the role split exists to prevent.
"""


def span_tree(
    ledger: sqlite3.Connection, run_id: str
) -> tuple[list[str], dict[str, list[str]]]:
    """One run's spans as `(root names, {span name: child names})`, in start order.

    Names rather than ids, because an id proves the shape to a test and nothing to a reader:
    `researcher.loop → web_search` is the assertion this is actually about. Uniqueness is
    asserted rather than assumed, so a run that opened two spans with one name cannot silently
    collapse into a tree that looks right.
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
