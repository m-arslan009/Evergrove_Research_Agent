"""The MCP surface, driven by a real client (Day 6).

Offline and model-free, the same way `test_service.py` is: `FakeProvider` is injected in
place of the composed providers, the registry is the real wired one with
`SEARCH_BACKEND=fixture`, and every test points `DB_PATH` at a temporary file. No transport
either — `Client` connects to an `MCPServer` object in process, so these tests exercise
protocol dispatch without a subprocess. `tests/integration/test_mcp_client.py` is what proves
stdio, by running the shipped demo client against a real server subprocess.

What is deliberately not tested here: that the agent researches correctly (the loop suites own
that), that `TaskContext` enforces its own bounds (Pydantic's job, and `test_schemas.py`
already pins the rules), and that the SDK generates a JSON Schema from type hints. What *is*
tested is everything that could silently go wrong between an MCP client and a stored report.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from evergrove_agent import service
from evergrove_agent.agents import AgentProviders
from evergrove_agent.agents.single_agent import PreparationFailed
from evergrove_agent.config import Settings
from evergrove_agent.llm.fake_provider import FakeProvider
from evergrove_agent.mcp import server
from evergrove_agent.mcp.server import CURRENT_URI, PREPARATION_URI, build_server
from evergrove_agent.schemas import FocusPreparationReport

TASK = "Learn PostgreSQL indexing"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Defaults, but pointed at a temporary database — overrides `conftest.settings`."""
    return Settings(_env_file=None, db_path=tmp_path / "agent.sqlite3")


@pytest.fixture
def offline_run(
    monkeypatch: pytest.MonkeyPatch, valid_report_payload: dict[str, Any]
) -> Iterator[FakeProvider]:
    """Make `prepare_focus_session` composable offline, and hand back the fake provider.

    The MCP tool passes only `settings` to the service, because a surface has no business
    assembling a run — which leaves `AgentProviders.from_settings` as the seam. Substituting
    it is what keeps the *whole real path* under test: the service still builds the registry,
    still opens the database, still traces, and still saves the preparation. Patching the
    service function itself would have tested nothing but the argument mapping.

    The script is a planner turn that finalises immediately, then the report. `resources` is
    emptied because the shared payload cites a page this run never opened, and grounding
    would reject it.
    """
    provider = FakeProvider(
        [
            json.dumps(
                {
                    "action": "FINALISE",
                    "research_question": None,
                    "source_preference": "docs",
                    "reasoning": "because",
                }
            ),
            json.dumps({**valid_report_payload, "resources": []}),
        ]
    )
    monkeypatch.setattr(
        service.AgentProviders,
        "from_settings",
        classmethod(lambda cls, settings=None: AgentProviders(provider, provider, provider)),
    )
    yield provider


async def test_the_server_publishes_one_tool_and_both_resources(settings: Settings) -> None:
    """The surface a client discovers is the one the plan specifies.

    A decorator that failed to register, a renamed tool, or a mistyped URI would leave the
    server running and answering — and invisible to every client, because discovery is the
    only way a client learns any of these exist. The tool's schema is asserted against
    `TaskContext`'s field set for the same reason the surface does not restate its bounds: the
    two are supposed to be one definition, and this is what would catch them parting.
    """
    async with Client(build_server(settings)) as client:
        tools = (await client.list_tools()).tools
        resources = (await client.list_resources()).resources
        templates = (await client.list_resource_templates()).resource_templates

    assert [tool.name for tool in tools] == ["prepare_focus_session"]
    assert [str(resource.uri) for resource in resources] == [CURRENT_URI]
    assert [template.uri_template for template in templates] == [PREPARATION_URI]

    schema = tools[0].input_schema
    assert set(schema["properties"]) == {
        "task_title",
        "session_minutes",
        "task_description",
        "attachment_path",
    }
    assert schema["required"] == ["task_title"], "only the task itself is mandatory"


async def test_a_prepared_session_is_readable_through_both_resources(
    settings: Settings, offline_run: FakeProvider
) -> None:
    """Call the tool, then read the same report back by run id and as the current task.

    This is the whole point of the feature and the only test that proves the chain end to
    end: MCP dispatch, argument mapping onto `TaskContext`, a real run, the report reaching
    `prep_report`, and both resources finding it. It is also the regression guard for the
    persistence change — before it, `save_preparation` stored a lossy summary and there was
    nothing for a resource to return, so a broken write here would silently reduce the
    resource to a permanent not-found.
    """
    async with Client(build_server(settings)) as client:
        result = await client.call_tool(
            "prepare_focus_session", {"task_title": TASK, "session_minutes": 25}
        )
        assert not result.is_error, result.content
        returned = FocusPreparationReport.model_validate(result.structured_content)

        by_run = (await client.read_resource(f"evergrove://preparation/{returned.run_id}")).contents
        current = (await client.read_resource(CURRENT_URI)).contents

    stored = FocusPreparationReport.model_validate_json(by_run[0].text)
    assert stored == returned, "the resource must return the report the tool produced"
    assert stored.original_task == TASK, "bookkeeping, not something the model chose"
    assert FocusPreparationReport.model_validate_json(current[0].text) == returned
    assert offline_run.remaining == 0, "the planner and the report, in that order"


async def test_a_failed_run_is_an_error_carrying_the_run_id(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """A run that produced no valid report is an MCP error, not a half-answer.

    The failure has to survive the surface with its message intact: `PreparationFailed`
    already names the run id and the attempts, and that is the only way a caller can go and
    read the trace. A surface that swallowed it — returning an empty report, or an error
    stripped down to "tool failed" — would make a failed run indistinguishable from a
    successful one that found nothing.
    """

    async def refuse(*_: Any, **__: Any) -> FocusPreparationReport:
        raise PreparationFailed("no valid report after 3 attempts", run_id="run_abc123")

    monkeypatch.setattr(server, "run_preparation", refuse)

    async with Client(build_server(settings)) as client:
        result = await client.call_tool("prepare_focus_session", {"task_title": TASK})

    assert result.is_error
    message = "\n".join(block.text for block in result.content)
    assert "no valid report after 3 attempts" in message
    assert "run_abc123" in message, "without the run id the trace cannot be found"


@pytest.mark.parametrize(
    ("uri", "subject"),
    [
        ("evergrove://preparation/run_missing", "run 'run_missing'"),
        (CURRENT_URI, "the most recent run"),
    ],
    ids=["by run id", "current task"],
)
async def test_reading_a_preparation_that_was_never_stored_says_so(
    settings: Settings, uri: str, subject: str
) -> None:
    """Both resources report an absent report the same way, and name what was looked for.

    Parameterized because the two are asymmetric inside the SDK — a template re-raises
    `ResourceError` untouched while a static resource replaces it — so the same code is not
    guaranteed to behave the same way on both paths. That asymmetry is exactly what silently
    reduces one of them to "Error reading resource", and it is why the server raises
    `MCPError` rather than the SDK's own not-found type.
    """
    async with Client(build_server(settings)) as client:
        with pytest.raises(Exception, match=f"no preparation stored for {subject}"):
            await client.read_resource(uri)
