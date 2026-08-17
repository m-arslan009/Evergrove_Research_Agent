"""Persistent storage: the single SQLite file, the caches, the budget counter and the two
memories that sit in it. Nothing in this package calls a network or a model.

Two of these are memory in the plan's sense and are deliberately separate:
`prep_memory` survives runs and is found by a normalised task key, while `run_memory` belongs
to one `run_id` and records that run's hops. Every module here raises on a `sqlite3.Error`;
deciding what a storage failure *means* belongs to the caller — `tools/memory_tools.py` for the
memories, `tracing/tracer.py` for the trace, `fetch_url` for the cache.
"""

from __future__ import annotations

from evergrove_agent.memory.budget import (
    QUOTA_CONSUMING_BACKENDS,
    BudgetReservation,
    consumes_quota,
    current_month,
    get_search_usage,
    reserve_search_call,
)
from evergrove_agent.memory.cache import (
    CachedSource,
    get_cached_source,
    store_cached_source,
)
from evergrove_agent.memory.db import (
    SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
    connect,
    initialize_schema,
    open_database,
    transaction,
)
from evergrove_agent.memory.prep_memory import (
    normalize_task_key,
    recall_previous_preparation,
    save_preparation,
)
from evergrove_agent.memory.run_memory import (
    RunMemoryEntry,
    RunMemoryKind,
    RunMemoryRecord,
    entries_from,
    get_run_memory,
    record_entries,
    seen_queries,
    seen_urls,
)
from evergrove_agent.memory.search_cache import (
    CachedSearch,
    get_cached_search,
    normalize_query,
    search_cache_key,
    store_cached_search,
)

__all__ = [
    "QUOTA_CONSUMING_BACKENDS",
    "SCHEMA_STATEMENTS",
    "SCHEMA_VERSION",
    "BudgetReservation",
    "CachedSearch",
    "CachedSource",
    "RunMemoryEntry",
    "RunMemoryKind",
    "RunMemoryRecord",
    "connect",
    "consumes_quota",
    "current_month",
    "entries_from",
    "get_cached_search",
    "get_cached_source",
    "get_run_memory",
    "get_search_usage",
    "initialize_schema",
    "normalize_query",
    "normalize_task_key",
    "open_database",
    "recall_previous_preparation",
    "record_entries",
    "reserve_search_call",
    "save_preparation",
    "search_cache_key",
    "seen_queries",
    "seen_urls",
    "store_cached_search",
    "store_cached_source",
    "transaction",
]
