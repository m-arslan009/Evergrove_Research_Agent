"""`validate_report`: does a shaped report actually rest on what the run gathered?

The report has already passed Pydantic by the time these rules run, so nothing here
re-checks a type, a length or an enum — `test_schemas.py` owns that. What this suite
protects is the only thing a schema cannot see: which URLs this run really saw, and
whether anybody opened them.

Every case is pure and offline. The evidence sets are written out in the test rather than
built from a `RunState`, because the function's contract is "two sets and a report" and a
constructed run would prove `RunState`'s projections a second time instead.
"""

from __future__ import annotations

from typing import Any

import pytest

from evergrove_agent.schemas import FocusPreparationReport, SourceAuthority
from evergrove_agent.tools.base import RunContext
from evergrove_agent.tools.validate_report import (
    ReportValidation,
    ValidateReportInput,
    ValidateReportTool,
    validate_report,
)

DOCS = "https://www.postgresql.org/docs/current/indexes.html"
BLOG = "https://someblog.example/postgres-indexes"
UNSEEN = "https://developer.chrome.com/docs/indexes"

EVIDENCE = frozenset({DOCS, BLOG})
FETCHED = frozenset({DOCS})


def _report(payload: dict[str, Any], **changes: Any) -> FocusPreparationReport:
    """The shared valid payload with fields replaced — never mutated in place, so one
    case cannot leak into the next."""
    return FocusPreparationReport.model_validate({**payload, **changes})


def _check(report: FocusPreparationReport, **overrides: Any) -> ReportValidation:
    """The grounded defaults, so each test states only what it is varying."""
    return validate_report(
        report,
        evidence_urls=overrides.pop("evidence_urls", EVIDENCE),
        fetched_urls=overrides.pop("fetched_urls", FETCHED),
        max_topics=overrides.pop("max_topics", 5),
        research_performed=overrides.pop("research_performed", True),
    )


def _codes(result: ReportValidation) -> list[str]:
    return [issue.code for issue in result.issues]


# --- the grounding rule ---------------------------------------------------------------------


def test_a_report_citing_only_gathered_sources_passes(
    valid_report_payload: dict[str, Any],
) -> None:
    """The case that must not regress in the other direction. A validator that rejects
    good reports would make S10's ladder burn every attempt and fail every run — a far
    worse failure than the one this module exists to catch."""
    result = _check(_report(valid_report_payload))

    assert result.ok is True
    assert result.issues == []
    assert result.as_lines() == []


def test_a_citation_the_run_never_gathered_is_rejected(
    valid_report_payload: dict[str, Any],
) -> None:
    """The requirement S9 exists for: a plausible URL that appears nowhere in the run.

    Pydantic accepts it — it is a well-formed `HttpUrl` — so set membership is the only
    thing standing between a hallucinated citation and the user.
    """
    report = _report(
        valid_report_payload,
        resources=[
            {
                "title": "Chrome: Indexes",
                "url": UNSEEN,
                "why_this_source": "Looks like the official guide for this",
                "authority": "official",
            }
        ],
    )

    result = _check(report)

    assert result.ok is False
    assert _codes(result) == ["ungrounded_url"]
    assert result.issues[0].field == "resources[0].url"
    assert "developer.chrome.com" in result.issues[0].message
    assert result.as_lines()[0].startswith("- resources[0].url: ")


@pytest.mark.parametrize(
    ("url", "trailing"),
    [
        (f"{DOCS}#btree", "a fragment"),
        (f"{DOCS}?utm_source=newsletter", "a tracking parameter"),
        ("https://someblog.example/postgres-indexes/", "a trailing slash"),
    ],
)
def test_the_same_page_written_differently_is_still_grounded(
    valid_report_payload: dict[str, Any], url: str, trailing: str
) -> None:
    """Both sides go through `canonicalize_url`, the function `GatheredSource.url` was
    built with. Catches the worst possible bug here: a *valid* report failing grounding on
    URL formatting, which S10 would answer by spending every retry attempt on nothing."""
    report = _report(
        valid_report_payload,
        resources=[
            {
                "title": "PostgreSQL: Indexes",
                "url": url,
                "why_this_source": f"The same page, carrying {trailing}",
                "authority": "unknown",
            }
        ],
    )

    assert _check(report).ok is True


# There is deliberately no test for a non-http(s) citation: `Resource.url` is an `HttpUrl`,
# so the schema rejects one before this function ever sees it. The `cited is None` branch
# stays as a guard on the membership test, not as behaviour a report can reach.


