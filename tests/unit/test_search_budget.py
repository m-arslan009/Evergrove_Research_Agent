"""The monthly live-search guard. Temporary database file, no network, no provider."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evergrove_agent.config import Settings
from evergrove_agent.memory import budget, db, search_cache
from evergrove_agent.search import RawSource

AUGUST = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
SEPTEMBER = datetime(2026, 9, 1, 0, 5, tzinfo=UTC)


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialised database in a temporary file."""
    with db.open_database(tmp_path / "agent.sqlite3") as conn:
        yield conn


@pytest.fixture
def budget_of_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """A two-call monthly ceiling, supplied the way production supplies it — config."""
    monkeypatch.setattr(
        budget,
        "get_settings",
        lambda: Settings(_env_file=None, monthly_search_budget=2),
    )


@pytest.mark.usefixtures("budget_of_two")
def test_increments_then_refuses_at_the_configured_limit(
    connection: sqlite3.Connection,
) -> None:
    """Each grant costs one, and the call past the ceiling is refused.

    Catches an off-by-one that either wastes the last search or lets one through past the
    tier, and a limit read from anywhere but config — the bug where a hardcoded 200 or 250
    quietly ignores `MONTHLY_SEARCH_BUDGET`.
    """
    assert budget.get_search_usage(connection, now=AUGUST) == 0

    first = budget.reserve_search_call(connection, now=AUGUST)
    second = budget.reserve_search_call(connection, now=AUGUST)
    third = budget.reserve_search_call(connection, now=AUGUST)

    assert (first.granted, first.used) == (True, 1)
    assert (second.granted, second.used) == (True, 2)
    assert (third.granted, third.used, third.limit) == (False, 2, 2)
    assert third.month == "2026-08"
    assert budget.get_search_usage(connection, now=AUGUST) == 2


def test_a_zero_budget_refuses_the_first_call_of_a_month(
    connection: sqlite3.Connection,
) -> None:
    """A ceiling of zero grants nothing, even with no row for the month yet.

    Catches the specific hole in the atomic upsert: its `WHERE used < limit` guards only
    the update branch, so a fresh month would otherwise slip one live call past a budget
    that was explicitly set to zero.
    """
    reservation = budget.reserve_search_call(connection, limit=0, now=AUGUST)

    assert reservation.granted is False
    assert budget.get_search_usage(connection, now=AUGUST) == 0


@pytest.mark.usefixtures("budget_of_two")
def test_a_new_month_starts_a_fresh_budget(connection: sqlite3.Connection) -> None:
    """August being exhausted says nothing about September.

    Catches a counter that is global rather than per calendar month — the bug that stops
    all searching for the rest of the project once one month runs out.
    """
    budget.reserve_search_call(connection, now=AUGUST)
    budget.reserve_search_call(connection, now=AUGUST)
    assert budget.reserve_search_call(connection, now=AUGUST).granted is False

    september = budget.reserve_search_call(connection, now=SEPTEMBER)

    assert (september.granted, september.used, september.month) == (True, 1, "2026-09")
    assert budget.get_search_usage(connection, now=AUGUST) == 2


@pytest.mark.parametrize(
    ("backend", "spends"),
    [("serpapi", True), ("fixture", False), ("academic", False), ("ddgs", False)],
)
def test_only_metered_backends_spend_the_budget(backend: str, spends: bool) -> None:
    """Only SerpAPI is charged; the offline and unmetered backends are free.

    Catches the regression that makes the whole fixture-by-default strategy pointless — a
    test suite or an offline development run burning the live monthly tier.
    """
    assert budget.consumes_quota(backend) is spends


@pytest.mark.usefixtures("budget_of_two")
def test_a_cache_hit_costs_no_budget(connection: sqlite3.Connection) -> None:
    """Answering from the cache leaves the month's usage untouched.

    Walks the order `web_search` will follow — look up, reserve only on a miss — because
    the reverse order is the expensive bug: a cache that works but still spends a search
    saves nothing at all.
    """
    params = {"backend": "serpapi", "source_type": "docs", "max_results": 6}
    results = [RawSource(url="https://www.postgresql.org/docs/current/indexes.html")]

    def search_once(query: str) -> bool:
        """Returns whether this query needed a live call."""
        if search_cache.get_cached_search(connection, query, now=AUGUST, **params):
            return False
        assert budget.reserve_search_call(connection, now=AUGUST).granted
        search_cache.store_cached_search(
            connection, query=query, results=results, now=AUGUST, **params
        )
        return True

    assert search_once("postgresql indexing") is True
    assert budget.get_search_usage(connection, now=AUGUST) == 1

    assert search_once("PostgreSQL  Indexing") is False
    assert budget.get_search_usage(connection, now=AUGUST) == 1
