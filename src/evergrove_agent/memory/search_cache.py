"""The search cache: a query answered once, reused until it expires.

Plan sections 10 and 12. `web_search` (S7) reads this *before* it reaches a backend and
writes back what the backend returned; nothing else uses the `search_cache` table. A hit
therefore costs neither network nor quota, which is the whole point — the free tier is 250
searches a month, and asking the same question twice spends two of them for one answer.

Storage only. It does not search, rank, retry, or know that SerpAPI exists, and it opens no
connection of its own: the caller passes one from `db.connect` / `db.open_database`.

The lookup key is a digest of the *normalised* query plus exactly the parameters that change
what a backend would return — the backend name, the source type and the result limit. Two
spellings of one query share an entry; a different backend or limit never does.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from evergrove_agent.config import get_settings
from evergrove_agent.memory.db import transaction
from evergrove_agent.search import RawSource, SearchSourceType

_COLUMNS = (
    "key, query, backend, source_type, max_results, results_json,"
    " searched_at, expires_at"
)

_KEY_SEPARATOR = "\x1f"
"""ASCII unit separator: it cannot occur in a query, a backend name or a source type, so
no combination of components can be spelled two ways and collide."""


@dataclass(frozen=True)
class CachedSearch:
    """One cached result list, with the parameters it is only valid for.

    A storage row, deliberately not a pydantic model — the same split `CachedSource` keeps.
    The tool-facing shape is `WebSearchOutput`, built by `web_search` from `results`.
    """

    key: str
    """Digest the entry is stored under — what a lookup is keyed on."""
    query: str
    """The normalised query, kept readable so the file can be inspected by hand."""
    backend: str
    source_type: str
    max_results: int
    results: tuple[RawSource, ...]
    """Exactly what the backend returned, unranked. Ranking is `normalize_sources`' job
    and is redone on every hit rather than frozen into the cache."""
    searched_at: datetime
    expires_at: datetime


def normalize_query(query: str) -> str:
    """`query` reduced to the form the cache keys on, or `""` when it is not a query.

    Whitespace collapsed and case folded, and nothing else: two spellings of one question
    should share an entry, but the cache must never decide two *different* questions are
    the same one.
    """
    return " ".join(query.split()).casefold()


def search_cache_key(
    query: str,
    *,
    backend: str,
    source_type: SearchSourceType,
    max_results: int,
) -> str:
    """The deterministic key for this search, or `""` for an empty query.

    Only the four inputs that change what a backend would return go in. `max_results` is
    among them: serving a six-result request from a cached ten-result row would be a
    different, wider policy than this one, so a different limit gets a different entry.
    """
    normalized = normalize_query(query)
    if not normalized:
        return ""

    material = _KEY_SEPARATOR.join(
        (normalized, backend, source_type, str(max_results))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def get_cached_search(
    connection: sqlite3.Connection,
    query: str,
    *,
    backend: str,
    source_type: SearchSourceType,
    max_results: int,
    now: datetime | None = None,
) -> CachedSearch | None:
    """Return the cached results for this search, or `None` when there is no valid entry.

    A missing row, an expired row, an empty query and a row this build can no longer read
    are all one answer — a miss — because the caller does the same thing with each: search.
    Expiry is decided here rather than in SQL so the stored timestamp format stays this
    module's business, and an expired row is left in place: reads do not write, and the
    next store overwrites it anyway.
    """
    key = search_cache_key(
        query, backend=backend, source_type=source_type, max_results=max_results
    )
    if not key:
        return None

    row = connection.execute(
        f"SELECT {_COLUMNS} FROM search_cache WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return None

    entry = _to_cached_search(row)
    if entry is None or entry.expires_at <= (now or datetime.now(UTC)):
        return None
    return entry


def store_cached_search(
    connection: sqlite3.Connection,
    *,
    query: str,
    backend: str,
    source_type: SearchSourceType,
    max_results: int,
    results: list[RawSource],
    ttl_days: int | None = None,
    now: datetime | None = None,
) -> CachedSearch:
    """Store `results` for this search, expiring `ttl_days` (default
    `SEARCH_CACHE_TTL_DAYS`) from now.

    Replaces any existing entry for the same key, so refreshing an expired search is the
    same call as caching it the first time. An empty result list is stored like any other:
    a query that genuinely found nothing has been answered, and re-asking it costs quota
    for the same silence. Raises `ValueError` on an empty query — the caller validated it
    before it searched, and an invented key would poison the table.
    """
    key = search_cache_key(
        query, backend=backend, source_type=source_type, max_results=max_results
    )
    if not key:
        raise ValueError(f"refusing to cache an empty search query: {query!r}")

    searched_at = now or datetime.now(UTC)
    days = ttl_days if ttl_days is not None else get_settings().search_cache_ttl_days
    entry = CachedSearch(
        key=key,
        query=normalize_query(query),
        backend=backend,
        source_type=source_type,
        max_results=max_results,
        results=tuple(results),
        searched_at=searched_at,
        expires_at=searched_at + timedelta(days=days),
    )

    with transaction(connection) as cursor:
        cursor.execute(
            f"INSERT OR REPLACE INTO search_cache ({_COLUMNS})"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.key,
                entry.query,
                entry.backend,
                entry.source_type,
                entry.max_results,
                _dump_results(entry.results),
                entry.searched_at.isoformat(),
                entry.expires_at.isoformat(),
            ),
        )
    return entry


def _dump_results(results: tuple[RawSource, ...]) -> str:
    """Serialise the result list to the JSON stored in `results_json`.

    `RawSource` — S4's model, the one every backend already returns — is what gets stored,
    so nothing provider-specific reaches the table and a hit reconstructs exactly the shape
    a live call would have produced.
    """
    return json.dumps([source.model_dump() for source in results], separators=(",", ":"))


def _to_cached_search(row: sqlite3.Row) -> CachedSearch | None:
    """Rebuild a row into the dataclass, or `None` if it can no longer be read.

    A row written by an older build, or one edited by hand, must degrade to a cache miss —
    a search is cheap and re-storing fixes the row, while raising here would take down a
    tool over a stale cache entry.
    """
    try:
        payload = json.loads(row["results_json"])
        results = tuple(RawSource.model_validate(item) for item in payload)
    except (json.JSONDecodeError, TypeError, ValidationError):
        return None

    return CachedSearch(
        key=row["key"],
        query=row["query"],
        backend=row["backend"],
        source_type=row["source_type"],
        max_results=row["max_results"],
        results=results,
        searched_at=_parse_timestamp(row["searched_at"]),
        expires_at=_parse_timestamp(row["expires_at"]),
    )


def _parse_timestamp(value: str) -> datetime:
    """Read a stored ISO-8601 timestamp, treating a naive one as UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
