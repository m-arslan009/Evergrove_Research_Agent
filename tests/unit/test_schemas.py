"""The schema is the contract, so its constraints get tested before anything uses them.

Plan section 21 names the rejections that must hold: 1 topic, 9 topics, a bad URL,
`session_duration_minutes=0`, and an empty `success_criteria`.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from evergrove_agent.schemas import (
    ErrorCode,
    FocusPreparationReport,
    Resource,
    TaskContext,
    ToolError,
    ToolResult,
)


def test_accepts_a_valid_payload(valid_report_payload: dict[str, Any]) -> None:
    report = FocusPreparationReport.model_validate(valid_report_payload)

    assert report.run_id == "run_a71c3f"
    assert report.resources[0].authority == "official"
    assert report.practice is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("topics_to_cover", ["Only one topic"]),
        ("topics_to_cover", [f"Topic {i}" for i in range(9)]),
        ("session_duration_minutes", 0),
        ("session_duration_minutes", 181),
        ("success_criteria", ""),
        ("interpreted_goal", "short"),
        ("hops_used", -1),
        ("hops_used", 4),
        ("sources_examined", -1),
        ("resources", [{"title": "x"} for _ in range(6)]),
        ("topics_to_skip", [f"Skip {i}" for i in range(11)]),
        ("assumptions", [f"Assumption {i}" for i in range(7)]),
        ("unknowns", [f"Unknown {i}" for i in range(7)]),
    ],
)
def test_rejects_out_of_range_fields(
    valid_report_payload: dict[str, Any], field: str, value: object
) -> None:
    payload = {**valid_report_payload, field: value}

    with pytest.raises(ValidationError):
        FocusPreparationReport.model_validate(payload)


def test_rejects_a_blank_topic(valid_report_payload: dict[str, Any]) -> None:
    payload = {**valid_report_payload, "topics_to_cover": ["B-tree basics", "   "]}

    with pytest.raises(ValidationError, match="empty entry"):
        FocusPreparationReport.model_validate(payload)


def test_rejects_a_bad_resource_url(valid_report_payload: dict[str, Any]) -> None:
    payload = {
        **valid_report_payload,
        "resources": [
            {
                "title": "Not a real URL",
                "url": "not-a-url",
                "why_this_source": "It should never get this far",
                "authority": "official",
            }
        ],
    }

    with pytest.raises(ValidationError):
        FocusPreparationReport.model_validate(payload)


def test_rejects_an_unknown_authority() -> None:
    with pytest.raises(ValidationError):
        Resource(
            title="Some page",
            url="https://example.com/a",
            why_this_source="Ten characters at least",
            authority="trustworthy",  # type: ignore[arg-type]
        )


def test_rejects_unexpected_fields(valid_report_payload: dict[str, Any]) -> None:
    """`extra="forbid"` is what turns model drift into a retry instead of silent loss."""
    payload = {**valid_report_payload, "confidence": 0.9}

    with pytest.raises(ValidationError):
        FocusPreparationReport.model_validate(payload)


def test_resources_and_practice_are_optional(valid_report_payload: dict[str, Any]) -> None:
    """A `--no-research` run has no sources, and a short session may have no exercise."""
    payload = {**valid_report_payload, "resources": [], "practice": None, "sources_examined": 0}

    report = FocusPreparationReport.model_validate(payload)

    assert report.resources == []
    assert report.practice is None


def test_json_schema_is_generated_for_constrained_decoding() -> None:
    """This dict is what gets handed to Ollama's `format=` parameter."""
    schema = FocusPreparationReport.model_json_schema()

    assert schema["type"] == "object"
    assert "topics_to_cover" in schema["properties"]
    assert set(schema["required"]) >= {
        "run_id",
        "original_task",
        "session_duration_minutes",
        "interpreted_goal",
        "success_criteria",
    }


class TestTaskContext:
    def test_defaults_to_a_twenty_five_minute_session(self) -> None:
        task = TaskContext(task_title="Learn PostgreSQL indexing")

        assert task.session_minutes == 25
        assert task.attachment_path is None

    @pytest.mark.parametrize("minutes", [0, 4, 181])
    def test_rejects_an_impossible_session_length(self, minutes: int) -> None:
        with pytest.raises(ValidationError):
            TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=minutes)

    def test_rejects_an_empty_title(self) -> None:
        with pytest.raises(ValidationError):
            TaskContext(task_title="")


class TestToolResult:
    def test_success_carries_data(self) -> None:
        result: ToolResult[str] = ToolResult(ok=True, data="fetched", duration_ms=12)

        assert result.ok
        assert result.from_cache is False

    def test_failure_must_carry_an_error(self) -> None:
        with pytest.raises(ValidationError, match="must carry an error"):
            ToolResult(ok=False, duration_ms=3)

    def test_success_must_not_carry_an_error(self) -> None:
        error = ToolError(code=ErrorCode.TIMEOUT, message="took too long", retryable=True)

        with pytest.raises(ValidationError, match="must not carry an error"):
            ToolResult(ok=True, data="x", duration_ms=3, error=error)

    def test_error_codes_serialise_as_their_names(self) -> None:
        error = ToolError(code=ErrorCode.CORRUPT_PDF, message="unreadable", retryable=False)

        assert error.model_dump(mode="json")["code"] == "CORRUPT_PDF"