# --- the authority rule -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "authority", ["official", "standards", "primary", "secondary", "unknown"]
)
def test_a_source_nobody_opened_may_only_be_cited_as_unknown(
    valid_report_payload: dict[str, Any], authority: SourceAuthority
) -> None:
    """`BLOG` was discovered but never fetched. Catches an unread page being promoted to
    authoritative — the quiet version of a hallucination, where the URL is real but the
    claim about it was never checked by anyone."""
    report = _report(
        valid_report_payload,
        resources=[
            {
                "title": "Postgres indexes explained",
                "url": BLOG,
                "why_this_source": "Search said it covers B-trees",
                "authority": authority,
            }
        ],
    )

    result = _check(report)

    if authority == "unknown":
        assert result.ok is True
    else:
        assert _codes(result) == ["authority_overclaim"]
        assert result.issues[0].field == "resources[0].authority"
        assert "unknown" in result.issues[0].message


def test_a_fetched_source_may_claim_any_authority(
    valid_report_payload: dict[str, Any],
) -> None:
    """The other half of the rule. Somebody opened `DOCS`, so calling it official is
    exactly what the report should do — a validator that flagged this would push every
    report towards `unknown` and make the field meaningless."""
    assert _check(_report(valid_report_payload)).ok is True


# --- a run that gathered nothing ------------------------------------------------------------


def test_a_run_without_research_may_not_cite_and_must_admit_it(
    valid_report_payload: dict[str, Any],
) -> None:
    """The `--no-research` path. Two failures at once, on purpose: every issue is
    reported in one pass, so a retry is told everything that is wrong rather than
    spending an attempt to discover the second problem."""
    report = _report(valid_report_payload, unknowns=[])

    result = _check(
        report,
        evidence_urls=frozenset(),
        fetched_urls=frozenset(),
        research_performed=False,
    )

    assert result.ok is False
    assert _codes(result) == [
        "ungrounded_url",
        "resources_without_research",
        "unknowns_required",
    ]


def test_a_no_research_report_that_admits_what_it_rests_on_passes(
    valid_report_payload: dict[str, Any],
) -> None:
    """The honest version of the same run must not be rejected."""
    report = _report(
        valid_report_payload,
        resources=[],
        unknowns=["Nothing was researched; this rests on model knowledge alone"],
    )

    result = _check(
        report,
        evidence_urls=frozenset(),
        fetched_urls=frozenset(),
        research_performed=False,
    )

    assert result.ok is True


# --- the prompt rules nothing used to enforce --------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param(
            {"topics_to_skip": ["GIN", "Reading EXPLAIN"]},
            "topic_overlap",
            id="a topic both covered and skipped",
        ),
        pytest.param(
            {
                "topics_to_cover": [
                    "What an index is",
                    "B-tree basics",
                    "Reading EXPLAIN",
                    "Partial indexes",
                    "Covering indexes",
                    "Index-only scans",
                ]
            },
            "too_many_topics",
            id="more topics than the session can hold",
        ),
        pytest.param(
            {"interpreted_goal": "learn postgresql indexing!"},
            "goal_not_narrowed",
            id="a goal that is the task typed back",
        ),
    ],
)
def test_the_finalise_rules_a_schema_cannot_express_are_enforced(
    valid_report_payload: dict[str, Any], changes: dict[str, Any], expected: str
) -> None:
    """Three rules `finalise.md` states in words and nothing checked. Parameterized
    because each is one comparison over the same report — separate tests would be three
    copies of one setup. `max_topics=5` is what a 25-minute session allows, so the middle
    case is over the session's limit while still inside the schema's `max_length=8`."""
    result = _check(_report(valid_report_payload, **changes))

    assert _codes(result) == [expected]


# --- the registered wrapper ---------------------------------------------------------------------


async def test_the_tool_reports_a_rejected_report_as_a_successful_call(
    valid_report_payload: dict[str, Any],
) -> None:
    """`ToolResult.ok` answers "did the validator run", not "was the report good". A
    wrapper that conflated the two would make an invalid report look like a broken tool in
    the trace, and would hand S10 an error envelope where it expects a verdict."""
    result = await ValidateReportTool().run(
        ValidateReportInput(
            report=_report(valid_report_payload, resources=[]),
            evidence_urls=[],
            fetched_urls=[],
            research_performed=False,
        ),
        RunContext(),
    )

    assert result.ok is True
    assert result.error is None
    assert result.data.ok is False
    assert _codes(result.data) == ["unknowns_required"]
