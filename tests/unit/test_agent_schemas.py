"""The contracts the four Day 3 reasoning stages exchange.

Two of these models are handed to a model for constrained decoding, and three later
subtasks read the third — so the rules tested here are the ones whose failure would show
up as a wasted search call, a hallucinated citation, or a retry loop, not as a type error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from evergrove_agent.llm.hosted_provider import to_gemini_schema
from evergrove_agent.schemas import (
    AppraisalRequest,
    AppraisalVerdict,
    GatheredSource,
    ResearchAssignment,
    ResearchFindings,
    RunState,
    SearchSourceType,
    SupervisorDecision,
    TaskContext,
)
from evergrove_agent.search import SearchSourceType as SearchLayerSourceType

MODEL_FACING = (SupervisorDecision, AppraisalVerdict)
"""The two models a provider decodes into. Everything else is assembled by our code."""


def _source(url: str, *, read: bool) -> GatheredSource:
    return GatheredSource(
        url=url,
        title=url,
        retrieved_at=datetime(2026, 8, 14, tzinfo=UTC) if read else None,
    )


def _finding(hop: int, queries: list[str], sources: list[GatheredSource]) -> ResearchFindings:
    return ResearchFindings(
        research_question=f"question {hop}",
        hop=hop,
        queries_used=queries,
        sources=sources,
    )


class TestSupervisorDecision:
    @pytest.mark.parametrize("question", [None, "", "   "])
    def test_research_without_a_question_is_rejected(self, question: str | None) -> None:
        """A hop with no question reaches `web_search` as a blank query: one search call
        spent for nothing, and real SerpAPI quota on a metered backend. Rejecting it here
        makes it a retry instead."""
        with pytest.raises(ValidationError, match="non-empty research_question"):
            SupervisorDecision(
                action="RESEARCH", research_question=question, reasoning="go look"
            )

    def test_finalise_needs_no_question(self) -> None:
        """Finalising straight away is legitimate — the report is then honest about
        having no sources. Requiring a question here would reject a valid decision."""
        decision = SupervisorDecision(action="FINALISE", reasoning="enough already")

        assert decision.research_question is None
        assert decision.source_preference == "general"

    def test_a_terse_reason_still_validates(self) -> None:
        """`reasoning` carries no min_length on purpose: a brief model must not burn a
        retry on brevity."""
        assert SupervisorDecision(action="FINALISE", reasoning="ok").reasoning == "ok"


class TestAppraisalVerdict:
    def test_insufficient_without_a_followup_is_valid(self) -> None:
        """"Not enough, and nothing specific would help" is a real verdict, and the loop
        answers it by finalising with populated `unknowns`. A validator forcing a
        follow-up here would turn that into a retry loop and invite an invented
        question."""
        verdict = AppraisalVerdict(sufficient=False, reasoning="thin, but nothing obvious")

        assert verdict.requested_followup is None
        assert verdict.missing_information == []

    def test_a_verdict_without_the_semantic_judgement_still_validates(self) -> None:
        """T2's three fields are additive, and that is the whole claim. A 4B model given a
        wider constrained-decoding target routinely fills only the fields it understands;
        if any of these lost its default the reply would fail `model_validate_json`, spend
        `_decode`'s one re-ask, and return `None` — which the loop reads as "the appraiser
        could not answer" and stops the run on. A defaulted field is what keeps the Day 3
        verdict a valid Day 5 verdict."""
        verdict = AppraisalVerdict.model_validate(
            {"sufficient": True, "reasoning": "covered"}
        )

        assert (verdict.accepted, verdict.rejected, verdict.disagreements) == ([], [], [])

    @pytest.mark.parametrize(
        ("field", "cap"), [("accepted", 8), ("rejected", 8), ("disagreements", 5)]
    )
    def test_the_semantic_judgement_is_bounded(self, field: str, cap: int) -> None:
        """These travel into `finalise`'s prompt, which already carries the sources, the
        `FocusPreparationReport` schema and the generated report through one 4096-token
        window. An unbounded list is how that window overflows — and the failure is the
        expensive kind, because what gets pushed out is the evidence the report is
        supposed to rest on."""
        payload = {"sufficient": False, "reasoning": "thin"}

        AppraisalVerdict.model_validate({**payload, field: ["source"] * cap})
        with pytest.raises(ValidationError):
            AppraisalVerdict.model_validate({**payload, field: ["source"] * (cap + 1)})


# --- the code-assembled messages (Day 5 T2) ------------------------------------------------
#
# `SupervisorDecision` and `AppraisalVerdict` are covered above as model output: when one of
# those is malformed, `_decode` quotes the errors back and re-asks. **Nothing re-asks for the
# three below.** They are built by our own code from a decision, the ledger and tool results,
# so a bound that stops holding does not produce a retry — it produces a blank query reaching
# `web_search`, or an appraiser judging a hop against an allowance it should never have seen.


def _assignment(**overrides: Any) -> dict[str, Any]:
    """The payload `runtime._assign` actually builds, as a mutable dict."""
    return {
        "research_question": "what does a B-tree index do",
        "session_minutes": 25,
        "hop": 1,
        "max_searches": 3,
        "max_fetches": 2,
        **overrides,
    }


class TestResearchAssignment:
    def test_a_representative_assignment_validates(self) -> None:
        """Including the two dedupe lists, which are the whole reason hop 2 is a new
        query rather than a repeat of hop 1 — and a repeat is live quota spent on a result
        the cache already held."""
        assignment = ResearchAssignment.model_validate(
            _assignment(
                source_preference="docs",
                avoid_queries=["postgres index"],
                avoid_urls=["https://a.example/1"],
            )
        )

        assert assignment.source_preference == "docs"
        assert assignment.avoid_urls == ["https://a.example/1"]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("research_question", ""),
            ("hop", 0),
            ("hop", 4),
            ("max_searches", -1),
            ("max_fetches", -1),
            ("session_minutes", 0),
            ("source_preference", "blogs"),
        ],
    )
    def test_materially_malformed_assignments_are_rejected(
        self, field: str, value: object
    ) -> None:
        """Each of these reaches the Researcher as silently wrong work rather than as an
        error. An empty question is a blank `web_search` query — one search claim and real
        SerpAPI quota spent for nothing. A negative allowance means `RunBudget.remaining`
        stopped clamping at 0, which `_assign` passes straight through. A hop outside 1-3
        is the cap that bounds the entire run no longer holding. And an unknown
        `source_preference` is handed to `web_search` as `source_type` with no
        translation, so this bound is the only thing between a drifting agent contract and
        a tool that cannot serve it."""
        with pytest.raises(ValidationError):
            ResearchAssignment.model_validate(_assignment(**{field: value}))


class TestResearchFindings:
    def test_a_hop_that_gathered_nothing_is_still_valid(self) -> None:
        """`run_research_step` promises never to raise, and a hop that found nothing is
        still a fact about the run. A contract that rejected the empty case would lose the
        `failures` that are the only reason the report does not read like a confident
        one."""
        findings = ResearchFindings(research_question="q", hop=1)

        assert findings.sources == [] and findings.failures == []

    @pytest.mark.parametrize("field", ["sufficient", "report", "action"])
    def test_findings_cannot_carry_a_final_report_decision(self, field: str) -> None:
        """The Researcher gathers; it never judges sufficiency and never writes the
        report. `extra="forbid"` is what makes that a property of the *message* rather
        than a sentence in a docstring — without it a worker could widen its own authority
        simply by widening what it hands back, and the Supervisor would have no way to
        tell."""
        with pytest.raises(ValidationError):
            ResearchFindings.model_validate(
                {"research_question": "q", "hop": 1, field: True}
            )


class TestAppraisalRequest:
    def test_a_representative_request_validates(self) -> None:
        """The read/unread distinction has to survive into the Appraiser's input: it is
        what `sufficiency.md` tells the model a lead is, as opposed to evidence."""
        request = AppraisalRequest(
            research_question="what does a B-tree index do",
            session_minutes=25,
            sources=[_source("https://a.example/read", read=True)],
        )

        assert request.sources[0].was_read

    @pytest.mark.parametrize("field", ["hop", "hops_remaining", "max_fetches"])
    def test_the_request_carries_no_budget_and_no_hop_count(self, field: str) -> None:
        """Deliberate, and the reason is behavioural rather than tidy: a judge that knows
        a follow-up is impossible stops asking for one, which hides the real verdict
        instead of changing it. Whether a follow-up can be afforded is the Supervisor's
        decision, taken after this message is answered — so the bound worth protecting is
        that the field cannot be added here by accident."""
        with pytest.raises(ValidationError):
            AppraisalRequest.model_validate(
                {"research_question": "q", "session_minutes": 25, field: 0}
            )


class TestRunState:
    def test_discovered_and_fetched_urls_are_distinguished(self) -> None:
        """The grounding rule (S9) admits a discovered-but-unread URL only with
        `authority="unknown"`. Collapsing the two sets would either reject a legitimate
        citation or let a hallucinated URL through — the guard's whole point."""
        state = RunState(
            task=TaskContext(task_title="Learn PostgreSQL indexing"),
            findings=[
                _finding(
                    1,
                    ["postgres btree index"],
                    [_source("https://a.example/read", read=True),
                     _source("https://b.example/lead", read=False)],
                )
            ],
        )

        assert state.evidence_urls == {"https://a.example/read", "https://b.example/lead"}
        assert state.fetched_urls == {"https://a.example/read"}

    def test_sources_and_queries_flatten_across_hops_in_order(self) -> None:
        """Hop 2's dedupe and `sources_examined` both read these. Losing hop 1 would let
        hop 2 re-run an identical live query — quota spent for a cached answer."""
        state = RunState(
            task=TaskContext(task_title="Learn PostgreSQL indexing"),
            findings=[
                _finding(1, ["q1"], [_source("https://a.example/1", read=True)]),
                _finding(2, ["q2"], [_source("https://b.example/2", read=True)]),
            ],
        )

        assert state.used_queries == ("q1", "q2")
        assert [s.url for s in state.all_sources] == [
            "https://a.example/1",
            "https://b.example/2",
        ]

    def test_the_hop_ceiling_survives_assignment(self) -> None:
        """The loop mutates this object. Without `validate_assignment` a bad increment
        would pass silently, and the hop bound is what stops a model driving forever."""
        state = RunState(task=TaskContext(task_title="Learn PostgreSQL indexing"))

        state.hop = 3
        with pytest.raises(ValidationError):
            state.hop = 4


