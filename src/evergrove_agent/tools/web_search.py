"""`web_search` — the agent's one way of finding sources (plan section 10).

A question in, a short ranked list of links out. Nothing in this file parses a provider's
response, opens a page, or rewrites a query: every one of those already has an owner. What
this module owns is the *order* the existing pieces run in, and turning every way a search
can fail into a `ToolResult` the agent can act on.

The order exists to protect one scarce thing. SerpAPI's free tier is 250 searches a month
and nothing external holds us to `MONTHLY_SEARCH_BUDGET`, so the cache is read before the
quota is touched and the quota is claimed before a request is made:

    resolve backend → search cache → quota → backend → cache the answer → normalise

Resolving the backend comes first because its name is part of the cache key — a recorded
fixture must never answer a `serpapi` query. And `SEARCH_BACKEND=fixture` resolves to the
fixture backend for *every* source type: the committed default cannot fall through to a
live API, which is what makes the rest of the build free.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict, Field

from evergrove_agent.config import SearchBackendName, Settings, get_settings
from evergrove_agent.memory import (
    consumes_quota,
    get_cached_search,
    open_database,
    reserve_search_call,
    store_cached_search,
)
from evergrove_agent.schemas import ErrorCode, ToolError, ToolResult
from evergrove_agent.search import (
    NormalizedSource,
    RawSource,
    SearchBackend,
    SearchBackendError,
    SearchSourceType,
    build_search_backend,
    normalize_sources,
)
from evergrove_agent.tools.base import RunContext

logger = logging.getLogger(__name__)
"""Only the degraded paths speak, as in `fetch_url`: a cache that cannot be read is
survivable, but a run silently re-searching every query looks exactly like a slow
provider unless something says which it is."""

_RETRY_BACKOFF_S = 2.0
"""Waited once, between the first attempt and its retry. A provider that just refused a
connection is rarely ready the same millisecond, and one pause is the whole of our
politeness — there is deliberately no exponential ladder, because every rung of this one
costs a search."""

_FALLBACK_SOURCE_TYPE: SearchSourceType = "general"
"""The wider question asked when a narrower intent found nothing answerable. Any source
beats no source, and `general` is the one type that never narrows a provider's index."""


