"""The model-facing tool bridge: what a model is offered, and what happens when it asks.

Every tool is already proven in its own suite and the registry in `test_tool_registry.py`;
none of that is re-proven here. What this suite protects is the join between them — the two
places a model could get more power than it was given, and the places where its mistakes
have to stay recoverable:

* the menu (a pipeline tool leaking onto it, a tool offered that cannot possibly work);
* the advertisement matching what the registry will actually validate;
* a call to something never offered reaching the registry anyway;
* a bad request arriving as a readable `ToolResult` rather than an exception.

Offline and model-free throughout: no provider is constructed, `respx` is active with no
routes so any HTTP call fails the test, and the search fixture and database live under
`tmp_path`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx

from evergrove_agent.agents import tool_calling
from evergrove_agent.agents.tool_calling import (
    ToolCallOutcome,
    advertise,
    advertised_tool_names,
    dispatch,
    dispatch_all,
    to_tool_spec,
)
from evergrove_agent.config import AgentRole, Settings
from evergrove_agent.llm.base import ToolCall
from evergrove_agent.llm.hosted_provider import to_gemini_schema
from evergrove_agent.schemas import ErrorCode
from evergrove_agent.tools import RunContext, ToolRegistry
from evergrove_agent.tools.wiring import build_tool_registry

ROLES: tuple[AgentRole, ...] = ("supervisor", "researcher", "appraiser")
QUERY = "postgresql b-tree index"
DOCS_URL = "https://www.postgresql.org/docs/current/indexes.html"

# Gemini's `responseSchema` is an OpenAPI 3.0 subset. This is that subset as Google
# documents it, written out here rather than imported: it is Google's contract, not ours,
# and a test that imports the translator's own constant proves only that it agrees with
# itself.
GEMINI_KEYS = frozenset(
    {
        "type",
        "description",
        "enum",
        "items",
        "properties",
        "required",
        "minItems",
        "maxItems",
        "nullable",
        "format",
    }
)


@pytest.fixture
def offline_settings(tmp_path: Path) -> Settings:
    """Defaults pointed at temporary paths — never the real `DB_PATH` or fixture set."""
    return Settings(
        _env_file=None,
        db_path=tmp_path / "agent.sqlite3",
        search_fixture_dir=tmp_path / "search",
    )


def recorded_search(fixture_dir: Path) -> None:
    """One committed-shape recording, so the fixture backend can answer `QUERY`."""
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / "recorded.json").write_text(
        json.dumps(
            {
                "query": QUERY,
                "source_type": "docs",
                "recorded_from": "handwritten",
                "results": [
                    {
                        "url": DOCS_URL,
                        "title": "PostgreSQL: Indexes",
                        "snippet": "An index is a specialised lookup structure.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def recording_registry(settings: Settings) -> tuple[ToolRegistry, list[str]]:
    """A wired registry that also reports every name that reached `call`.

    The list is how a test can prove a refusal happened *before* the registry, which is the
    only difference between a guarded menu and a decorative one.
    """
    registry = build_tool_registry(settings)
    seen: list[str] = []
    inner = registry.call

    async def recording(name, args, ctx):
        seen.append(name)
        return await inner(name, args, ctx)

    registry.call = recording
    return registry, seen


# --- what is on the menu ---------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("has_attachment", [False, True])
def test_normalize_sources_is_never_advertised(
    role: AgentRole, has_attachment: bool
) -> None:
    """`normalize_sources` is registered for the trace, never for the model — `wiring.py`
    deferred that decision to this layer. Catches the pipeline tool arriving on a menu,
    which would invite a model to decide which URLs are the same page."""
    assert "normalize_sources" not in advertised_tool_names(
        role, has_attachment=has_attachment
    )


def test_only_the_researcher_is_given_tools() -> None:
    """Planning, finalising and judging are constrained-decoding calls made with `schema=`.
    Catches a menu handed to one of them — on Ollama that is `format` and `tools` in a
    single payload, which is where a 4B model's reliability goes."""
    assert advertised_tool_names("supervisor") == ()
    assert advertised_tool_names("appraiser", has_attachment=True) == ()
    assert advertised_tool_names("researcher") == ("web_search", "fetch_url")


def test_read_document_is_advertised_only_with_an_attachment() -> None:
    """Catches a menu entry that cannot succeed: with no attachment `read_document` can
    only answer `NOT_FOUND` or `PATH_NOT_ALLOWED`, and every unusable option costs a small
    model attention on every turn of the hop."""
    assert advertised_tool_names("researcher", has_attachment=True) == (
        "web_search",
        "fetch_url",
        "read_document",
    )
    assert "read_document" not in advertised_tool_names("researcher")


