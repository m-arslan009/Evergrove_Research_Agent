"""The search cache. Every test runs against a temporary file — never `DB_PATH`."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evergrove_agent.config import Settings
from evergrove_agent.memory import db, search_cache
from evergrove_agent.search import RawSource

QUERY = "postgresql b-tree indexing"
SEARCHED_AT = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
PARAMS = {"backend": "serpapi", "source_type": "docs", "max_results": 6}

RESULTS = [
    RawSource(
        url="https://www.postgresql.org/docs/current/indexes.html",
        title="PostgreSQL: Indexes",
        snippet="Indexes are a common way to enhance database performance.",
        source_backend="serpapi",
    ),
    RawSource(url="https://use-the-index-luke.com/", title="Use The Index, Luke!"),
]


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialised database in a temporary file."""
    with db.open_database(tmp_path / "agent.sqlite3") as conn:
        yield conn


def test_miss_then_store_then_hit(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty cache misses; after a store the entry comes back whole.

    Catches a broken JSON round trip — a `RawSource` field dropped or mangled between write
    and read, which would silently hand `web_search` different sources than the backend
    returned — and a TTL taken from somewhere other than config.
    """
    monkeypatch.setattr(
        search_cache,
        "get_settings",
        lambda: Settings(_env_file=None, search_cache_ttl_days=3),
    )

    assert (
        search_cache.get_cached_search(connection, QUERY, now=SEARCHED_AT, **PARAMS)
        is None
    )

    stored = search_cache.store_cached_search(
        connection, query=QUERY, results=RESULTS, now=SEARCHED_AT, **PARAMS
    )
    hit = search_cache.get_cached_search(
        connection, QUERY, now=SEARCHED_AT, **PARAMS
    )

    assert hit == stored
    assert hit is not None
    assert list(hit.results) == RESULTS
    assert hit.backend == "serpapi"
    assert hit.source_type == "docs"
    assert hit.max_results == 6
    assert hit.searched_at == SEARCHED_AT
    assert hit.expires_at == SEARCHED_AT + timedelta(days=3)


@pytest.mark.parametrize(
    "equivalent",
    [
        "PostgreSQL B-Tree Indexing",
        "  postgresql b-tree indexing  ",
        "postgresql   b-tree\tindexing",
        "postgresql b-tree indexing\n",
    ],
)
def test_equivalent_queries_share_one_entry(
    connection: sqlite3.Connection, equivalent: str
) -> None:
    """Spellings that differ only in case or whitespace hit the same entry.

    Catches a key built from the raw string, which would spend a live search — and a month
    of quota, a few queries at a time — on every re-typing of one question.
    """
    search_cache.store_cached_search(
        connection, query=QUERY, results=RESULTS, now=SEARCHED_AT, **PARAMS
    )

    hit = search_cache.get_cached_search(
        connection, equivalent, now=SEARCHED_AT, **PARAMS
    )

    assert hit is not None
    assert list(hit.results) == RESULTS
    assert connection.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0] == 1


@pytest.mark.parametrize(
    "changed",
    [
        {"backend": "academic"},
        {"source_type": "general"},
        {"max_results": 10},
        {"query": "postgresql gin indexing"},
    ],
)
def test_a_result_changing_parameter_is_a_different_entry(
    connection: sqlite3.Connection, changed: dict[str, object]
) -> None:
    """Changing anything that changes what a backend would return misses.

    Catches false sharing: answering a `general` query with cached `docs` results, or a
    ten-result request with six — a wrong answer served instantly, which is worse than a
    live call.
    """
    search_cache.store_cached_search(
        connection, query=QUERY, results=RESULTS, now=SEARCHED_AT, **PARAMS
    )

    lookup = {"query": QUERY, **PARAMS, **changed}
    query = lookup.pop("query")

    assert (
        search_cache.get_cached_search(connection, query, now=SEARCHED_AT, **lookup)
        is None
    )


@pytest.mark.parametrize(
    ("elapsed", "expect_hit"),
    [
        (timedelta(days=6, hours=23), True),
        (timedelta(days=7, seconds=1), False),
    ],
)
def test_expiry_decides_a_hit_and_a_refresh_replaces_in_place(
    connection: sqlite3.Connection, elapsed: timedelta, expect_hit: bool
) -> None:
    """Past its TTL an entry stops being a hit, and re-searching refreshes it in place.

    Catches stale results served as current, and an `INSERT` that would raise on the
    existing key — the path `web_search` takes every time a cached query expires.
    """
    search_cache.store_cached_search(
        connection,
        query=QUERY,
        results=RESULTS,
        ttl_days=7,
        now=SEARCHED_AT,
        **PARAMS,
    )
    later = SEARCHED_AT + elapsed

    hit = search_cache.get_cached_search(connection, QUERY, now=later, **PARAMS)
    assert (hit is not None) is expect_hit

    refreshed_results = [RawSource(url="https://example.com/newer", title="Newer")]
    search_cache.store_cached_search(
        connection,
        query=QUERY,
        results=refreshed_results,
        ttl_days=7,
        now=later,
        **PARAMS,
    )

    refreshed = search_cache.get_cached_search(connection, QUERY, now=later, **PARAMS)
    assert refreshed is not None
    assert list(refreshed.results) == refreshed_results
    assert connection.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0] == 1