class WebSearchInput(BaseModel):
    """What to search for, what kind of source is wanted, and how many."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=3,
        max_length=200,
        description="What to search for, as search-engine keywords rather than a "
        "sentence.",
    )
    source_type: SearchSourceType = Field(
        default="general",
        description="The kind of source wanted. 'docs' for official documentation, "
        "'technical' for engineering writing, 'academic' for papers, 'general' for "
        "anything.",
    )
    max_results: int = Field(
        default=6,
        ge=1,
        le=20,
        description="Ceiling on how many sources come back. Fewer, better sources beat "
        "a long list.",
    )


class WebSearchOutput(BaseModel):
    """The sources found, canonical, deduplicated and authority-ranked."""

    model_config = ConfigDict(extra="forbid")

    results: list[NormalizedSource] = Field(default_factory=list)
    """Best first: official documentation outranks a blog. An empty list means the query
    was answered and found nothing — it is not a failure."""


def _select_backend(
    source_type: SearchSourceType, configured: SearchBackendName
) -> SearchBackendName:
    """Which backend answers this search.

    Two rules, in this order, and the first one is the important one:

    `fixture` stays `fixture` for every source type. An offline run must be *provably*
    offline, and a routing rule with an exception is not provable.

    Otherwise a scholarly question goes to `academic` — OpenAlex/Crossref/arXiv are free,
    keyless and unmetered, so the right sources also cost no quota. Everything else uses
    whatever `SEARCH_BACKEND` names, untouched: a query is never redirected *onto* a
    metered backend the user did not choose.
    """
    if configured == "fixture":
        return "fixture"
    if source_type == "academic":
        return "academic"
    return configured


def _widening(source_type: SearchSourceType) -> tuple[SearchSourceType, ...]:
    """The source types to try, narrowest first — the rungs of the failure ladder."""
    if source_type == _FALLBACK_SOURCE_TYPE:
        return (source_type,)
    return (source_type, _FALLBACK_SOURCE_TYPE)


class WebSearchTool:
    """Search the web for sources, from the cache when it can and within the budget."""

    name = "web_search"
    description = (
        "Search the web for sources on a topic and return a short ranked list of URLs "
        "with titles and snippets, official documentation first. Answers from the local "
        "cache when the same search was run recently. It does not open the pages it "
        "finds — pass a URL to fetch_url to read one."
    )
    input_model = WebSearchInput
    output_model = WebSearchOutput

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        backend: SearchBackend | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """`backend` and `connection` are injection seams, the shape `fetch_url` uses.

        An injected backend answers every source type, bypassing `_select_backend` — but
        it is still charged, cached and keyed under its own `name`, so a stub called
        `serpapi` is metered exactly like the real one.
        """
        self._settings = settings or get_settings()
        self._backend = backend
        self._connection = connection
        self._built: dict[str, SearchBackend] = {}

    async def run(
        self, args: WebSearchInput, ctx: RunContext
    ) -> ToolResult[WebSearchOutput]:
        """Cache → quota → backend → cache. `duration_ms` is the registry's job."""
        query = args.query.strip()
        if not query:
            return _failure(
                ErrorCode.BAD_ARGUMENTS,
                "the search query is blank; pass the keywords to search for.",
                retryable=False,
            )

        backend_name = self._backend_name(args.source_type)

        cached = self._cache_read(query, backend_name, args)
        if cached is not None:
            return _success(list(cached), args.max_results, from_cache=True)

        outcome = await self._search_ladder(query, backend_name, args)
        if isinstance(outcome, ToolResult):
            return outcome

        results, answered_source_type = outcome
        self._cache_write(query, backend_name, answered_source_type, args, results)
        return _success(results, args.max_results, from_cache=False)

    # --- choosing and building the backend ------------------------------------------------

    def _backend_name(self, source_type: SearchSourceType) -> str:
        """The name this search is charged, cached and keyed under."""
        if self._backend is not None:
            return self._backend.name
        return _select_backend(source_type, self._settings.search_backend)

    def _resolve(self, backend_name: str) -> SearchBackend:
        """The backend itself, built once per name and reused.

        Reused because a backend may hold state worth keeping: the fixture backend indexes
        its directory on first use, and rebuilding it per attempt would re-read the disk.
        Raises `SearchBackendError` for a name the factory refuses.
        """
        if self._backend is not None:
            return self._backend
        backend = self._built.get(backend_name)
        if backend is None:
            backend = build_search_backend(backend_name, self._settings)
            self._built[backend_name] = backend
        return backend

    # --- the failure ladder -------------------------------------------------------------------

    async def _search_ladder(
        self, query: str, backend_name: str, args: WebSearchInput
    ) -> tuple[list[RawSource], SearchSourceType] | ToolResult[WebSearchOutput]:
        """Ask; ask again if that could help; then ask wider.

        At most three live attempts, and each one claims its own quota, because each one
        is a search the provider will charge for. The retry fires only on the first rung
        and only for a failure marked retryable — an exhausted plan or a rejected key is
        never asked twice, which is what stops a broken key spending the month.

        The error reported at the end is the *first* one, not the last: it describes the
        search that was actually asked for, while the last describes the fallback.
        """
        first: SearchBackendError | None = None

        for source_type in _widening(args.source_type):
            for attempt in (1, 2):
                try:
                    outcome = await self._attempt(
                        query, backend_name, source_type, args.max_results
                    )
                except SearchBackendError as exc:
                    first = first or exc
                    retry_worthwhile = (
                        exc.retryable
                        and attempt == 1
                        and source_type == args.source_type
                    )
                    if not retry_worthwhile:
                        break
                    await asyncio.sleep(_RETRY_BACKOFF_S)
                    continue

                if isinstance(outcome, ToolResult):
                    # A budget refusal: no later rung can afford anything either.
                    return outcome
                return outcome, source_type

        return _search_unavailable(first)

    async def _attempt(
        self,
        query: str,
        backend_name: str,
        source_type: SearchSourceType,
        max_results: int,
    ) -> list[RawSource] | ToolResult[WebSearchOutput]:
        """One live attempt: build, claim the quota, ask.

        Building comes before claiming so a backend we cannot even construct never costs
        a reservation — there is no refund path, by design.
        """
        backend = self._resolve(backend_name)
        refusal = self._reserve(backend_name)
        if refusal is not None:
            return refusal
        return await backend.search(
            query, source_type=source_type, max_results=max_results
        )

    def _reserve(self, backend_name: str) -> ToolResult[WebSearchOutput] | None:
        """Claim one live search, or the refusal to return instead. `None` means proceed.

        Unmetered backends — `fixture`, `academic` — never reach the counter, so an
        offline run cannot move it. A ledger that cannot be read is the one place a
        SQLite failure is *not* degraded into "carry on": spending an uncounted live
        search is exactly what the counter exists to prevent, so the call is refused. It
        is not `MONTHLY_BUDGET_EXCEEDED`, which would claim to know something we could
        not read.
        """
        if not consumes_quota(backend_name):
            return None

        try:
            with self._connection_scope() as connection:
                reservation = reserve_search_call(
                    connection, limit=self._settings.monthly_search_budget
                )
        except sqlite3.Error as exc:
            logger.warning(
                "search budget ledger unavailable (%s: %s); refusing a live %s search",
                type(exc).__name__,
                exc,
                backend_name,
            )
            return _failure(
                ErrorCode.SEARCH_UNAVAILABLE,
                "the monthly search budget could not be read, so a live search was "
                "refused rather than spent uncounted. Use the sources already gathered.",
                retryable=False,
            )

        if not reservation.granted:
            return _failure(
                ErrorCode.MONTHLY_BUDGET_EXCEEDED,
                f"the monthly live-search budget is spent: {reservation.used} of "
                f"{reservation.limit} searches used in {reservation.month}. Work with "
                "the sources already gathered.",
                retryable=False,
            )
        return None

    # --- the cache -----------------------------------------------------------------------------

    @contextmanager
    def _connection_scope(self) -> Iterator[sqlite3.Connection]:
        """The injected connection, or one opened and closed for this call."""
        if self._connection is not None:
            yield self._connection
            return
        with open_database(self._settings.db_path) as connection:
            yield connection

    def _cache_read(
        self, query: str, backend_name: str, args: WebSearchInput
    ) -> tuple[RawSource, ...] | None:
        """A valid cached result list, or `None`. A broken cache degrades to a miss.

        Non-fatal because a search can still answer — but logged, because every miss here
        is a search that may cost quota.
        """
        try:
            with self._connection_scope() as connection:
                entry = get_cached_search(
                    connection,
                    query,
                    backend=backend_name,
                    source_type=args.source_type,
                    max_results=args.max_results,
                )
        except sqlite3.Error as exc:
            logger.warning(
                "search cache read failed for %r (%s: %s); searching instead",
                query,
                type(exc).__name__,
                exc,
            )
            return None
        return None if entry is None else entry.results

    def _cache_write(
        self,
        query: str,
        backend_name: str,
        source_type: SearchSourceType,
        args: WebSearchInput,
        results: list[RawSource],
    ) -> None:
        """Store what the backend returned, under the parameters that actually produced it.

        `source_type` is the rung that answered, not necessarily the one requested: a row
        must never claim a `docs` search returned what a `general` one found. The cost is
        that a repeat of a fallen-back query runs the ladder again — bounded by the same
        budget guard, and honest.

        Raw `RawSource` goes in, because ranking is redone on every read: a change to
        `domains.json` then takes effect without invalidating a single cached search.
        """
        try:
            with self._connection_scope() as connection:
                store_cached_search(
                    connection,
                    query=query,
                    backend=backend_name,
                    source_type=source_type,
                    max_results=args.max_results,
                    results=results,
                    ttl_days=self._settings.search_cache_ttl_days,
                )
        except (sqlite3.Error, ValueError) as exc:
            logger.warning(
                "search cache write failed for %r (%s: %s); the results were returned "
                "but the next identical search will pay for them again",
                query,
                type(exc).__name__,
                exc,
            )


