"""The SQLite base layer. Every test runs against a temporary file — never `DB_PATH`."""

from __future__ import annotations

from pathlib import Path

import pytest

from evergrove_agent.config import Settings
from evergrove_agent.memory import db


def _tables(connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row["name"] for row in rows}


def test_initialize_schema_creates_the_database(tmp_path: Path) -> None:
    """A fresh path yields a real file with the expected tables.

    Catches a broken DDL string and a missing parent directory — the configured default
    lives under `data/`, which is not in the repository.
    """
    path = tmp_path / "nested" / "agent.sqlite3"

    with db.open_database(path) as connection:
        assert _tables(connection) >= {"schema_meta"}
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()["value"]
        assert version == str(db.SCHEMA_VERSION)

    assert path.exists()


def test_initialize_schema_is_idempotent_and_keeps_data(tmp_path: Path) -> None:
    """Re-initialising an existing database changes nothing and destroys nothing.

    Catches a future `CREATE TABLE` written without `IF NOT EXISTS`, and any re-init
    that would wipe a populated cache — `open_database` runs this on every open.
    """
    path = tmp_path / "agent.sqlite3"

    with db.open_database(path) as connection:
        with db.transaction(connection) as cursor:
            cursor.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('canary', 'kept')"
            )

    with db.open_database(path) as connection:
        rows = dict(
            connection.execute("SELECT key, value FROM schema_meta").fetchall()
        )

    assert rows["canary"] == "kept"
    assert rows["schema_version"] == str(db.SCHEMA_VERSION)


def test_transaction_rolls_back_a_failed_write(tmp_path: Path) -> None:
    """A body that raises leaves nothing behind, and the error still reaches the caller.

    Catches a half-written cache entry surviving a mid-write failure, and an exception
    swallowed by the context manager.
    """
    path = tmp_path / "agent.sqlite3"

    with db.open_database(path) as connection:
        with pytest.raises(RuntimeError):
            with db.transaction(connection) as cursor:
                cursor.execute(
                    "INSERT INTO schema_meta (key, value) VALUES ('partial', 'x')"
                )
                raise RuntimeError("write failed halfway")

        remaining = connection.execute(
            "SELECT COUNT(*) FROM schema_meta WHERE key = 'partial'"
        ).fetchone()[0]

    assert remaining == 0


def test_connect_defaults_to_the_configured_db_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no argument the path comes from config — catches a reintroduced literal."""
    configured = tmp_path / "configured.sqlite3"
    monkeypatch.setattr(
        db, "get_settings", lambda: Settings(_env_file=None, db_path=configured)
    )

    connection = db.connect()
    try:
        db.initialize_schema(connection)
    finally:
        connection.close()

    assert configured.exists()
