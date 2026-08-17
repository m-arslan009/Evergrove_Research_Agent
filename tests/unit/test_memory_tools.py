"""The memory tools: the wired path, and the guarantee that memory cannot end a run.

`test_memory.py` proves the storage. This suite proves the two things only the tool layer can
be responsible for:

* the answers a caller branches on — "nothing to recall" has to be a clear, successful empty
  result, not a failure;
* **a broken database never raises.** That is the whole reason these wrappers exist. A run on
  this hardware takes 9-15 minutes, and losing one to a memory write would be a far worse
  failure than having no memory.

Offline throughout: SQLite under `tmp_path`, no model, no network.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from evergrove_agent.config import Settings
from evergrove_agent.memory import db, run_memory
from evergrove_agent.schemas import ErrorCode, FocusPreparationReport
from evergrove_agent.tools import RunContext
from evergrove_agent.tools.memory_tools import (
    RecallInput,
    RecordRunMemoryInput,
    SavePreparationInput,
)
from evergrove_agent.tools.wiring import build_tool_registry


@pytest.fixture
def offline_settings(tmp_path: Path) -> Settings:
    """Defaults pointed at a temporary database — never the real `DB_PATH`."""
    return Settings(_env_file=None, db_path=tmp_path / "agent.sqlite3")


async def test_recall_with_no_history_is_a_successful_empty_answer(
    offline_settings: Settings,
) -> None:
    """A first run gets `ok=True`, `found=False`, `previous=None`.

    The contract the memory-aware prompting task will branch on. Catches the tempting mistake
    of reporting "no memory" as a `ToolError`: every first run would then carry a failed tool
    call in its trace, and a real outage would be indistinguishable from an empty database.
    """
    registry = build_tool_registry(offline_settings)

    result = await registry.call(
        "recall_previous_preparation",
        RecallInput(task_title="Learn PostgreSQL indexing"),
        RunContext(),
    )

    assert result.ok is True
    assert result.error is None
    assert result.data is not None
    assert result.data.found is False
    assert result.data.previous is None


async def test_a_saved_preparation_is_recalled_through_the_registry(
    offline_settings: Settings, valid_report_payload: dict[str, object]
) -> None:
    """Save through one registry call, recall through another, under a reworded title.

    The end-to-end cross-run claim, exercised the way the run itself will do it: through
    `registry.call`, with arguments validated by the tools' own input models. A unit-level
    round trip cannot catch a tool wired to the wrong storage function, a `task_key` computed
    from the wrong field, or an input model that rejects what the loop actually sends.
    """
    registry = build_tool_registry(offline_settings)
    report = FocusPreparationReport.model_validate(valid_report_payload)

    saved = await registry.call(
        "save_preparation", SavePreparationInput(report=report), RunContext()
    )
    recalled = await registry.call(
        "recall_previous_preparation",
        RecallInput(task_title="Continue postgresql indexing"),
        RunContext(),
    )

    assert saved.ok is True
    assert saved.data is not None
    assert saved.data.saved is True
    assert saved.data.task_key == "indexing postgresql"

    assert recalled.ok is True
    assert recalled.data is not None
    assert recalled.data.found is True
    assert recalled.data.previous is not None
    assert recalled.data.previous.run_id == report.run_id
    assert recalled.data.previous.topics_deferred == ["GIN", "GiST", "BRIN"]


async def test_recorded_session_memory_belongs_to_the_calling_run(
    offline_settings: Settings,
) -> None:
    """`record_run_memory` takes its `run_id` from `ctx`, never from its arguments.

    A tool that could be *told* which run it belongs to could file one run's hops under
    another's id, and the mistake would only surface much later as a run appearing to have
    evidence it never gathered. `RunContext` is already the single answer to "which run is
    this" for the budget and the trace, and this keeps memory on the same footing.
    """
    registry = build_tool_registry(offline_settings)
    ctx = RunContext()

    result = await registry.call(
        "record_run_memory",
        RecordRunMemoryInput(hop=1, entries=run_memory.entries_from(goal="a narrowed goal")),
        ctx,
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data.written == 1
    with db.open_database(offline_settings.db_path) as connection:
        rows = run_memory.get_run_memory(connection, ctx.run_id)
    assert [(row.hop, row.kind, row.content) for row in rows] == [
        (1, "goal", "a narrowed goal")
    ]


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("recall_previous_preparation", RecallInput(task_title="Learn PostgreSQL indexing")),
        (
            "record_run_memory",
            RecordRunMemoryInput(
                hop=1, entries=run_memory.entries_from(goal="a narrowed goal")
            ),
        ),
    ],
)
async def test_a_broken_database_is_a_tool_result_not_an_exception(
    offline_settings: Settings, tool_name: str, args: object
) -> None:
    """Every memory tool answers a dead database with a `ToolResult`, never a raise.

    The guarantee the whole feature rests on, and the reason these wrappers exist at all: the
    storage modules raise `sqlite3.Error` by design, so if one of these forgot its guard a
    corrupt or locked database would end a fifteen-minute research run. A closed connection is
    the cheapest real failure to inject — `sqlite3.ProgrammingError` is a `sqlite3.Error`, and
    it comes from SQLite rather than from a patched function that only looks like it.

    `save_preparation` is covered by the sibling test below, which needs a valid report.
    """
    connection = db.connect(offline_settings.db_path)
    db.initialize_schema(connection)
    connection.close()
    registry = build_tool_registry(offline_settings, connection=connection)

    result = await registry.call(tool_name, args, RunContext())

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.UNKNOWN
    assert result.error.retryable is False
    assert "memory is unavailable" in result.error.message


async def test_saving_into_a_broken_database_does_not_raise(
    offline_settings: Settings, valid_report_payload: dict[str, object]
) -> None:
    """The write side of the same guarantee, over a validated report.

    Separate from the parameterized case only because it needs a real report; the assertion is
    the same one, and it is the more important half: a save happens *after* a run has already
    produced a valid report, so raising here would throw away work that had entirely succeeded.
    """
    connection = db.connect(offline_settings.db_path)
    db.initialize_schema(connection)
    connection.close()
    registry = build_tool_registry(offline_settings, connection=connection)

    result = await registry.call(
        "save_preparation",
        SavePreparationInput(
            report=FocusPreparationReport.model_validate(valid_report_payload)
        ),
        RunContext(),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.UNKNOWN


async def test_memory_tools_are_never_offered_to_the_model() -> None:
    """None of the three appears on any role's advertised menu.

    Registered is not advertised. Whether a preparation is remembered, and when it is recalled,
    is our code's decision (plan 12.2) — and it is also what makes "an invalid preparation is
    never saved" structural, since a model that could call `save_preparation` could call it on
    a report that never passed validation.
    """
    from evergrove_agent.agents.tool_calling import advertised_tool_names

    memory_tools = {
        "recall_previous_preparation",
        "save_preparation",
        "record_run_memory",
    }
    for role in ("supervisor", "researcher", "appraiser"):
        for has_attachment in (False, True):
            advertised = set(
                advertised_tool_names(role, has_attachment=has_attachment)
            )
            assert advertised.isdisjoint(memory_tools)


def test_the_storage_layer_still_raises(offline_settings: Settings) -> None:
    """The split this suite depends on: storage raises, the tool guards.

    If `prep_memory` ever started swallowing its own errors, every test above would keep
    passing while the guard they exercise had become dead code — and the next storage module
    would copy the wrong stance.
    """
    from evergrove_agent.memory import prep_memory

    connection = db.connect(offline_settings.db_path)
    db.initialize_schema(connection)
    connection.close()

    with pytest.raises(sqlite3.Error):
        prep_memory.recall_previous_preparation(
            connection, task_title="Learn PostgreSQL indexing"
        )
