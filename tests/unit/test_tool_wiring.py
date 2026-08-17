"""`build_tool_registry`: is every finished tool actually on the menu, and does a call
through the assembled registry reach the real tool?

The individual tools are proven in their own suites; nothing is re-proven here. What this
suite protects is the wiring itself — a finished tool never registered, a name that drifted,
a hook chain assembled in the wrong order, or a factory that quietly returns one shared
registry. The two
end-to-end cases run against a temporary attachment directory and a temporary SQLite file,
with `respx` active and no routes registered, so any HTTP request at all fails the test
rather than leaving the machine.

The tools CLI is tested here too, for the same reason: it is nothing but wiring — flags onto
the input models, one `registry.call`, a formatter — so its failures are wiring failures.
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
from evergrove_agent.schemas import ErrorCode, ToolResult
from evergrove_agent.tools import RunContext
from evergrove_agent.tools.cli import _build_call, build_parser, run
from evergrove_agent.tools.hooks import enforce_run_budget
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


def test_every_wired_registry_enforces_the_budget_and_traces_only_on_request(
    settings: Settings,
) -> None:
    """Two opposite mistakes, one assertion each (Day 4 T2/T3).

    Budget enforcement is not optional: a wired registry that forgot its pre-hook would let
    a run spend without limit, and every caller would have to remember to install it.
    Tracing is the opposite — it needs somewhere to write, so a registry that started
    writing spans without being given a tracer would be tracing by accident.
    """
    untraced = build_tool_registry(settings)

    assert untraced._pre_hooks == [enforce_run_budget]
    assert untraced._post_hooks == []

    traced = build_tool_registry(settings, tracer=object())

    assert len(traced._pre_hooks) == 2
    assert traced._pre_hooks[-1] is enforce_run_budget, (
        "the span must be opened before the budget can short-circuit the call"
    )
    assert len(traced._post_hooks) == 1


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


# --- the tools CLI ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            ["search", QUERY, "--type", "docs", "--max-results", "3"],
            ("web_search", {"query": QUERY, "source_type": "docs", "max_results": 3}),
        ),
        # An option left unset is dropped, never passed as None: the defaults stay on the
        # input model, and `--backend` is settings, not a tool argument.
        (["search", QUERY, "--backend", "fixture"], ("web_search", {"query": QUERY})),
        (
            ["fetch", "https://example.com/page", "--excerpt-for", "what is a b-tree"],
            (
                "fetch_url",
                {"url": "https://example.com/page", "excerpt_for": "what is a b-tree"},
            ),
        ),
        (
            ["read", "notes.txt", "--mode", "section", "--section", "Costs"],
            (
                "read_document",
                {"path": "notes.txt", "mode": "section", "section_hint": "Costs"},
            ),
        ),
        (
            ["normalize", "https://a.dev/x", "https://a.dev/x/"],
            (
                "normalize_sources",
                {
                    "sources": [
                        {"url": "https://a.dev/x", "source_backend": "cli"},
                        {"url": "https://a.dev/x/", "source_backend": "cli"},
                    ]
                },
            ),
        ),
    ],
)
def test_every_cli_flag_reaches_a_field_of_the_tools_own_input_model(
    settings: Settings, argv: list[str], expected: tuple[str, dict[str, object]]
) -> None:
    """Catches a flag whose `dest` drifted away from the field it feeds — a rename that
    breaks nothing at import time and shows up only when a person runs the command. The
    `model_validate` is the second half: `extra="forbid"` on every input model turns a key
    that is no longer a field into a failure here rather than a `BAD_ARGUMENTS` in a
    terminal."""
    name, call_args = _build_call(build_parser().parse_args(argv))

    assert (name, call_args) == expected

    tool = build_tool_registry(settings).get(name)
    assert tool is not None
    tool.input_model.model_validate(call_args)


@respx.mock
async def test_cli_search_answers_from_the_committed_recordings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The documented `search --backend fixture` flow, end to end through the registry.

    Two regressions at once: a CLI that bypasses `build_tool_registry` (nothing would come
    back), and a `--backend` that never reaches `Settings.search_backend` — the settings
    here say `serpapi`, so an override that quietly did nothing would attempt a metered live
    call, which the no-routes `respx` mock turns into a failure instead of a bill."""
    settings = Settings(
        _env_file=None, db_path=tmp_path / "agent.sqlite3", search_backend="serpapi"
    )
    args = build_parser().parse_args(
        ["search", QUERY, "--type", "docs", "--backend", "fixture"]
    )

    code = await run(args, settings=settings)

    assert code == 0
    out = capsys.readouterr().out
    assert "ok  web_search" in out
    assert "https://www.postgresql.org/docs/current/" in out


async def test_cli_prints_a_tool_failure_as_its_code_and_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The contract that makes the CLI usable for debugging: a refusing tool is a readable
    line and exit 1, never a traceback. Catches a formatter that reads `result.data` before
    checking `ok`, which would turn every structured failure into an AttributeError."""
    monkeypatch.setattr(
        "evergrove_agent.documents.reader.get_settings",
        lambda: Settings(_env_file=None, allowed_attachment_dir=tmp_path),
    )
    args = build_parser().parse_args(["read", "missing.md"])

    code = await run(
        args, settings=Settings(_env_file=None, db_path=tmp_path / "a.sqlite3")
    )

    assert code == 1
    captured = capsys.readouterr()
    assert ErrorCode.NOT_FOUND.value in captured.err
    assert "missing.md" in captured.err
    assert "Traceback" not in captured.err + captured.out


async def test_cli_json_prints_the_real_toolresult_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` is what a person falls back to when the summary is not enough, so it has to
    be the envelope itself. Catches a hand-rolled dict drifting from `ToolResult` — at which
    point the CLI would no longer be evidence of what the agent will receive."""
    (tmp_path / "notes.txt").write_text("A B-tree index is sorted.\n", encoding="utf-8")
    monkeypatch.setattr(
        "evergrove_agent.documents.reader.get_settings",
        lambda: Settings(_env_file=None, allowed_attachment_dir=tmp_path),
    )
    args = build_parser().parse_args(["read", "notes.txt", "--json"])

    code = await run(
        args, settings=Settings(_env_file=None, db_path=tmp_path / "a.sqlite3")
    )

    assert code == 0
    result = ToolResult.model_validate_json(capsys.readouterr().out)
    assert result.ok is True
    assert "B-tree index" in result.data["text"]
