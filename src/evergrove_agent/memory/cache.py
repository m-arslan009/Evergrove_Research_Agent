"""The source cache: fetched page text kept on disk so the second look costs nothing.

Plan section 12. `fetch_url` (S6) will read this before touching the network and write
back what it fetched; nothing else uses the `source_cache` table. This module is storage
only — it does not fetch, extract, rank or know what HTTP is. It opens no connection of
its own either: the caller passes one from `db.connect` / `db.open_database`, which stays
the single way into the file.

The lookup key is the *canonical* URL, produced by S4's `canonicalize_url`, so the
tracking-parameter and trailing-slash variants of one page share one entry instead of
being fetched again.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from evergrove_agent.config import get_settings
from evergrove_agent.memory.db import transaction
from evergrove_agent.search import canonicalize_url

_COLUMNS = "url, final_url, title, text, content_type, fetched_at, expires_at"


@dataclass(frozen=True)
class CachedSource:
    """One cached page: the fields `fetch_url` needs to answer from the cache alone.

    A storage row, deliberately not a pydantic model — nothing here crosses a tool
    boundary, and the tool-facing shapes live in `schemas/`.
    """

    url: str
    """Canonical URL the entry is stored under — what a lookup is keyed on."""
    final_url: str
    """Where the fetch actually landed after redirects; equals `url` when there were none."""
    title: str
    text: str
    """Extracted readable text, not raw HTML."""
    content_type: str
    """Kept so a cache hit still tells an HTML page from a PDF without re-fetching."""
    fetched_at: datetime
    expires_at: datetime


def get_cached_source(
    connection: sqlite3.Connection,
    url: str,
    *,
    now: datetime | None = None,
) -> CachedSource | None:
    """Return the cached page for `url`, or `None` when there is no valid entry.

    A missing row, an expired row and an unusable URL are all the same answer — a miss —
    because the caller does the same thing with each: fetch it. Expiry is decided here
    rather than in SQL so the stored timestamp format stays this module's business, and
    an expired row is left in place rather than deleted: reads do not write, and the next
    store overwrites it anyway.
    """
    key = canonicalize_url(url)
    if key is None:
        return None

    row = connection.execute(
        f"SELECT {_COLUMNS} FROM source_cache WHERE url = ?", (key,)
    ).fetchone()
    if row is None:
        return None

    entry = _to_cached_source(row)
    if entry.expires_at <= (now or datetime.now(UTC)):
        return None
    return entry


def store_cached_source(
    connection: sqlite3.Connection,
    *,
    url: str,
    text: str,
    final_url: str | None = None,
    title: str = "",
    content_type: str = "",
    ttl_days: int | None = None,
    now: datetime | None = None,
) -> CachedSource:
    """Store `text` for `url`, expiring `ttl_days` (default `CACHE_TTL_DAYS`) from now.

    Replaces any existing entry for the same canonical URL, so refreshing an expired page
    is the same call as caching it the first time. Raises `ValueError` on a URL that
    cannot be canonicalised: the caller canonicalises before it fetches, so reaching here
    with one means something is wrong, and inventing a key would poison the table.
    """
    key = canonicalize_url(url)
    if key is None:
        raise ValueError(f"refusing to cache an uncanonicalizable URL: {url!r}")

    fetched_at = now or datetime.now(UTC)
    days = ttl_days if ttl_days is not None else get_settings().cache_ttl_days
    entry = CachedSource(
        url=key,
        final_url=final_url or key,
        title=title,
        text=text,
        content_type=content_type,
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(days=days),
    )

    with transaction(connection) as cursor:
        cursor.execute(
            f"INSERT OR REPLACE INTO source_cache ({_COLUMNS})"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entry.url,
                entry.final_url,
                entry.title,
                entry.text,
                entry.content_type,
                entry.fetched_at.isoformat(),
                entry.expires_at.isoformat(),
            ),
        )
    return entry


def _to_cached_source(row: sqlite3.Row) -> CachedSource:
    """Rebuild a row into the dataclass, timestamps back to timezone-aware UTC."""
    return CachedSource(
        url=row["url"],
        final_url=row["final_url"],
        title=row["title"],
        text=row["text"],
        content_type=row["content_type"],
        fetched_at=_parse_timestamp(row["fetched_at"]),
        expires_at=_parse_timestamp(row["expires_at"]),
    )


def _parse_timestamp(value: str) -> datetime:
    """Read a stored ISO-8601 timestamp, treating a naive one as UTC.

    Everything is written aware and in UTC; the fallback only guards a row written by
    hand or by an older build, so a comparison can never raise on mixed awareness.
    """
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
