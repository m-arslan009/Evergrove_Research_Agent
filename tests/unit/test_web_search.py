"""`web_search`: is a repeated search free, is a live one counted, and does every failure
come back as a code instead of an exception?

Nothing here reaches the network or spends quota. The backend is a recording stub, the
cache and the budget ledger live in a SQLite file under `tmp_path` — never `DB_PATH` — and
the one end-to-end case runs under `respx` with no routes registered, so any HTTP request
at all would fail the test rather than leave the machine. Every call goes through
`ToolRegistry`, the only sanctioned way to reach a tool.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import respx

from evergrove_agent.config import Settings
from evergrove_agent.memory import (
    db,
    get_cached_search,
    get_search_usage,
    store_cached_search,
)
from evergrove_agent.schemas import ErrorCode, ToolResult
from evergrove_agent.search import RawSource, SearchBackendError, SearchSourceType
from evergrove_agent.tools import RunContext, ToolRegistry
from evergrove_agent.tools import web_search as web_search_module
from evergrove_agent.tools.web_search import WebSearchTool, _select_backend

QUERY = "postgresql b-tree index"

DOCS = RawSource(
    url="https://www.postgresql.org/docs/current/indexes.html?utm_source=newsletter",
    title="PostgreSQL: Indexes",
    snippet="An index is a specialised lookup structure.",
    source_backend="serpapi",
)
DOCS_AGAIN = RawSource(
    url="https://www.postgresql.org/docs/current/indexes.html#btree",
    title="",
    snippet="",
    source_backend="serpapi",
)
"""The same page as `DOCS` once the tracking parameter and the fragment are gone — what
makes it possible to prove deduplication happens before the results are returned."""

BLOG = RawSource(
    url="https://medium.com/@someone/postgres-indexes",
    title="Postgres indexes explained",
    snippet="A blog post.",
    source_backend="serpapi",
)


class StubBackend:
    """A backend that records what it was asked and replays queued outcomes.

    Each call takes the next outcome, and the last one repeats — so a single list answers
    both "fails once then succeeds" and "always fails". An outcome that is a
    `SearchBackendError` is raised, exactly as a real backend raises it.
    """

    def __init__(
        self, name: str, *outcomes: list[RawSource] | SearchBackendError
    ) -> None:
        self.name = name
        self.calls: list[tuple[str, str, int]] = []
        self._outcomes: list[Any] = list(outcomes) or [[]]

    async def search(
        self, query: str, *, source_type: SearchSourceType, max_results: int
    ) -> list[RawSource]:
        self.calls.append((query, source_type, max_results))
        outcome = self._outcomes[min(len(self.calls) - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, SearchBackendError):
            raise outcome
        return list(outcome)[:max_results]


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialised cache and budget ledger in a temporary file."""
    with db.open_database(tmp_path / "agent.sqlite3") as conn:
        yield conn


def registry_for(
    settings: Settings,
    connection: sqlite3.Connection,
    backend: StubBackend | None = None,
) -> ToolRegistry:
    """The tool behind the registry, pointed at the temporary database."""
    registry = ToolRegistry()
    registry.register(WebSearchTool(settings, backend=backend, connection=connection))
    return registry


async def search(registry: ToolRegistry, **kwargs: Any) -> ToolResult[Any]:
    """One `web_search` call, arguments validated by the registry as a model's would be."""
    return await registry.call("web_search", kwargs, RunContext())


def cached(
    connection: sqlite3.Connection,
    *,
    backend: str,
    source_type: SearchSourceType,
    max_results: int = 6,
) -> Any:
    """The cache row `QUERY` would be answered from, or `None`."""
    return get_cached_search(
        connection,
        QUERY,
        backend=backend,
        source_type=source_type,
        max_results=max_results,
    )


# --- the cache ------------------------------------------------------------------------------


