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
    AppraisalVerdict,
    GatheredSource,
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
