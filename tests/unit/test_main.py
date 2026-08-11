"""The Day 1 round trip: task in, validated report out, with a scripted model."""

from __future__ import annotations

import json
from typing import Any

import pytest

from evergrove_agent.config import Settings
from evergrove_agent.llm import FakeProvider
from evergrove_agent.main import (
    NO_RESEARCH_ASSUMPTION,
    build_parser,
    max_topics_for,
    prepare_without_research,
)
from evergrove_agent.schemas import TaskContext


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(5, 3), (15, 3), (25, 5), (45, 8), (180, 8)],
)
def test_session_sizing_rule(minutes: int, expected: int) -> None:
    """`max(3, minutes // 5)`, capped by the schema's own limit of 8 (plan 17)."""
    assert max_topics_for(minutes) == expected


async def test_round_trip_produces_a_validated_report(
    settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    provider = FakeProvider([json.dumps(valid_report_payload)])
    task = TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25)

    report = await prepare_without_research(task, provider, settings)

    assert report.interpreted_goal == valid_report_payload["interpreted_goal"]
    assert provider.calls[0].schema_name == "FocusPreparationReport"
    assert provider.calls[0].temperature == 0.0


async def test_the_prompt_carries_the_task_and_the_topic_cap(
    settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    provider = FakeProvider([json.dumps(valid_report_payload)])
    task = TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25)

    await prepare_without_research(task, provider, settings)

    prompt = provider.calls[0].messages[0].content
    assert "Learn PostgreSQL indexing" in prompt
    assert "between 2 and 5 items" in prompt


async def test_no_research_cannot_smuggle_in_sources(
    settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    """The scripted reply cites postgresql.org. Nothing was fetched, so it must not survive."""
    provider = FakeProvider([json.dumps(valid_report_payload)])
    task = TaskContext(task_title="Learn PostgreSQL indexing", session_minutes=25)

    report = await prepare_without_research(task, provider, settings)

    assert report.resources == []
    assert report.sources_examined == 0
    assert report.hops_used == 0
    assert NO_RESEARCH_ASSUMPTION in report.assumptions
    assert report.unknowns  # honesty field must not be empty when nothing was read


async def test_bookkeeping_fields_are_ours_not_the_models(
    settings: Settings, valid_report_payload: dict[str, Any]
) -> None:
    provider = FakeProvider([json.dumps(valid_report_payload)])
    task = TaskContext(task_title="Index tuning in practice", session_minutes=50)

    report = await prepare_without_research(task, provider, settings)

    assert report.run_id != valid_report_payload["run_id"]
    assert report.run_id.startswith("run_")
    assert report.model_used == "fake-model"
    assert report.original_task == "Index tuning in practice"
    assert report.session_duration_minutes == 50  # the model must not rescope


class TestCli:
    def test_no_research_has_a_no_search_alias(self) -> None:
        """Plan 21 calls it --no-research; plan 30 calls it --no-search. Both work."""
        parser = build_parser()

        assert parser.parse_args(["--task", "x", "--no-research"]).no_research
        assert parser.parse_args(["--task", "x", "--no-search"]).no_research

    def test_task_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--minutes", "25"])

    def test_defaults_to_twenty_five_minutes(self) -> None:
        assert build_parser().parse_args(["--task", "x"]).minutes == 25