async def test_cache_hit_reaches_neither_the_backend_nor_the_budget(
    settings: Settings, connection: sqlite3.Connection
) -> None:
    """A repeated search must cost nothing.

    Catches the regression that matters most here: a refactor that checks the budget or
    calls the backend before reading the cache would spend a live search — and the quota
    is 250 a month — for an answer already on disk.
    """
    store_cached_search(
        connection,
        query=QUERY,
        backend="serpapi",
        source_type="docs",
        max_results=6,
        results=[DOCS],
        ttl_days=7,
    )
    backend = StubBackend("serpapi", [BLOG])
    registry = registry_for(settings, connection, backend)

    result = await search(registry, query=QUERY, source_type="docs", max_results=6)

    assert result.ok
    assert result.from_cache is True
    assert [source.title for source in result.data.results] == ["PostgreSQL: Indexes"]
    assert backend.calls == []
    assert get_search_usage(connection) == 0


async def test_miss_searches_normalizes_ranks_and_caches(
    settings: Settings, connection: sqlite3.Connection
) -> None:
    """A fresh search is normalised before it is returned and stored before it is lost.

    Catches two regressions at once: results handed back in the backend's own order (a
    blog above the official manual, with the same page listed twice), and a search that is
    never cached and therefore paid for again on the next identical query.
    """
    backend = StubBackend("fixture", [BLOG, DOCS, DOCS_AGAIN])
    registry = registry_for(settings, connection, backend)

    result = await search(registry, query=QUERY, source_type="docs", max_results=6)

    assert result.ok
    assert result.from_cache is False
    assert [source.url for source in result.data.results] == [
        "https://www.postgresql.org/docs/current/indexes.html",
        "https://medium.com/@someone/postgres-indexes",
    ]
    assert result.data.results[0].domain_class == "official"

    entry = cached(connection, backend="fixture", source_type="docs")
    assert entry is not None
    # Raw and unranked: ranking is redone on every read, so re-classifying a domain does
    # not invalidate a single cached search.
    assert len(entry.results) == 3


async def test_empty_results_are_a_cached_success(
    settings: Settings, connection: sqlite3.Connection
) -> None:
    """A query that found nothing has been answered.

    Catches an empty list being treated as a failure — which would both mislead the agent
    and re-spend quota re-asking for the same silence.
    """
    backend = StubBackend("fixture", [])
    registry = registry_for(settings, connection, backend)

    result = await search(registry, query=QUERY, source_type="docs")

    assert result.ok
    assert result.data.results == []
    entry = cached(connection, backend="fixture", source_type="docs")
    assert entry is not None and entry.results == ()


# --- backend selection ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "source_type", "expected"),
    [
        ("fixture", "academic", "fixture"),
        ("fixture", "docs", "fixture"),
        ("serpapi", "academic", "academic"),
        ("serpapi", "docs", "serpapi"),
        ("academic", "general", "academic"),
    ],
)
def test_backend_selection_keeps_fixture_offline_and_academic_free(
    configured: str, source_type: SearchSourceType, expected: str
) -> None:
    """The routing table, tested where it is decided rather than five times end to end.

    Catches the two expensive mistakes: a `fixture` run resolving to a live backend (which
    would put a development loop on the metered tier), and a scholarly query being sent to
    SerpAPI when OpenAlex would have answered it for free.
    """
    assert _select_backend(source_type, configured) == expected