class TestModelFacingSchemas:
    @pytest.mark.parametrize("model", MODEL_FACING)
    def test_unexpected_fields_are_rejected(self, model: type[BaseModel]) -> None:
        """`extra="forbid"` is what turns model drift into a retry rather than a field
        silently dropped on the floor."""
        payload: dict[str, Any] = {
            **_minimal(model),
            "confidence": 0.9,
        }

        with pytest.raises(ValidationError):
            model.model_validate(payload)

    @pytest.mark.parametrize("model", MODEL_FACING)
    def test_the_schema_survives_translation_to_gemini(
        self, model: type[BaseModel]
    ) -> None:
        """Attempt 3 of the finalisation ladder is the hosted provider. A schema whose
        `str | None` or `$defs` did not translate would fail there — the one attempt left
        after the local model has already failed twice."""
        translated = to_gemini_schema(model.model_json_schema())

        assert translated["type"] == "object"
        assert "anyOf" not in repr(translated)
        assert "$ref" not in repr(translated)
        assert set(model.model_fields) <= set(translated["properties"])

    def test_the_action_enum_reaches_the_model(self) -> None:
        """Constrained decoding can only hold the model to values the schema states."""
        translated = to_gemini_schema(SupervisorDecision.model_json_schema())

        assert translated["properties"]["action"]["enum"] == ["RESEARCH", "FINALISE"]