# --- shaping the answer ---------------------------------------------------------------------


def _success(
    results: Sequence[RawSource], max_results: int, *, from_cache: bool
) -> ToolResult[WebSearchOutput]:
    """The one place a result list becomes an answer, for a hit and a fresh search alike.

    Both paths end here, so a cached search and a live one produce the same output down to
    the ordering. Trimming happens after normalisation, never before: deduplication can
    only shrink the list, and cutting first would discard a source that outranks one kept.
    """
    normalized = normalize_sources(results)
    return ToolResult(
        ok=True,
        data=WebSearchOutput(results=normalized.sources[:max_results]),
        duration_ms=0,
        from_cache=from_cache,
    )


def _failure(
    code: ErrorCode, message: str, *, retryable: bool
) -> ToolResult[WebSearchOutput]:
    """A failure the agent can route on, never an exception — the tool contract."""
    return ToolResult(
        ok=False,
        error=ToolError(code=code, message=message[:500], retryable=retryable),
        duration_ms=0,
    )


def _search_unavailable(
    failure: SearchBackendError | None,
) -> ToolResult[WebSearchOutput]:
    """The end of the ladder: every rung failed.

    One code for every backend failure, deliberately. What the agent does about it is the
    same in each case — search is not answering, so work with what is already gathered —
    and `retryable` carries the only distinction it can act on.
    """
    if failure is None:  # unreachable: the ladder always attempts at least once
        return _failure(
            ErrorCode.SEARCH_UNAVAILABLE, "no search was attempted.", retryable=False
        )
    return _failure(
        ErrorCode.SEARCH_UNAVAILABLE,
        f"search is unavailable: {failure}",
        retryable=failure.retryable,
    )