@respx.mock
async def test_fixture_backend_answers_without_touching_the_network(
    tmp_path: Path, connection: sqlite3.Connection
) -> None:
    """The committed default must be provably offline, for every source type.

    No routes are registered, so any HTTP request raises. Catches a live fall-through —
    the failure that would silently spend the free tier during ordinary development.
    """
    fixture_dir = tmp_path / "search"
    fixture_dir.mkdir()
    (fixture_dir / "recorded.json").write_text(
        json.dumps(
            {
                "query": QUERY,
                "source_type": "academic",
                "recorded_from": "test",
                "results": [{"url": DOCS.url, "title": DOCS.title, "snippet": ""}],
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None, search_backend="fixture", search_fixture_dir=fixture_dir
    )
    registry = registry_for(settings, connection)

    result = await search(registry, query=QUERY, source_type="academic")

    assert result.ok
    assert result.data.results[0].source_backend == "fixture"
    assert get_search_usage(connection) == 0


# --- the budget ---------------------------------------------------------------------------------


async def test_exhausted_budget_refuses_before_the_backend(
    connection: sqlite3.Connection,
) -> None:
    """Past the monthly ceiling, a live search is refused rather than attempted.

    Catches the guard being skipped after a cache miss — the single failure that would let
    a run burn through the rest of the month's searches.
    """
    settings = Settings(
        _env_file=None, search_backend="serpapi", monthly_search_budget=0
    )
    backend = StubBackend("serpapi", [DOCS])
    registry = registry_for(settings, connection, backend)

    result = await search(registry, query=QUERY, source_type="docs")

    assert not result.ok
    assert result.error.code is ErrorCode.MONTHLY_BUDGET_EXCEEDED
    assert result.error.retryable is False
    assert backend.calls == []
    assert cached(connection, backend="serpapi", source_type="docs") is None


async def test_retry_claims_quota_for_each_live_attempt(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retried search is two searches at the provider, and must be counted as two.

    Catches a reservation claimed once per tool call rather than once per live attempt,
    which would let the ladder spend more of the month than the counter ever records.
    """
    monkeypatch.setattr(web_search_module, "_RETRY_BACKOFF_S", 0)
    settings = Settings(_env_file=None, search_backend="serpapi")
    backend = StubBackend(
        "serpapi", SearchBackendError("serpapi", "503", retryable=True), [DOCS]
    )
    registry = registry_for(settings, connection, backend)

    result = await search(registry, query=QUERY, source_type="docs")

    assert result.ok
    assert [call[1] for call in backend.calls] == ["docs", "docs"]
    assert get_search_usage(connection) == 2


# --- failures -------------------------------------------------------------------------------------


async def test_backend_failure_becomes_a_structured_error(
    settings: Settings, connection: sqlite3.Connection
) -> None:
    """A backend that cannot answer must not take the run down, or poison the cache.

    Catches a raised `SearchBackendError` escaping into the agent loop, and a failed
    search being cached as though it had succeeded. A non-retryable failure is asked
    once per rung — the narrow question, then the wider one — and never twice on the same
    rung.
    """
    backend = StubBackend(
        "fixture", SearchBackendError("fixture", "no recording", retryable=False)
    )
    registry = registry_for(settings, connection, backend)

    result = await search(registry, query=QUERY, source_type="docs")

    assert not result.ok
    assert result.error.code is ErrorCode.SEARCH_UNAVAILABLE
    assert result.error.retryable is False
    assert "no recording" in result.error.message
    assert [call[1] for call in backend.calls] == ["docs", "general"]
    assert cached(connection, backend="fixture", source_type="docs") is None


async def test_general_fallback_is_cached_under_the_type_that_answered(
    settings: Settings, connection: sqlite3.Connection
) -> None:
    """A widened search is stored as the widened search it was.

    Catches a fallback answer cached under the source type that was *requested*, which
    would make every later `docs` search silently return general-web results without ever
    trying the narrower one again.
    """
    backend = StubBackend(
        "fixture", SearchBackendError("fixture", "nothing", retryable=False), [BLOG]
    )
    registry = registry_for(settings, connection, backend)

    result = await search(registry, query=QUERY, source_type="docs")

    assert result.ok
    assert [source.url for source in result.data.results] == [
        "https://medium.com/@someone/postgres-indexes"
    ]
    assert cached(connection, backend="fixture", source_type="general") is not None
    assert cached(connection, backend="fixture", source_type="docs") is None
