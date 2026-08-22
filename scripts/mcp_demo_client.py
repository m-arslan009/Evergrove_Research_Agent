"""Drive the MCP server over stdio, end to end (Day 6).

    uv run python scripts/mcp_demo_client.py --offline          # the whole flow, in seconds
    uv run python scripts/mcp_demo_client.py                    # discovery + the last report
    uv run python scripts/mcp_demo_client.py --task "Learn PostgreSQL indexing"

Three paths, one client. `--offline` runs the complete exchange -- discover, call the tool,
read the report back through its resource -- against `scripts/mcp_offline_server.py`, which
is the real server with the model scripted; it needs no model, no network and no waiting,
which is what makes it safe to run in front of an audience. With no flags it lists what the
real server exposes and reads the most recent stored preparation. With `--task` it does the
same complete exchange against the real server, which runs the real agent and takes 9-15
minutes.

Deliberately thin, the same way `show_trace.py` is: it starts a subprocess, makes a handful
of MCP calls and prints them. It imports nothing from `evergrove_agent` -- a client that
reached into the package would be proving the package works, not that the *protocol* does.

Exit codes follow `main.py`: 0 the exchange completed, 1 the server could not be driven,
2 the arguments were unusable, which is argparse's own.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from mcp import Client, MCPError
from mcp.client.stdio import (
    StdioServerParameters,
    get_default_environment,
    stdio_client,
)

REAL_SERVER = [sys.executable, "-m", "evergrove_agent.mcp.server"]
"""The shipped server as a subprocess. The running interpreter rather than
`uv run evergrove-mcp`, so the demo cannot pick up a different environment than the one
driving it."""

OFFLINE_SERVER = [sys.executable, str(Path(__file__).with_name("mcp_offline_server.py"))]
"""The same server with a scripted model. See that file for what is and is not faked."""

MISSING_RUN_ID = "run_does_not_exist"
"""Read at the end of a full exchange, on purpose. A resource that answers *and* refuses is
the only way to show on screen that the server distinguishes "nothing is stored under that
id" from "a preparation that happens to be blank"."""

DISCOVERY_TIMEOUT_S = 30.0
REAL_RUN_TIMEOUT_S = 960.0
"""Above `TOTAL_RUN_TIMEOUT_S` (900), so a run that hits its own deadline reports its own
stop reason instead of being cut off by the client. Set at all because an unset timeout
turns a wedged server into a demo that hangs with nothing on screen."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp_demo_client",
        description="Talk to the Evergrove MCP server over stdio.",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Prepare this task, then read the preparation back. Runs the real agent.",
    )
    parser.add_argument(
        "--minutes", type=int, default=25, help="Session length for --task. Default 25."
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run the whole exchange against the scripted server: no model, no network.",
    )
    parser.add_argument(
        "--server-command",
        nargs="+",
        default=None,
        help="Spawn this instead of the built-in server, e.g. uv run evergrove-mcp.",
    )
    return parser


async def drive(command: list[str], task: str | None, minutes: int, db: Path | None) -> int:
    """One MCP session: connect, discover, and either read or run."""
    env = get_default_environment()
    if db is not None:
        env["DB_PATH"] = str(db)
    parameters = StdioServerParameters(command=command[0], args=command[1:], env=env)
    timeout = REAL_RUN_TIMEOUT_S if task is not None else DISCOVERY_TIMEOUT_S

    print(f"server     : {' '.join(command)}")
    async with Client(stdio_client(parameters), read_timeout_seconds=timeout) as client:
        tools = (await client.list_tools()).tools
        print(f"tools      : {[tool.name for tool in tools]}")
        resources = (await client.list_resources()).resources
        print(f"resources  : {[str(resource.uri) for resource in resources]}")
        templates = (await client.list_resource_templates()).resource_templates
        print(f"templates  : {[template.uri_template for template in templates]}")

        if task is None:
            print("\nno --task given; reading the most recent stored preparation")
            await read(client, "evergrove://task/current")
            return 0

        print(f"\ncalling prepare_focus_session({task!r}); this runs the agent")
        result = await client.call_tool(
            "prepare_focus_session", {"task_title": task, "session_minutes": minutes}
        )
        if result.is_error:
            print(f"the tool reported an error:\n{_text(result.content)}", file=sys.stderr)
            return 1

        report = result.structured_content or json.loads(_text(result.content))
        run_id = report["run_id"]
        print(
            f"report     : run {run_id}, model {report['model_used']}, "
            f"{report['sources_examined']} sources examined"
        )
        print(f"\nreading it back through evergrove://preparation/{run_id}")
        await read(client, f"evergrove://preparation/{run_id}")
        print("\nand one that was never stored, to show the difference")
        await read(client, f"evergrove://preparation/{MISSING_RUN_ID}")
        return 0


async def read(client: Client, uri: str) -> None:
    """Read one resource and print what came back, or why nothing did."""
    try:
        contents = (await client.read_resource(uri)).contents
    except MCPError as exc:
        print(f"  {uri}\n    refused: {exc}")
        return
    for item in contents:
        stored = json.loads(item.text)
        print(f"  {uri}")
        print(f"    objective: {stored['session_objective']}")
        print(f"    topics   : {', '.join(stored['topics_to_cover'])}")


def _text(content: Sequence[object]) -> str:
    """The text blocks of a tool result, joined."""
    return "\n".join(getattr(block, "text", "") for block in content)


def _causes(exc: BaseException) -> list[BaseException]:
    """The real failures inside however many exception groups wrap them.

    `stdio_client` yields from inside an `anyio` task group, so *everything* that escapes the
    session -- a server that will not start, a protocol error, a timeout -- arrives here
    re-raised as a `BaseExceptionGroup`. Printed as-is that is the string "unhandled errors
    in a TaskGroup", which tells an audience watching a demo fail precisely nothing.
    """
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for inner in exc.exceptions for leaf in _causes(inner)]
    return [exc]


def _explain(exc: BaseException, command: list[str]) -> str:
    """One readable line for one failure. Never a traceback."""
    if isinstance(exc, MCPError):
        if exc.code == -32000:  # CONNECTION_CLOSED
            return (
                "the MCP server exited before answering; its own output is above\n"
                f"  server: {' '.join(command)}"
            )
        return f"the server refused the request ({exc.code}): {exc}"
    if isinstance(exc, OSError):
        return f"could not start the MCP server: {' '.join(command)}\n  {exc}"
    if isinstance(exc, TimeoutError):
        return f"the MCP server stopped responding: {' '.join(command)}"
    return f"{type(exc).__name__}: {exc}"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    scratch: tempfile.TemporaryDirectory[str] | None = None
    db: Path | None = None
    command = args.server_command or (OFFLINE_SERVER if args.offline else REAL_SERVER)
    task = args.task
    if args.offline and args.server_command is None:
        scratch = tempfile.TemporaryDirectory(prefix="evergrove-mcp-demo-")
        db = Path(scratch.name) / "demo.sqlite3"
        command = [*command, "--db", str(db)]
        task = task or "Learn PostgreSQL indexing"

    try:
        return asyncio.run(drive(command, task, args.minutes, db))
    except Exception as exc:  # noqa: BLE001 - a demo prints why it failed, never a traceback
        for cause in _causes(exc):
            print(f"could not drive the MCP server: {_explain(cause, command)}", file=sys.stderr)
        return 1
    finally:
        if scratch is not None:
            scratch.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
