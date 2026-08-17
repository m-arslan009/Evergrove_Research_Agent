"""Persistent preparation memory and session memory (Day 4 T4).

Two tables, two jobs, one suite — because the thing worth protecting is the *boundary*
between them: `prep_memory` survives runs and is found by a normalised task key, while
`run_memory` belongs to one run and describes its hops. A change that let either answer the
other's question would be the expensive failure here, and it would not show up in a report.

What is deliberately not tested: `RunMemoryRecord`'s field access, the JSON encode/decode of a
list, Pydantic validating its own bounds, and schema creation — `test_db.py` already proves the
last one, and `initialize_schema` runs on every open.

Every test runs against a temporary file. Never `DB_PATH`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from evergrove_agent.config import Settings
from evergrove_agent.memory import db, prep_memory, run_memory
from evergrove_agent.schemas import FocusPreparationReport

NOW = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialised database in a temporary file."""
    with db.open_database(tmp_path / "agent.sqlite3") as conn:
        yield conn


@pytest.fixture
def report(valid_report_payload: dict[str, Any]) -> FocusPreparationReport:
    """The shared valid report — the only kind that ever reaches `save_preparation`."""
    return FocusPreparationReport.model_validate(valid_report_payload)


# --- persistent preparation memory ----------------------------------------------------------


def test_a_validated_preparation_round_trips(
    connection: sqlite3.Connection, report: FocusPreparationReport
) -> None:
    """Save then recall returns every field the next session needs, unchanged.

    Catches the failure this table exists to avoid being useless for: a JSON column, a URL or
    a timestamp lost or mangled between write and read. A continuation that recalls a
    preparation with empty `topics_covered` would confidently repeat the whole of last
    session, and nothing downstream could tell that it had.
    """
    saved = prep_memory.save_preparation(connection, report=report, now=NOW)

    recalled = prep_memory.recall_previous_preparation(
        connection, task_title="Learn PostgreSQL indexing", max_age_days=30, now=NOW
    )

    assert recalled == saved
    assert recalled is not None
    assert recalled.original_task == "Learn PostgreSQL indexing"
    assert recalled.interpreted_goal == report.interpreted_goal
    assert recalled.session_objective == report.session_objective
    assert recalled.topics_covered == ["What an index is", "B-tree basics", "Reading EXPLAIN"]
    assert recalled.topics_deferred == ["GIN", "GiST", "BRIN"]
    assert recalled.source_urls == [
        "https://www.postgresql.org/docs/current/indexes.html"
    ]
    assert recalled.run_id == "run_a71c3f"
    assert recalled.created_at == NOW


@pytest.mark.parametrize(
    "title",
    [
        "Learn PostgreSQL indexing",
        "postgresql indexing",
        "Continue PostgreSQL Indexing!",
        "Indexing in PostgreSQL",
        "  continue   learning   postgresql   indexing  ",
    ],
)
def test_equivalent_task_titles_share_one_key(title: str) -> None:
    """Every wording of one subject matches the preparation the others produced.

    This equivalence *is* the cross-run feature: without it "Continue PostgreSQL indexing"
    recalls nothing and yesterday's session is repeated in full. It is asserted over the key
    rather than over a database round trip because the key is what the index looks up, and a
    stopword-list edit that broke one of these would otherwise stay invisible until a live
    run — nine to fifteen minutes to find out.
    """
    assert prep_memory.normalize_task_key(title) == "indexing postgresql"


def test_a_different_subject_does_not_match(connection: sqlite3.Connection) -> None:
    """A saved preparation is not recalled for an unrelated task.

    The other half of the matching rule, and the more dangerous one to get wrong: a
    false match hands the agent someone else's covered topics and it will *skip* material the
    user actually asked for. Cheaper to catch here than in a report nobody can audit.
    """
    prep_memory.save_preparation(connection, report=_report_for("Learn Redis clustering"), now=NOW)

    assert (
        prep_memory.recall_previous_preparation(
            connection, task_title="Learn PostgreSQL indexing", now=NOW
        )
        is None
    )