def test_a_menu_naming_an_unregistered_tool_fails_at_build_time(
    offline_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `TOOL_NAMES` rule applied to the menu: a name that drifted from the tool it
    points at is a failing assertion, not a capability the agent silently lost. Catches a
    renamed tool leaving the researcher advertising something that no longer exists."""
    monkeypatch.setitem(
        tool_calling._ROLE_TOOLS, "researcher", ("web_search", "summarise")
    )

    with pytest.raises(ValueError, match="summarise"):
        advertise(build_tool_registry(offline_settings), "researcher")


# --- the advertisement matches the implementation ----------------------------------------


def test_a_spec_advertises_exactly_what_the_registry_will_validate(
    offline_settings: Settings,
) -> None:
    """The model must be told the same contract the registry enforces. Catches a hand-shaped
    or pruned schema drifting from `input_model` — the model would then fill in a field that
    comes back as `BAD_ARGUMENTS`, and neither side would be obviously wrong."""
    registry = build_tool_registry(offline_settings)

    specs = advertise(registry, "researcher", has_attachment=True)

    assert [spec.name for spec in specs] == list(
        advertised_tool_names("researcher", has_attachment=True)
    )
    for spec in specs:
        tool = registry.get(spec.name)
        assert tool is not None
        assert spec.description == tool.description
        assert spec.parameters == tool.input_model.model_json_schema()


@pytest.mark.parametrize("name", ["web_search", "fetch_url", "read_document"])
def test_every_advertised_schema_survives_translation_for_the_hosted_provider(
    offline_settings: Settings, name: str
) -> None:
    """The hosted retry is the last rung of S10's ladder, so a schema Gemini rejects fails
    where it is most expensive: live, mid-run, after the local attempts are spent. Catches
    an input model gaining a shape `to_gemini_schema` cannot express."""
    tool = build_tool_registry(offline_settings).get(name)
    assert tool is not None

    translated = to_gemini_schema(to_tool_spec(tool).parameters)

    assert translated["type"] == "object"
    assert set(translated["properties"]) == set(
        tool.input_model.model_json_schema()["properties"]
    )
    assert translated["required"] == tool.input_model.model_json_schema()["required"]

    def assert_supported(node: dict[str, object]) -> None:
        assert set(node) <= GEMINI_KEYS, f"{name}: {set(node) - GEMINI_KEYS}"
        for sub in node.get("properties", {}).values():
            assert_supported(sub)
        if "items" in node:
            assert_supported(node["items"])

    assert_supported(translated)


# --- dispatch ------------------------------------------------------------------------------


@respx.mock
async def test_a_tool_that_was_not_offered_never_reaches_the_registry(
    offline_settings: Settings,
) -> None:
    """The guard this whole module exists for. `normalize_sources` is registered, so without
    the menu check the registry would happily run it. Catches "registered" quietly becoming
    "reachable" the moment a model guesses a name."""
    registry, seen = recording_registry(offline_settings)
    allowed = advertised_tool_names("researcher")

    result = await dispatch(
        ToolCall(name="normalize_sources", arguments={"sources": []}),
        registry=registry,
        ctx=RunContext(),
        allowed=allowed,
    )

    assert seen == []
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.UNKNOWN
    assert result.error.retryable is False
    assert "normalize_sources" in result.error.message
    for name in allowed:
        assert name in result.error.message


@pytest.mark.parametrize(
    ("arguments", "expected_in_message"),
    [
        ({"query": "pg"}, "query"),
        ({"query": QUERY, "sort": "date"}, "sort"),
    ],
)
@respx.mock
async def test_malformed_arguments_come_back_as_a_readable_refusal(
    offline_settings: Settings,
    arguments: dict[str, object],
    expected_in_message: str,
) -> None:
    """A model filling a form in wrong is the ordinary case, not a broken run. Catches an
    argument path that raises out of the loop instead of handing the model the one line it
    needs to correct itself — and confirms the registry stays the only validator, since the
    bridge never builds the input model itself."""
    result = await dispatch(
        ToolCall(name="web_search", arguments=arguments),
        registry=build_tool_registry(offline_settings),
        ctx=RunContext(),
        allowed=advertised_tool_names("researcher"),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.BAD_ARGUMENTS
    assert expected_in_message in result.error.message


@respx.mock
async def test_dispatched_calls_reach_the_real_tools_in_order(
    tmp_path: Path, offline_settings: Settings
) -> None:
    """The assembled path, offline: one call the researcher was given and one it was not,
    answered in the order asked. Catches a dispatcher that side-steps the registry (nothing
    would come back), loses which question produced which result, or runs the turn's calls
    concurrently — which would spend the search quota in an order no trace can reproduce.

    No `respx` routes are registered, so a live fall-through fails the test rather than
    quietly spending the free tier."""
    recorded_search(tmp_path / "search")

    outcomes = await dispatch_all(
        [
            ToolCall(
                name="web_search",
                arguments={"query": QUERY, "source_type": "docs"},
            ),
            ToolCall(name="read_document", arguments={"path": "notes.txt"}),
        ],
        registry=build_tool_registry(offline_settings),
        ctx=RunContext(),
        allowed=advertised_tool_names("researcher"),
    )

    assert [outcome.call.name for outcome in outcomes] == ["web_search", "read_document"]
    assert all(isinstance(outcome, ToolCallOutcome) for outcome in outcomes)

    search, document = outcomes
    assert search.result.ok is True, search.result.error
    assert [source.url for source in search.result.data.results] == [DOCS_URL]
    # Advertised only with an attachment, so without one it is refused before the registry.
    assert document.result.ok is False
    assert document.result.error is not None
    assert document.result.error.code is ErrorCode.UNKNOWN


async def test_a_turn_with_no_tool_calls_is_not_a_failure(
    offline_settings: Settings,
) -> None:
    """A model that answered with prose has not broken anything; deciding what to do about
    it belongs to the research step. Catches a bridge that raises or invents an outcome for
    an empty turn."""
    assert (
        await dispatch_all(
            [],
            registry=build_tool_registry(offline_settings),
            ctx=RunContext(),
            allowed=advertised_tool_names("researcher"),
        )
        == []
    )
