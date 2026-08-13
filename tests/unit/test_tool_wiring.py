"""`build_tool_registry`: is every finished tool actually on the menu, and does a call
through the assembled registry reach the real tool?

The individual tools are proven in their own suites; nothing is re-proven here. What this
suite protects is the wiring itself — a finished tool never registered, a name that drifted,
a hook slipped in early, or a factory that quietly returns one shared registry. The two
end-to-end cases run against a temporary attachment directory and a temporary SQLite file,
with `respx` active and no routes registered, so any HTTP request at all fails the test
rather than leaving the machine.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
import respx

from evergrove_agent.config import Settings
from evergrove_agent.memory import db
from evergrove_agent.schemas import ErrorCode
from evergrove_agent.tools import RunContext
from evergrove_agent.tools.read_document import ReadDocumentTool
from evergrove_agent.tools.wiring import TOOL_NAMES, build_tool_registry

QUERY = "postgresql b-tree index"


@pytest.fixture
def wired_settings(tmp_path: Path) -> Settings:
    """Defaults, pointed at temporary paths — never the real `DB_PATH` or fixture dir."""
    return Settings(
        _env_file=None,
        db_path=tmp_path / "agent.sqlite3",
        search_fixture_dir=tmp_path / "search",
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialised cache and budget ledger in a temporary file."""
    with db.open_database(tmp_path / "agent.sqlite3") as conn:
        yield conn


# --- what is on the menu -------------------------------------------------------------------


def test_every_finished_tool_is_registered_under_its_own_name(
    settings: Settings,
) -> None:
    """Catches the failure this module exists to prevent: a completed tool that was never
    wired, or a `TOOL_NAMES` entry that drifted from the tool's own `name` — either of
    which the agent would experience as a capability that silently does not exist."""
    registry = build_tool_registry(settings)

    assert registry.names == TOOL_NAMES
    assert len(registry) == len(TOOL_NAMES)
    for name in TOOL_NAMES:
        tool = registry.get(name)
        assert tool is not None
        assert tool.name == name


def test_each_build_is_a_fresh_independent_registry(settings: Settings) -> None:
    """Catches a module-level singleton. Registering a duplicate name raises by design, so
    a shared registry would make the second build of a process fail for no reason, and one
    caller's registry would see another's tools."""
    first = build_tool_registry(settings)
    second = build_tool_registry(settings)

    assert first is not second
    assert first.names == second.names == TOOL_NAMES
    assert first.get("web_search") is not second.get("web_search")


def test_wiring_installs_no_hooks(settings: Settings) -> None:
    """The pre/post extension points belong to the tracing capability. Catches a hook
    installed early, which would change every tool's behaviour from the composition root
    rather than from the code that owns the concern."""
    registry = build_tool_registry(settings)

    assert registry._pre_hooks == []
    assert registry._post_hooks == []


# --- the registry contracts, over the real tool set ------------------------------------------


def test_a_second_registration_of_a_wired_name_is_refused(settings: Settings) -> None:
    """Duplicate names raise at wiring time (the S1 contract). Catches a future second
    composition root registering the same tools into an already-built registry."""
    registry = build_tool_registry(settings)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(ReadDocumentTool())

    assert registry.names == TOOL_NAMES


async def test_an_unknown_tool_reports_the_wired_menu(settings: Settings) -> None:
    """A miss must be a readable `ToolResult`, not an exception, and must name what *is*
    available — that sentence is how a model recovers from a mistyped tool name."""
    result = await build_tool_registry(settings).call("summarise", {}, RunContext())

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.UNKNOWN
    for name in TOOL_NAMES:
        assert name in result.error.message


# --- the assembled path reaches the real tools -----------------------------------------------


@respx.mock
async def test_read_document_through_the_wired_registry_reads_a_real_file(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves the registered instance is the real tool, not a placeholder: a genuine file
    is parsed and the registry stamps the call. `read_document` takes no settings of its
    own, so the attachment directory is pointed at `tmp_path` where the reader reads it."""
    (tmp_path / "notes.txt").write_text(
        "A B-tree index stores keys in sorted order.\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "evergrove_agent.documents.reader.get_settings",
        lambda: Settings(_env_file=None, allowed_attachment_dir=tmp_path),
    )

    result = await build_tool_registry(settings).call(
        "read_document", {"path": "notes.txt"}, RunContext()
    )

    assert result.ok is True, result.error
    assert "B-tree index" in result.data.text
    assert result.duration_ms >= 0


@respx.mock
async def test_web_search_through_the_wired_registry_stays_offline(
    tmp_path: Path, wired_settings: Settings, connection: sqlite3.Connection
) -> None:
    """The assembled registry must be as offline as the committed default promises. No
    routes are registered, so a live fall-through raises instead of quietly spending the
    free tier — the one wiring mistake that costs real money."""
    fixture_dir = tmp_path / "search"
    fixture_dir.mkdir()
    (fixture_dir / "recorded.json").write_text(
        json.dumps(
            {
                "query": QUERY,
                "source_type": "docs",
                "recorded_from": "handwritten",
                "results": [
                    {
                        "url": "https://www.postgresql.org/docs/current/indexes.html",
                        "title": "PostgreSQL: Indexes",
                        "snippet": "An index is a specialised lookup structure.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = build_tool_registry(wired_settings, connection=connection)

    result = await registry.call(
        "web_search", {"query": QUERY, "source_type": "docs"}, RunContext()
    )

    assert result.ok is True, result.error
    assert [source.url for source in result.data.results] == [
        "https://www.postgresql.org/docs/current/indexes.html"
    ]
