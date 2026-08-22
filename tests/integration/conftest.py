"""The fixtures an offline integration run needs, shared by every suite in this directory.

Only what has to be a *fixture* lives here — a temporary workspace, a second handle on the
database it writes, and the report a scripted model returns. Everything else about driving one
of these runs is plain functions in `offline_run.py`, which a test imports by name; see that
module's docstring for why the split is where it is.

Extracted from `test_multi_agent.py` when `test_mcp_server.py` needed the same workspace: the
trace a run records can only be compared across two surfaces if both surfaces write to one
database, and `ledger` is the handle that reads it back.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from evergrove_agent.config import Settings
from evergrove_agent.memory import db


@pytest.fixture
def workspace(tmp_path: Path) -> Settings:
    """Committed fixtures for input, a temporary file for state — as `test_single_loop.py`.

    Only `DB_PATH` moves. `SEARCH_BACKEND` and `SEARCH_FIXTURE_DIR` stay exactly as a fresh
    clone has them, because "the shipped defaults run offline" is part of what is being
    accepted here.
    """
    return Settings(_env_file=None, db_path=tmp_path / "agent.sqlite3")


@pytest.fixture
def ledger(workspace: Settings) -> Iterator[sqlite3.Connection]:
    """A second handle on the run's database, for reading spans and the search counter back."""
    with db.open_database(workspace.db_path) as connection:
        yield connection


@pytest.fixture
def report(valid_report_payload: dict[str, Any]):
    """A report a model could plausibly return, citing nothing unless a test says otherwise.

    The shared payload cites a real page these runs do open, but grounding is not these suites'
    subject and an unopened citation would fail a run for a reason none of these tests is
    about. Citations are opted into.
    """

    def _report(**overrides: Any) -> str:
        return json.dumps({**valid_report_payload, "resources": [], **overrides})

    return _report
