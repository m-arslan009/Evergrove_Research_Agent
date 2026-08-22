"""The MCP client, over a real stdio transport (Day 6).

`test_mcp_server.py` connects to the server *object* in process, which is fast and proves
dispatch — and skips the transport entirely. Everything a stdio deployment can get wrong
lives in the gap: framing a whole report through a pipe, keeping the server's stdout free of
anything but protocol while a run logs and writes SQLite, spawning and reaping a child
process on Windows, and the client script itself, which until now nothing executed.

Both tests run the shipped `scripts/mcp_demo_client.py` as a subprocess, because that
command *is* the deliverable — a test that drove the client's parts but not the command
would leave the demo exactly as unproven as it was. Offline and model-free: the `--offline`
path spawns `scripts/mcp_offline_server.py`, which scripts the model and nothing else.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
CLIENT = SCRIPTS / "mcp_demo_client.py"


def run_client(*args: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    """The demo command, as an operator runs it."""
    return subprocess.run(
        [sys.executable, str(CLIENT), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_an_external_client_completes_the_whole_exchange_over_stdio() -> None:
    """Discover the surface, call the tool, and read the same report back by its run id.

    The one test that proves the requirement end to end over a transport: an outside process
    connects, learns what the server offers, invokes the research capability, receives a
    report, and reads that report again through `evergrove://preparation/{run_id}`. The run
    id is asserted to be the *same* one in both places, which is what makes it a round trip
    rather than two unrelated answers — and it is a real id minted by the run, not the
    placeholder in the scripted payload, so the assertion also pins that bookkeeping still
    overwrites what the model wrote.

    It would catch an SDK API drift, a server whose stdout stopped being protocol-pure, a
    broken spawn, and a demo command that has quietly stopped working. None of those are
    visible to the in-process tests.
    """
    result = run_client("--offline")

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "prepare_focus_session" in result.stdout, "the tool was never discovered"

    run_ids = re.findall(r"run_[0-9a-f]{6}", result.stdout)
    assert run_ids, f"no run id was reported:\n{result.stdout}"
    assert f"evergrove://preparation/{run_ids[0]}" in result.stdout, (
        "the report was not read back through its own resource"
    )
    assert "objective:" in result.stdout, "the resource returned nothing readable"
    assert "no preparation stored for run 'run_does_not_exist'" in result.stdout, (
        "an absent preparation must still be distinguishable from a blank one"
    )


def test_a_server_that_will_not_start_fails_in_one_readable_line() -> None:
    """A demo that fails must say why, in words, without a traceback.

    `stdio_client` yields from inside an anyio task group, so anything escaping the session
    arrives wrapped in an `ExceptionGroup` whose message is "unhandled errors in a
    TaskGroup" — which is what an audience would otherwise see when a server fails to start.
    This pins the unwrapping, and pins that a failure stays an exit code rather than a stack
    dump.
    """
    result = run_client("--server-command", "definitely-not-a-real-mcp-server", timeout=60)

    assert result.returncode == 1
    assert "could not start the MCP server" in result.stderr
    assert "definitely-not-a-real-mcp-server" in result.stderr, "name what failed to start"
    assert "unhandled errors in a TaskGroup" not in result.stderr
    assert "Traceback" not in result.stderr
