"""The real MCP server, with the model scripted, so a full round trip takes seconds.

    uv run python scripts/mcp_offline_server.py --db /tmp/demo.sqlite3

A real run costs 9-15 minutes on this hardware, which is neither a demo nor a test. This
serves the **same** `build_server(settings)` surface over the **same** stdio transport, and
substitutes exactly one thing: `AgentProviders.from_settings`, the seam
`tests/integration/test_mcp_server.py` already uses. Everything else is the shipped path --
`service.prepare_focus_session`, the wired tool registry, the fixture search backend,
tracing, `validate_report`'s grounding check, and the `prep_report` write the resources read
back.

**Only the model is faked, and the report says so.** `model_used` comes out as
`fake-model` because `_apply_bookkeeping` stamps the provider's real name, so nothing that
reads this run can mistake it for a researched one. `resources` is empty and
`sources_examined` is 0 for a structural reason, not a shortcut: the scripted plan finalises
without opening a page, and S9's grounding check rejects a citation the run never fetched.

This proves the **protocol**, never the research. It belongs in `scripts/` for the reason
`show_trace.py` does -- an operator entry point, `main()`-shaped, with nothing importable in
it -- and it lives here rather than under `tests/` so that the shipped demo and the test that
guards the shipped demo drive one launcher instead of two copies that drift.

`--db` is an argument rather than an environment variable because `stdio_client` passes only
`get_default_environment()`'s dozen-odd Windows names to a server it spawns; `DB_PATH` is not
among them. `Settings(_env_file=None)` for the same kind of reason: a developer's `.env`
must not be able to point a run that claims to be offline at a live search backend.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Sequence
from pathlib import Path

from evergrove_agent import service
from evergrove_agent.agents import AgentProviders
from evergrove_agent.config import Settings
from evergrove_agent.llm.fake_provider import FakeProvider
from evergrove_agent.mcp.server import build_server

DEFAULT_DB = Path(tempfile.gettempdir()) / "evergrove-mcp-offline.sqlite3"

PLAN = {
    "action": "FINALISE",
    "research_question": None,
    "source_preference": "docs",
    "reasoning": "Scripted offline demo: finalise without a research hop.",
}
"""The Supervisor's one decision. FINALISE immediately, so the run makes no search and no
fetch -- the two things that would need a network."""

REPORT = {
    "run_id": "run_scripted",
    "generated_at": "2026-01-01T00:00:00Z",
    "model_used": "scripted",
    "original_task": "scripted",
    "session_duration_minutes": 25,
    "interpreted_goal": "Understand B-tree indexes well enough to read EXPLAIN output",
    "session_objective": "Read EXPLAIN and tell an index scan from a sequential scan",
    "topics_to_cover": ["What an index is", "B-tree basics", "Reading EXPLAIN"],
    "topics_to_skip": ["GIN", "GiST", "BRIN"],
    "resources": [],
    "practice": {
        "instruction": "Create a table with 10k rows, run EXPLAIN on a filtered SELECT",
        "expected_outcome": "You can point at the line that says Index Scan",
    },
    "success_criteria": "You can explain why one query used the index and another did not",
    "assumptions": ["The user has psql and a local database"],
    "unknowns": ["Which PostgreSQL version the user runs"],
    "hops_used": 0,
    "sources_examined": 0,
}
"""The report the scripted model 'writes'. The first six fields are overwritten by
`_apply_bookkeeping` with the run's real provenance, so their values here are placeholders
that never reach a client."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp_offline_server",
        description="The Evergrove MCP server over stdio, with a scripted model.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite file this server reads and writes. Default {DEFAULT_DB}.",
    )
    return parser


def install_scripted_model() -> None:
    """Replace the provider factory with one that hands every role the same script.

    A **fresh** `FakeProvider` per call, deliberately: the script is consumed by one run, and
    a shared instance would answer the first `prepare_focus_session` and raise
    `ExhaustedScript` on the second -- turning a demo that gets called twice into a failure
    that looks like the server broke.
    """

    def scripted(cls: type[AgentProviders], settings: Settings | None = None) -> AgentProviders:
        provider = FakeProvider([json.dumps(PLAN), json.dumps(REPORT)])
        return AgentProviders(provider, provider, provider)

    service.AgentProviders.from_settings = classmethod(scripted)  # type: ignore[method-assign]


def main(argv: Sequence[str] | None = None) -> int:
    """Serve over stdio until the client disconnects."""
    args = build_parser().parse_args(argv)
    install_scripted_model()
    build_server(Settings(_env_file=None, db_path=args.db)).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