@pytest.mark.parametrize(
    ("age_days", "expected"),
    [(29, True), (31, False)],
)
def test_recall_respects_the_age_window(
    connection: sqlite3.Connection,
    report: FocusPreparationReport,
    age_days: int,
    expected: bool,
) -> None:
    """A preparation inside the window is recalled; one outside it is not.

    Catches a boundary or timezone slip in the 30-day window, which would either resurface a
    months-old session as "what we did last time" or make continuation impossible. The clock
    is injected, so neither case waits.
    """
    prep_memory.save_preparation(
        connection, report=report, now=NOW - timedelta(days=age_days)
    )

    recalled = prep_memory.recall_previous_preparation(
        connection, task_title="Learn PostgreSQL indexing", max_age_days=30, now=NOW
    )

    assert (recalled is not None) is expected


def test_the_window_default_comes_from_config(
    connection: sqlite3.Connection,
    report: FocusPreparationReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no `max_age_days`, `MEMORY_RECALL_MAX_AGE_DAYS` decides.

    The setting was declared on Day 1 and consumed by nothing until now. A default hard-coded
    in this module instead would ignore it silently, and the only symptom would be a recall
    window nobody can change.
    """
    monkeypatch.setattr(
        prep_memory,
        "get_settings",
        lambda: Settings(_env_file=None, memory_recall_max_age_days=3),
    )
    prep_memory.save_preparation(connection, report=report, now=NOW - timedelta(days=5))

    assert (
        prep_memory.recall_previous_preparation(
            connection, task_title="Learn PostgreSQL indexing", now=NOW
        )
        is None
    )


def test_the_most_recent_preparation_wins(
    connection: sqlite3.Connection, valid_report_payload: dict[str, Any]
) -> None:
    """Three sessions on one task recall the newest, not the first.

    Catches a missing or inverted `ORDER BY`, which would pin every continuation to the
    oldest session on file — so the agent would keep offering to continue from a beginner
    session it had already moved past twice.
    """
    for index, day in enumerate((10, 1, 5)):
        payload = {
            **valid_report_payload,
            "run_id": f"run_00000{index}",
            "interpreted_goal": f"Goal recorded {day} days ago, at least ten characters",
        }
        prep_memory.save_preparation(
            connection,
            report=FocusPreparationReport.model_validate(payload),
            now=NOW - timedelta(days=day),
        )

    recalled = prep_memory.recall_previous_preparation(
        connection, task_title="postgresql indexing", now=NOW
    )

    assert recalled is not None
    assert recalled.run_id == "run_000001"


def test_an_unreadable_row_degrades_to_the_next_one(
    connection: sqlite3.Connection, report: FocusPreparationReport
) -> None:
    """A corrupt row is skipped, not raised on, and an older good row still answers.

    Memory is an enhancement: one row written by an older build must not take the recall path
    down or hide every other preparation behind it. The same degradation `search_cache`
    applies to a stale payload.
    """
    prep_memory.save_preparation(connection, report=report, now=NOW - timedelta(days=2))
    connection.execute(
        "UPDATE prep_memory SET topics_covered = 'not json' WHERE run_id = ?",
        (report.run_id,),
    )
    connection.commit()
    prep_memory.save_preparation(
        connection,
        report=FocusPreparationReport.model_validate(
            {**report.model_dump(mode="json"), "run_id": "run_older"}
        ),
        now=NOW - timedelta(days=9),
    )

    recalled = prep_memory.recall_previous_preparation(
        connection, task_title="Learn PostgreSQL indexing", now=NOW
    )

    assert recalled is not None
    assert recalled.run_id == "run_older"


# --- session memory ---------------------------------------------------------------------------


def test_an_earlier_hop_is_still_readable_after_a_later_one(
    connection: sqlite3.Connection,
) -> None:
    """Hop 1's goal, finding and appraisal survive hop 2 being written.

    This is the plan's session-memory claim made checkable: what the run knew at hop 1 is
    still there, still attributed to hop 1, after hop 2 wrote its own. Catches a writer that
    replaced the run's memory instead of appending to it, or one that lost the hop number —
    either of which would make "hop 2 built on hop 1" unprovable after the fact.
    """
    run_memory.record_entries(
        connection,
        run_id="run_a71c3f",
        hop=1,
        entries=run_memory.entries_from(
            goal="B-tree fundamentals for a 25-minute session",
            decision="RESEARCH: the default index type is the place to start",
            findings="B-tree handles equality and range queries",
            appraisal="sufficient=False: nothing explains EXPLAIN output yet",
            queries=["postgresql b-tree index"],
            urls=["https://www.postgresql.org/docs/current/btree.html"],
        ),
        now=NOW,
    )
    run_memory.record_entries(
        connection,
        run_id="run_a71c3f",
        hop=2,
        entries=run_memory.entries_from(goal="Reading EXPLAIN output"),
        now=NOW + timedelta(seconds=90),
    )

    remembered = run_memory.get_run_memory(connection, "run_a71c3f")
    hop_one = {(row.kind, row.content) for row in remembered if row.hop == 1}

    assert {kind for kind, _ in hop_one} == {
        "goal",
        "decision",
        "finding",
        "appraisal",
        "seen_query",
        "seen_url",
    }
    assert ("goal", "B-tree fundamentals for a 25-minute session") in hop_one
    assert [row.hop for row in remembered] == [1, 1, 1, 1, 1, 1, 2]
    assert run_memory.get_run_memory(connection, "run_a71c3f", kind="goal")[1].hop == 2


def test_seen_queries_and_urls_come_back_as_sets(
    connection: sqlite3.Connection,
) -> None:
    """A query or URL already spent in this run is recognisable from what was recorded.

    The point of recording them: a repeat is a wasted search call and, on a metered backend,
    real quota. `RunState` prevents that in process; this proves the durable record can answer
    the same question, which is what an audit or a resumed run would have to rely on.
    """
    run_memory.record_entries(
        connection,
        run_id="run_a71c3f",
        hop=1,
        entries=run_memory.entries_from(
            queries=["postgresql b-tree index", "postgresql b-tree index", ""],
            urls=["https://www.postgresql.org/docs/current/btree.html"],
        ),
        now=NOW,
    )

    assert run_memory.seen_queries(connection, "run_a71c3f") == {
        "postgresql b-tree index"
    }
    assert run_memory.seen_urls(connection, "run_a71c3f") == {
        "https://www.postgresql.org/docs/current/btree.html"
    }
    assert run_memory.seen_queries(connection, "run_other") == set()


def test_one_run_never_sees_another_run_s_memory(
    connection: sqlite3.Connection,
) -> None:
    """Session memory is scoped to its own `run_id`.

    Both tables live in one SQLite file, and this is the line between them that matters most:
    a run reading another run's hops would reuse findings it never gathered, and the grounding
    check would then be judging a report against the wrong evidence.
    """
    run_memory.record_entries(
        connection,
        run_id="run_first",
        hop=1,
        entries=run_memory.entries_from(goal="first run"),
        now=NOW,
    )
    run_memory.record_entries(
        connection,
        run_id="run_second",
        hop=1,
        entries=run_memory.entries_from(goal="second run"),
        now=NOW,
    )

    assert [row.content for row in run_memory.get_run_memory(connection, "run_first")] == [
        "first run"
    ]


def _report_for(task_title: str) -> FocusPreparationReport:
    """A minimal valid report for `task_title` — used where only the key matters."""
    return FocusPreparationReport(
        run_id="run_ffffff",
        generated_at=NOW,
        model_used="qwen3:4b",
        original_task=task_title,
        session_duration_minutes=25,
        interpreted_goal="A narrowed goal of at least ten characters",
        session_objective="An objective of at least ten characters",
        topics_to_cover=["First topic", "Second topic"],
        success_criteria="You can explain the thing you set out to explain",
        hops_used=0,
        sources_examined=0,
    )
