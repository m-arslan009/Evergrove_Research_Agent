"""Persistent storage: the single SQLite file today, the caches, budget counters and
memory that sit in it later. Nothing in this package calls a network or a model.
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
    "connect",
    "consumes_quota",
    "current_month",
    "get_cached_search",
    "get_cached_source",
    "get_search_usage",
    "initialize_schema",
    "normalize_query",
    "open_database",
    "reserve_search_call",
    "search_cache_key",
    "store_cached_search",
    "store_cached_source",
    "transaction",
]
