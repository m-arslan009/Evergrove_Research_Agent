"""The MCP server: one tool that prepares a focus session, two resources that read one back.

    uv run evergrove-mcp            # stdio; speaks MCP on stdin/stdout

**A surface, not a second composition root.** Every line below either maps MCP arguments onto
`TaskContext`, awaits `service.prepare_focus_session`, or reads a row back. No registry, no
providers, no budget, no prompt, no retry — the CLI does not assemble those either, and two
surfaces that each did would be two places for a run's composition to drift. The plan's rule
for this file is a size: about sixty lines, and past a hundred logic has leaked in.

**Nothing is re-validated here.** `TaskContext` already declares every bound the CLI relies
on, and the SDK builds the tool's JSON Schema from the type hints and its output schema from
`FocusPreparationReport`, so an argument the model gets wrong is refused by the same rules the
CLI enforces. A second copy of those limits in this file would be a second place for them to
drift — the reason `tools/cli.py` gives for not restating them either.

**Failures travel out, with one thing added.** `LLMError` is not caught at all: the SDK
renders it as a tool error carrying its message, which is what the CLI prints too.
`PreparationFailed` is caught for exactly one reason — to put its `run_id` in the text. The
run id is an *attribute* of that exception and not part of its message (see
`agents/supervisor.py`), so a client that only ever sees the message would have no way to
reach `scripts/show_trace.py` for the run that just failed. The CLI can afford this because it
prints the run id in its progress line while the run is in flight; an MCP client is handed one
result and nothing else. Nothing is re-wrapped beyond that: the original message travels
verbatim and `PreparationFailed.issues` is still on the exception this one is chained from.

**stdout belongs to the protocol.** A stdio server speaks MCP on stdout, so nothing here ever
prints; the SDK's own diagnostics go to stderr, the same discipline `main.enable_trace_log`
already follows.

The service function is imported under an alias. The tool below must be *named*
`prepare_focus_session` — that name is the published MCP tool identifier — and a nested
function of that name would shadow the import and call itself.
"""

from __future__ import annotations

from mcp import MCPError
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import INVALID_PARAMS

from evergrove_agent.agents.single_agent import PreparationFailed
from evergrove_agent.config import Settings, get_settings
from evergrove_agent.memory import report_store
from evergrove_agent.memory.db import open_database
from evergrove_agent.schemas import FocusPreparationReport, TaskContext
from evergrove_agent.service import prepare_focus_session as run_preparation

SERVER_NAME = "evergrove-research-agent"
PREPARATION_URI = "evergrove://preparation/{run_id}"
CURRENT_URI = "evergrove://task/current"


def build_server(settings: Settings | None = None) -> MCPServer:
    """The configured server, built but not running.

    Separate from `main` for the reason `build_parser` is separate in `main.py` and
    `tools/cli.py`: a test needs the object, not a transport. `MCPServer` can list and
    dispatch its own tools and resources directly, so the whole surface is reachable in
    process with no stdio and no subprocess.

    `settings` is resolved once here and closed over by all three handlers, so a caller that
    overrides it — a test pointing `DB_PATH` at a temporary file — reaches every one of them.
    """
    settings = settings or get_settings()
    mcp = MCPServer(name=SERVER_NAME, version="0.1.0")

    @mcp.tool()
    async def prepare_focus_session(
        task_title: str,
        session_minutes: int = 25,
        task_description: str | None = None,
        attachment_path: str | None = None,
    ) -> FocusPreparationReport:
        """Research a task and return a validated focus preparation.

        Runs the full agent: interpret the task, search, read sources, judge them, and write a
        report whose every citation the run actually opened. The report's `run_id` is the key
        to `evergrove://preparation/{run_id}`.
        """
        task = TaskContext(
            task_title=task_title,
            session_minutes=session_minutes,
            task_description=task_description,
            attachment_path=attachment_path,
        )
        try:
            return await run_preparation(task, settings=settings)
        except PreparationFailed as exc:
            raise ToolError(f"{exc} (run {exc.run_id})") from exc

    @mcp.resource(PREPARATION_URI, mime_type="application/json")
    def preparation(run_id: str) -> str:
        """One stored focus preparation, as JSON."""
        with open_database(settings.db_path) as connection:
            return _json(report_store.get_report(connection, run_id), f"run {run_id!r}")

    @mcp.resource(CURRENT_URI, mime_type="application/json")
    def current_task() -> str:
        """The most recently prepared focus session, as JSON."""
        with open_database(settings.db_path) as connection:
            return _json(report_store.latest_report(connection), "the most recent run")

    return mcp


def _json(report: FocusPreparationReport | None, subject: str) -> str:
    """The report as JSON, or a not-found naming what was looked for.

    Raised rather than returned as an empty body: a client must be able to tell "nothing is
    stored under that id" from a preparation that happens to be blank. A missing report is a
    normal answer — `report_store` returns `None` both for a run that never existed and for a
    row an older build wrote — so both are reported the same way, as absent.

    **`MCPError` specifically, and not the SDK's own `ResourceNotFoundError`.** The two read
    paths guard differently: a template re-raises `(ResourceError, MCPError)` untouched, while
    a static resource re-raises only `MCPError` and replaces anything else with "Error reading
    resource <uri>". `MCPError` is the one type that survives both, so
    `evergrove://preparation/{run_id}` and `evergrove://task/current` fail with the same
    message instead of one of them losing it. Verified against mcp 2.0.0, not assumed.
    """
    if report is None:
        raise MCPError(INVALID_PARAMS, f"no preparation stored for {subject}")
    return report.model_dump_json()


def main() -> int:
    """Serve over stdio until the client disconnects."""
    build_server().run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
