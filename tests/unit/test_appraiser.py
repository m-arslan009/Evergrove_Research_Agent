"""The Appraiser as an evidence judge, not a sufficiency flag (Day 5 T4).

Everything else that touches this stage drives it through a loop and reads one field:
`test_single_agent.py` asserts that `sufficient` and `requested_followup` steer the run, and
`test_multi_agent.py` asserts that the Supervisor is the only route to it. Neither says
anything about the judgement itself — a stage that answered `sufficient` correctly and
nothing else would pass both suites.

So this file drives `judge_sufficiency` directly, with a scripted `FakeProvider`, and asks
the questions that are only about the verdict:

* does an authoritative source come back accepted, with a reading of what it establishes
  **and** of what it leaves open;
* is a source rejected with a stated reason rather than silently dropped;
* does a genuine contradiction survive, and does an absent one stay absent;
* does an insufficient verdict name what is missing and ask something specific.

Each case is one scripted reply, because what is under test is the code path from a model's
JSON to a typed verdict the loop can act on — a real model's judgement quality is a live
question and belongs to a live run, not to an offline suite that would only be asserting its
own script.

The last section is the structural half: this module reaches no tool, which is a property of
what it can *import*, not of what one scripted run happened to emit.

Offline and model-free: `FakeProvider` injected, no registry, no network, no database.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evergrove_agent.agents.appraiser import judge_sufficiency
from evergrove_agent.config import Settings
from evergrove_agent.llm import FakeProvider
from evergrove_agent.schemas import AppraisalRequest, AppraisalVerdict, GatheredSource
from evergrove_agent.tools.base import RunBudget, RunContext

QUESTION = "Which PostgreSQL index type suits equality lookups?"

DOCS_URL = "https://www.postgresql.org/docs/current/indexes-types.html"
BLOG_URL = "https://example.com/indexing-explained"


def official_page() -> GatheredSource:
    """A page the run actually opened, from a documentation domain."""
    return GatheredSource(
        url=DOCS_URL,
        title="PostgreSQL: Index Types",
        domain_class="official",
        snippet="The index types PostgreSQL provides",
        excerpt=(
            "B-tree indexes can handle equality and range queries on data that can be "
            "sorted. The PostgreSQL query planner will consider using a B-tree index "
            "whenever an indexed column is involved in a comparison."
        ),
        retrieved_at=datetime(2026, 8, 18, 9, 0, tzinfo=UTC),
    )


def unread_blog() -> GatheredSource:
    """A search hit nobody opened — a lead, whose snippet is a claim rather than evidence."""
    return GatheredSource(
        url=BLOG_URL,
        title="Indexing explained",
        domain_class="secondary",
        snippet="Everything you need to know about indexes",
    )


def request(*sources: GatheredSource) -> AppraisalRequest:
    return AppraisalRequest(
        research_question=QUESTION, session_minutes=25, sources=list(sources)
    )


def scripted(**verdict: Any) -> FakeProvider:
    """One `AppraisalVerdict` reply, exactly as a model would return it: JSON text.

    Written as JSON rather than as a `model_dump_json` of a constructed verdict, so the test
    exercises the same parse the run does. A verdict built in Python and dumped would prove
    that Pydantic round-trips, which is not the thing that breaks.
    """
    return FakeProvider([json.dumps({"reasoning": "judged", **verdict})])


async def judge(provider: FakeProvider, appraisal: AppraisalRequest) -> AppraisalVerdict:
    """The stage, run for real, with the ledger it would have inside a run."""
    settings = Settings(_env_file=None)
    ctx = RunContext(budget=RunBudget.from_settings(settings))
    verdict = await judge_sufficiency(
        appraisal, provider=provider, ctx=ctx, settings=settings
    )
    assert verdict is not None, "the stage returned no verdict at all"
    return verdict


# --- what an accepted source is worth -------------------------------------------------------


async def test_an_authoritative_source_is_accepted_with_what_it_does_and_does_not_show() -> (
    None
):
    """Acceptance is a reading of the source, and the two halves are not the same claim.

    `supports` is what the report may rest on. `does_not_support` is the relevant gap, which
    is `unknowns` material — and it is the half that stops a report presenting one page as
    having settled the whole question. A verdict that carried only the first would let the
    deliverable overclaim from evidence the Appraiser knew was partial.

    The authority travels with it, in the report's own vocabulary, so that the judgement and
    the citation classify a page the same way.
    """
    provider = scripted(
        sufficient=True,
        accepted=[
            {
                "source": "PostgreSQL: Index Types",
                "supports": "B-tree handles equality and range comparisons",
                "does_not_support": "how a partial index changes the plan",
                "authority": "official",
            }
        ],
    )

    verdict = await judge(provider, request(official_page()))

    accepted = verdict.accepted[0]
    assert accepted.source == "PostgreSQL: Index Types"
    assert accepted.supports == "B-tree handles equality and range comparisons"
    assert accepted.does_not_support == "how a partial index changes the plan"
    assert accepted.authority == "official"
    assert verdict.rejected == []


async def test_the_evidence_the_appraiser_judges_is_the_full_excerpt() -> None:
    """The judgement is only as good as what the stage was shown.

    This is the one stage handed `render_sources` — full excerpts at `SOURCE_EXCERPT_CHARS`
    — rather than `finalise`'s compact block, and the reason is structural: everything after
    it reads a summary, so if the excerpt does not reach *here* nothing downstream ever sees
    the source text at all. A refactor that quietly handed the Appraiser snippets would still
    produce plausible verdicts, which is exactly why it needs its own assertion.

    The read/unread wording is asserted with it because it is what the whole "a lead is not
    evidence" rule keys on, in this prompt and in `finalise.md` alike.
    """
    provider = scripted(sufficient=True)

    await judge(provider, request(official_page(), unread_blog()))

    prompt = provider.calls[0].messages[0].content
    assert "B-tree indexes can handle equality and range queries" in prompt
    assert "status: read" in prompt
    assert "status: found but not opened" in prompt
    assert QUESTION in prompt, "the judgement is against the question, not the sources alone"


# --- what a rejection has to say ------------------------------------------------------------


async def test_a_source_is_rejected_with_a_concrete_reason() -> None:
    """A rejection without a reason is a verdict nobody downstream can act on.

    Two things depend on the reason being carried rather than inferred: the report has to be
    able to put it in `unknowns` when it matters, and a human reading the trace back has to
    be able to check the claim against the page. "This one does not help" supports neither,
    and would make an unjustified rejection indistinguishable from a justified one.

    The low-authority lead is the acceptance case the plan names for T4 by hand — the
    unopened blog next to the official page — so this asserts the shape of exactly that
    judgement.
    """
    provider = scripted(
        sufficient=True,
        accepted=[{"source": "PostgreSQL: Index Types", "authority": "official"}],
        rejected=[
            {
                "source": "Indexing explained",
                "reason": "a blog that was never opened; its snippet states no version",
            }
        ],
    )

    verdict = await judge(provider, request(official_page(), unread_blog()))

    rejected = verdict.rejected[0]
    assert rejected.source == "Indexing explained"
    assert "never opened" in rejected.reason
    assert [entry.source for entry in verdict.accepted] == ["PostgreSQL: Index Types"], (
        "a rejected source must not also be accepted"
    )


# --- disagreements: real ones kept, absent ones not invented --------------------------------


async def test_a_genuine_disagreement_survives_the_judgement() -> None:
    """A contradiction the Appraiser saw is exactly what the report owes its reader.

    It is also the one thing in the verdict that no later stage can reconstruct: `finalise`
    is handed snippets and notes, not the two excerpts that disagreed. Dropped here, the
    report quietly picks a side and presents it as settled.
    """
    provider = scripted(
        sufficient=False,
        requested_followup="Which index type does PostgreSQL 16 default to?",
        disagreements=["the two pages give different defaults for the index type"],
    )

    verdict = await judge(provider, request(official_page(), unread_blog()))

    assert verdict.disagreements == [
        "the two pages give different defaults for the index type"
    ]


async def test_sources_that_simply_agree_produce_no_disagreements() -> None:
    """An empty list is a correct answer, and far more often than not.

    The failure this guards is the opposite of the usual one: a field that must be filled
    invites an invented contradiction, and a manufactured disagreement travels into the
    report as a warning to the user about a conflict that does not exist. Nothing in the
    schema requires the field, `sufficiency.md` says so in words, and this pins that a run
    whose sources agree carries the empty list all the way through.
    """
    provider = scripted(sufficient=True, accepted=[{"source": "PostgreSQL: Index Types"}])

    verdict = await judge(provider, request(official_page()))

    assert verdict.disagreements == []


# --- insufficiency: named, and specific ------------------------------------------------------


async def test_thin_evidence_names_what_is_missing_and_asks_something_specific() -> None:
    """The two fields that keep the multi-hop loop honest.

    `missing_information` is what the report has to admit in `unknowns`; `requested_followup`
    is where hop 2's question comes from, and it is the field that makes the second hop
    agentic rather than a scripted retry — the question exists only because a hop ran and
    produced content that did not exist before it. `_stop_after_hop` reads it directly: an
    insufficient verdict with no follow-up stops the run, so the difference between these two
    replies is the difference between a second hop and a finalised report.
    """
    provider = scripted(
        sufficient=False,
        missing_information=["no syntax for creating a partial index"],
        requested_followup="What is the CREATE INDEX syntax for a partial index?",
    )

    verdict = await judge(provider, request(official_page()))

    assert verdict.sufficient is False
    assert verdict.missing_information == ["no syntax for creating a partial index"]
    assert verdict.requested_followup == (
        "What is the CREATE INDEX syntax for a partial index?"
    )
    assert QUESTION not in (verdict.requested_followup or ""), (
        "a follow-up that repeats the question buys the run nothing"
    )


async def test_a_reply_that_never_fits_the_schema_returns_no_verdict() -> None:
    """The honest degradation, and the reason the wider T4 schema had to stay lenient.

    `_decode` re-asks once and then gives up, and `None` means "this stage has no answer" —
    which the Supervisor answers by finalising with the evidence it has rather than by
    assuming a verdict nobody gave. Asserting it here is what makes the coercion tests in
    `test_agent_schemas.py` matter: without them, a model answering T2's shape would land on
    exactly this path and cost the run its hop.
    """
    settings = Settings(_env_file=None)
    ctx = RunContext(budget=RunBudget.from_settings(settings))
    provider = FakeProvider(["not json at all", '{"sufficient": "yes please"}'])

    verdict = await judge_sufficiency(
        request(official_page()), provider=provider, ctx=ctx, settings=settings
    )

    assert verdict is None
    assert provider.remaining == 0, "it re-asked once, and stopped"


# --- the Appraiser performs no research ------------------------------------------------------

APPRAISER = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "evergrove_agent"
    / "agents"
    / "appraiser.py"
)


@pytest.mark.parametrize(
    "forbidden",
    [
        "evergrove_agent.agents.tool_calling",
        "evergrove_agent.tools.registry",
        "evergrove_agent.tools.fetch_url",
        "evergrove_agent.tools.web_search",
        "evergrove_agent.search",
    ],
)
def test_the_appraiser_cannot_reach_a_tool(forbidden: str) -> None:
    """T4 gave the Appraiser a much larger job, and this is the boundary that job stays in.

    A judge asked to say what a source does *not* establish is a step away from a judge that
    would like to go and find out — and the moment it can search, it is grading evidence it
    chose itself. The rule is enforced by what the module can import rather than by what one
    scripted run emitted, so it survives an edit that a behavioural test would not notice.

    `test_multi_agent.py` pins the first two as part of the role split; the search and fetch
    tools are named here because T4 is the task that made the temptation real.
    """
    tree = ast.parse(APPRAISER.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert forbidden not in imported
