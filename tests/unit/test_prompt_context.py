"""The four stage prompts and the renderers that fill their placeholders.

Wording is explicitly the part of S3 that stays safe to change, so nothing here asserts a
sentence. What is pinned is the contract underneath it — the four things that break a run
silently rather than loudly:

* a prompt file and its caller disagreeing about placeholders (`render_prompt` is
  `str.format`, so a stray brace or a renamed placeholder is a `KeyError` at runtime, on
  a path no earlier test reaches);
* a source that was never opened reading like one that was, which is what the authority
  rule in `finalise.md` and the grounding check in S9 both rest on;
* an excerpt arriving unbounded, in a 4096-token context window;
* a degraded run — a failed tool, a thin verdict — reaching the finalise prompt looking
  exactly like a confident one.

Offline and model-free: these are text functions, and no provider, tool, socket or
database is involved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from evergrove_agent.agents.prompt_context import (
    render_already_covered,
    render_progress,
    render_research_context,
    render_sources,
    render_tool_outcome,
)
from evergrove_agent.agents.tool_calling import ToolCallOutcome
from evergrove_agent.config import Settings
from evergrove_agent.llm.base import ToolCall
from evergrove_agent.llm.prompts import render_prompt
from evergrove_agent.schemas import (
    AppraisalVerdict,
    ErrorCode,
    GatheredSource,
    ResearchAssignment,
    ResearchFindings,
    RunState,
    TaskContext,
    ToolError,
    ToolFailure,
    ToolResult,
)

# The documented placeholder set of every stage prompt, in one place. `finalise` is
# listed with exactly the five arguments `main.py` passes today: this row is what catches
# a sixth placeholder being added to that file, which would break the shipped
# `--no-research` path the moment it ran.
PROMPT_PLACEHOLDERS: dict[str, dict[str, Any]] = {
    "plan": {
        "task_title": "TITLE-SENTINEL",
        "task_description": "DESCRIPTION-SENTINEL",
        "session_minutes": 25,
        "progress": "PROGRESS-SENTINEL",
    },
    "research_step": {
        "research_question": "QUESTION-SENTINEL",
        "session_minutes": 25,
        "source_preference": "docs",
        "available_tools": "TOOLS-SENTINEL",
        "allowance": "ALLOWANCE-SENTINEL",
        "already_covered": "COVERED-SENTINEL",
        "attachment": "ATTACHMENT-SENTINEL",
    },
    "sufficiency": {
        "research_question": "QUESTION-SENTINEL",
        "session_minutes": 25,
        "sources": "SOURCES-SENTINEL",
    },
    "finalise": {
        "task_title": "TITLE-SENTINEL",
        "task_description": "DESCRIPTION-SENTINEL",
        "session_minutes": 25,
        "max_topics": 5,
        "research_context": "CONTEXT-SENTINEL",
    },
}

QUESTION = "Which PostgreSQL index type suits equality lookups?"
DOCS_URL = "https://www.postgresql.org/docs/current/indexes.html"
BLOG_URL = "https://example.com/indexing-explained"


def _read_source(excerpt: str = "B-tree indexes answer equality and range lookups.") -> GatheredSource:
    return GatheredSource(
        url=DOCS_URL,
        title="PostgreSQL: Indexes",
        domain_class="official",
        snippet="Official index documentation",
        excerpt=excerpt,
        retrieved_at=datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc),
    )


def _lead_source() -> GatheredSource:
    """Discovered by a search, never opened — `retrieved_at` stays None."""
    return GatheredSource(
        url=BLOG_URL,
        title="Indexing explained",
        domain_class="secondary",
        snippet="A blog claims things about indexes",
    )


def _researched_state() -> RunState:
    return RunState(
        task=TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25),
        hop=1,
        findings=[
            ResearchFindings(
                research_question=QUESTION,
                hop=1,
                queries_used=["postgresql index types"],
                sources=[_read_source(), _lead_source()],
                failures=[
                    ToolFailure(
                        tool_name="web_search",
                        error=ToolError(
                            code=ErrorCode.SEARCH_UNAVAILABLE,
                            message="the backend answered 503 twice",
                            retryable=True,
                        ),
                    )
                ],
                notes="Only the official index page was readable.",
            )
        ],
        verdict=AppraisalVerdict(
            sufficient=False,
            missing_information=["partial index syntax"],
            requested_followup="What does EXPLAIN print for a partial index?",
            reasoning="One page is thin for the question asked.",
        ),
    )


@pytest.mark.parametrize("name", sorted(PROMPT_PLACEHOLDERS))
def test_every_prompt_renders_with_its_documented_placeholders(name: str) -> None:
    """`render_prompt` is `str.format`: an unescaped brace or a renamed placeholder
    raises, and every value handed in must actually reach the text — a placeholder a file
    stopped using is a fact the model silently stopped being told."""
    values = PROMPT_PLACEHOLDERS[name]

    text = render_prompt(name, **values)

    for value in values.values():
        assert str(value) in text


def test_render_sources_separates_read_from_unread_and_bounds_the_excerpt() -> None:
    """Both halves are load-bearing. The status wording is what `finalise.md`'s authority
    rule keys on, and an unbounded excerpt is how a 4096-token window overflows."""
    settings = Settings(_env_file=None, source_excerpt_chars=200)  # the field's floor

    text = render_sources([_read_source("x" * 500), _lead_source()], settings=settings)

    assert "status: read" in text
    assert "status: found but not opened" in text
    assert "x" * 200 in text
    assert "x" * 201 not in text
    # A source nobody opened contributes its search snippet, never an excerpt.
    assert "A blog claims things about indexes" in text


def test_research_context_reports_what_failed_and_what_is_still_missing(
    settings: Settings,
) -> None:
    """A run whose search broke must reach the finalise prompt visibly degraded. Silence
    here is exactly how a sourceless report ends up reading like a confident one."""
    text = render_research_context(_researched_state(), settings=settings)

    assert "SEARCH_UNAVAILABLE" in text
    assert "the backend answered 503 twice" in text
    assert "partial index syntax" in text
    assert "Only the official index page was readable." in text


def test_progress_starts_empty_and_then_carries_what_was_already_spent(
    settings: Settings,
) -> None:
    """The planner has no memory of its own previous turn, so this block is the only
    thing stopping hop 2 re-running hop 1's query — which on a metered backend is quota
    spent on a result the cache already held."""
    fresh = RunState(task=TaskContext(task_title="Learn PostgreSQL indexing"))

    assert "No research has been done yet" in render_progress(fresh, hops_remaining=3)

    text = render_progress(_researched_state(), hops_remaining=2)

    assert "postgresql index types" in text
    assert QUESTION in text
    assert "Still available: 2" in text
    assert "What does EXPLAIN print for a partial index?" in text


def test_already_covered_lists_both_spent_queries_and_opened_pages() -> None:
    """The researcher receives only its assignment (S1's self-contained rule), so what an
    earlier hop spent has to travel in this block or not at all."""
    assignment = ResearchAssignment(
        research_question=QUESTION,
        session_minutes=25,
        hop=2,
        max_searches=2,
        max_fetches=2,
        avoid_queries=["postgresql index types"],
        avoid_urls=[DOCS_URL],
    )

    text = render_already_covered(assignment)

    assert "postgresql index types" in text
    assert DOCS_URL in text


def test_a_tool_outcome_reaches_the_model_readable_and_bounded() -> None:
    """`research_step.md` tells the model to read an error and try once more. It can only
    do that if the code, the message and whether a retry could help all survive the trip
    into prose — and a success has to be clipped, because one `fetch_url` reply can
    legitimately be 20,000 characters."""
    settings = Settings(_env_file=None, source_excerpt_chars=200)  # the field's floor
    failed = ToolCallOutcome(
        call=ToolCall(name="fetch_url", arguments={"url": BLOG_URL}),
        result=ToolResult(
            ok=False,
            error=ToolError(
                code=ErrorCode.FETCH_FAILED,
                message="502 from the origin server",
                retryable=True,
            ),
            duration_ms=3,
        ),
    )

    text = render_tool_outcome(failed, settings=settings)

    assert "FETCH_FAILED" in text
    assert "502 from the origin server" in text
    assert "retrying may help" in text

    # Any tool output model would do here; the renderer only serialises and clips.
    succeeded = ToolCallOutcome(
        call=ToolCall(name="fetch_url", arguments={"url": DOCS_URL}),
        result=ToolResult(ok=True, data=_read_source("y" * 5000), duration_ms=7),
    )

    assert len(render_tool_outcome(succeeded, settings=settings)) < 400
