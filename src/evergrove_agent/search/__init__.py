"""Search-side building blocks: the shared authority map, source normalisation and the
backend contract now, the backends themselves later. Nothing in this package calls a
network.
"""

from __future__ import annotations

from evergrove_agent.search.base import (
    SearchBackend,
    SearchBackendError,
    SearchSourceType,
)
from evergrove_agent.search.domains import AUTHORITY_ORDER, classify_domain
from evergrove_agent.search.normalize import (
    NormalizedSource,
    NormalizeSourcesOutput,
    RawSource,
    canonicalize_url,
    normalize_sources,
)

__all__ = [
    "AUTHORITY_ORDER",
    "NormalizeSourcesOutput",
    "NormalizedSource",
    "RawSource",
    "SearchBackend",
    "SearchBackendError",
    "SearchSourceType",
    "canonicalize_url",
    "classify_domain",
    "normalize_sources",
]
