"""The source cache. Every test runs against a temporary file — never `DB_PATH`."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evergrove_agent.config import Settings
from evergrove_agent.memory import cache, db

CANONICAL = "https://example.com/guide/indexes"
FETCHED_AT = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialised database in a temporary file."""
    with db.open_database(tmp_path / "agent.sqlite3") as conn:
        yield conn


def test_miss_then_store_then_hit(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty cache misses; after a store the entry comes back whole.

    Catches a broken round trip — a column dropped, mis-ordered or mis-parsed between
    write and read — and a TTL taken from somewhere other than config.
    """
    monkeypatch.setattr(
        cache, "get_settings", lambda: Settings(_env_file=None, cache_ttl_days=3)
    )

    assert cache.get_cached_source(connection, CANONICAL, now=FETCHED_AT) is None

    stored = cache.store_cached_source(
        connection,
        url=CANONICAL,
        text="B-tree indexes are ordered structures.",
        final_url="https://example.com/guide/indexes?v=2",
        title="Indexes",
        content_type="text/html",
        now=FETCHED_AT,
    )

    hit = cache.get_cached_source(connection, CANONICAL, now=FETCHED_AT)

    assert hit == stored
    assert hit is not None
    assert hit.url == CANONICAL
    assert hit.final_url == "https://example.com/guide/indexes?v=2"
    assert hit.title == "Indexes"
    assert hit.text == "B-tree indexes are ordered structures."
    assert hit.content_type == "text/html"
    assert hit.fetched_at == FETCHED_AT
    assert hit.expires_at == FETCHED_AT + timedelta(days=3)


@pytest.mark.parametrize(
    "equivalent",
    [
        "HTTPS://Example.COM/guide/indexes",
        "https://example.com/guide/indexes/",
        "https://example.com/guide/indexes#summary",
        "https://example.com/guide/indexes?utm_source=newsletter",
        "https://example.com:443/guide/indexes",
        "example.com/guide/indexes",
    ],
)
def test_equivalent_urls_share_one_entry(
    connection: sqlite3.Connection, equivalent: str
) -> None:
    """Variants of one page hit the entry stored under its canonical form.

    Catches the lookup being keyed on the raw string instead of the canonical one — the
    bug that would make `fetch_url` re-fetch the same page under every spelling of it.
    """
    cache.store_cached_source(
        connection, url=CANONICAL, text="cached body", now=FETCHED_AT
    )

    hit = cache.get_cached_source(connection, equivalent, now=FETCHED_AT)

    assert hit is not None
    assert hit.text == "cached body"
    assert hit.url == CANONICAL


@pytest.mark.parametrize(
    ("elapsed", "expect_hit"),
    [
        (timedelta(days=6, hours=23), True),
        (timedelta(days=7, seconds=1), False),
    ],
)
def test_expiry_decides_a_hit_and_a_refresh_always_replaces(
    connection: sqlite3.Connection, elapsed: timedelta, expect_hit: bool
) -> None:
    """Past its TTL an entry stops being a hit, and re-storing it refreshes in place.

    Catches stale content served as valid, and an `INSERT` that would raise on the
    existing primary key — the path `fetch_url` takes every time it refreshes a page.
    """
    cache.store_cached_source(
        connection, url=CANONICAL, text="first body", ttl_days=7, now=FETCHED_AT
    )
    later = FETCHED_AT + elapsed

    hit = cache.get_cached_source(connection, CANONICAL, now=later)
    assert (hit is not None) is expect_hit

    cache.store_cached_source(
        connection, url=CANONICAL, text="second body", ttl_days=7, now=later
    )

    refreshed = cache.get_cached_source(connection, CANONICAL, now=later)
    assert refreshed is not None
    assert refreshed.text == "second body"
    assert connection.execute("SELECT COUNT(*) FROM source_cache").fetchone()[0] == 1
