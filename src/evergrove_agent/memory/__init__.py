"""Persistent storage: the single SQLite file today, the caches, budget counters and
memory that sit in it later. Nothing in this package calls a network or a model.
"""

from __future__ import annotations

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

__all__ = [
    "SCHEMA_STATEMENTS",
    "SCHEMA_VERSION",
    "CachedSource",
    "connect",
    "get_cached_source",
    "initialize_schema",
    "open_database",
    "store_cached_source",
    "transaction",
]