def test_the_source_type_literal_has_one_definition() -> None:
    """`source_preference` is handed to `web_search` as `source_type` with no
    translation. Two copies of the literal would let the agent contract drift away from
    the tool that consumes it, and the mismatch would only surface at runtime."""
    assert SearchSourceType is SearchLayerSourceType
    assert get_args(SearchSourceType) == ("docs", "technical", "academic", "general")


def _minimal(model: type[BaseModel]) -> dict[str, Any]:
    """The smallest valid payload for each model-facing schema."""
    if model is SupervisorDecision:
        return {"action": "FINALISE", "reasoning": "done"}
    return {"sufficient": True, "reasoning": "covered"}


def _messages() -> dict[str, BaseModel]:
    """One populated instance of every message the three agents exchange."""
    read = _source("https://a.example/read", read=True)
    return {
        "SupervisorDecision": SupervisorDecision(
            action="RESEARCH",
            research_question="what does a B-tree index do",
            source_preference="docs",
            reasoning="the task needs one concrete mechanism",
        ),
        "ResearchAssignment": ResearchAssignment.model_validate(
            _assignment(avoid_queries=["postgres index"], avoid_urls=["https://a.example/1"])
        ),
        "ResearchFindings": ResearchFindings(
            research_question="what does a B-tree index do",
            hop=1,
            queries_used=["postgresql b-tree index"],
            sources=[read],
            notes="one official page read",
        ),
        "AppraisalRequest": AppraisalRequest(
            research_question="what does a B-tree index do",
            session_minutes=25,
            sources=[read],
        ),
        "AppraisalVerdict": AppraisalVerdict(
            sufficient=False,
            missing_information=["partial index syntax"],
            requested_followup="what does EXPLAIN print for a partial index",
            reasoning="one page is thin for the question asked",
            accepted=["PostgreSQL: Indexes"],
            rejected=["a blog post, nothing sourced"],
            disagreements=["the two pages give different defaults"],
        ),
    }


@pytest.mark.parametrize("name", sorted(_messages()))
def test_every_inter_agent_message_survives_a_json_round_trip(name: str) -> None:
    """A hand-off has to be serializable, not merely typed.

    That is what separates T2's contracts from three functions that happen to pass objects:
    a transition between agents is a value that can be written to a trace, mirrored into
    `run_memory`, and — from Day 6 — carried over an MCP wire. The field that would break
    first is `GatheredSource.retrieved_at`, whose timezone is what `was_read` and therefore
    the whole grounding set are built on, and it is nested two messages deep inside
    `ResearchFindings` and `AppraisalRequest`. Losing it here is silent; losing it on the
    wire is Day 6's problem to diagnose.
    """
    message = _messages()[name]

    assert type(message).model_validate_json(message.model_dump_json()) == message
