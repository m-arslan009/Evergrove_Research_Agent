"""Shared fixtures. Everything here is offline and model-free."""

from __future__ import annotations

from typing import Any

import pytest

from evergrove_agent.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Defaults only — `_env_file=None` keeps a developer's real `.env` out of the run."""
    return Settings(_env_file=None)


@pytest.fixture
def valid_report_payload() -> dict[str, Any]:
    """A FocusPreparationReport that must always validate.

    Tests mutate a copy of this to prove each rule rejects what it should.
    """
    return {
        "run_id": "run_a71c3f",
        "generated_at": "2026-08-11T09:00:00Z",
        "model_used": "qwen3:4b",
        "original_task": "Learn PostgreSQL indexing",
        "session_duration_minutes": 25,
        "interpreted_goal": "Understand B-tree indexes well enough to read EXPLAIN output",
        "session_objective": "Read EXPLAIN and tell an index scan from a seq scan",
        "topics_to_cover": ["What an index is", "B-tree basics", "Reading EXPLAIN"],
        "topics_to_skip": ["GIN", "GiST", "BRIN"],
        "resources": [
            {
                "title": "PostgreSQL: Indexes",
                "url": "https://www.postgresql.org/docs/current/indexes.html",
                "why_this_source": "Official documentation for the exact version in use",
                "authority": "official",
            }
        ],
        "practice": {
            "instruction": "Create a table with 10k rows, run EXPLAIN on a filtered SELECT",
            "expected_outcome": "You can point at the line that says Index Scan",
        },
        "success_criteria": "You can explain why one query used the index and another did not",
        "assumptions": ["The user has psql and a local database"],
        "unknowns": ["Which PostgreSQL version the user runs"],
        "hops_used": 1,
        "sources_examined": 2,
    }
