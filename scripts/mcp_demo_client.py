"""Drive the MCP server over stdio, end to end (Day 6).

    uv run python scripts/mcp_demo_client.py
    uv run python scripts/mcp_demo_client.py --task "Learn PostgreSQL indexing" --minutes 25

With no `--task` it lists what the server exposes and reads the most recent stored
preparation — no model, no network, no waiting, which is what makes it safe to run in front
of an audience before anything else. With `--task` it does the whole round trip: call the
tool, take the `run_id` off the report it returns, and read that preparation back through
`evergrove://preparation/{run_id}`. That second path runs the real agent and needs whatever
provider `.env` selects.

Deliberately thin, the same way `show_trace.py` is: it starts a subprocess, makes four MCP
calls and prints them. It imports nothing from `evergrove_agent` — a client that reached into
the package would be proving the package works, not that the *protocol* does.

Exit codes follow `main.py`: 0 the exchange completed, 1 the server could not be driven,
2 the arguments were unusable, which is argparse's own.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER_COMMAND = [sys.executable, "-m", "evergrove_agent.mcp.server"]
"""The server as a subprocess. The running interpreter rather than `uv run evergrove-mcp`, so
the demo cannot pick up a different environment than the one driving it."""


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
    return parser


async def drive(args: argparse.Namespace) -> int:
    transport = stdio_client(StdioServerParameters(command=SERVER_COMMAND[0], args=SERVER_COMMAND[1:]))
    async with Client(transport) as client:
        tools = (await client.list_tools()).tools
        print(f"tools      : {[tool.name for tool in tools]}")
        resources = (await client.list_resources()).resources
        print(f"resources  : {[str(resource.uri) for resource in resources]}")
        templates = (await client.list_resource_templates()).resource_templates
        print(f"templates  : {[template.uri_template for template in templates]}")

        if args.task is None:
            print("\nno --task given; reading the most recent stored preparation")
            await _read(client, "evergrove://task/current")
            return 0

        print(f"\ncalling prepare_focus_session({args.task!r}); this runs the agent")
        result = await client.call_tool(
            "prepare_focus_session",
            {"task_title": args.task, "session_minutes": args.minutes},
        )
        if result.is_error:
            print(f"the tool reported an error:\n{_text(result.content)}", file=sys.stderr)
            return 1

        report = result.structured_content or json.loads(_text(result.content))
        run_id = report["run_id"]
        print(f"report     : run {run_id}, {report['sources_examined']} sources examined")
        print(f"\nreading it back through evergrove://preparation/{run_id}")
        await _read(client, f"evergrove://preparation/{run_id}")
        return 0


async def _read(client: Client, uri: str) -> None:
    """Read one resource and print what came back, or why nothing did."""
    try:
        contents = (await client.read_resource(uri)).contents
    except Exception as exc:  # noqa: BLE001 - the client raises MCPError for any refusal
        print(f"  {uri} -> {exc}")
        return
    for item in contents:
        stored = json.loads(item.text)
        print(f"  {uri}")
        print(f"    objective: {stored['session_objective']}")
        print(f"    topics   : {', '.join(stored['topics_to_cover'])}")


def _text(content: Sequence[object]) -> str:
    """The text blocks of a tool result, joined."""
    return "\n".join(getattr(block, "text", "") for block in content)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(drive(args))
    except Exception as exc:  # noqa: BLE001 - a demo prints why it failed, never a traceback
        print(f"could not drive the MCP server: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
