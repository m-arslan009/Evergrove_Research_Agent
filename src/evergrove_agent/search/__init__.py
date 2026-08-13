"""Search-side building blocks: the shared authority map, source normalisation, the
backend contract and the backends behind it. Only the live backends call a network, and
the committed default (`fixture`) is not one of them.
"""

from __future__ import annotations

from evergrove_agent.config import SearchBackendName, Settings, get_settings
from evergrove_agent.search.academic import AcademicSearchBackend
from evergrove_agent.search.base import (
    SearchBackend,
    SearchBackendError,
    SearchSourceType,
)
from evergrove_agent.search.domains import AUTHORITY_ORDER, classify_domain
from evergrove_agent.search.fixture import FixtureSearchBackend
from evergrove_agent.search.normalize import (
    NormalizedSource,
    NormalizeSourcesOutput,
    RawSource,
    canonicalize_url,
    normalize_sources,
)
from evergrove_agent.search.serpapi import SerpApiSearchBackend

__all__ = [
    "AUTHORITY_ORDER",
    "AcademicSearchBackend",
    "FixtureSearchBackend",
    "NormalizeSourcesOutput",
    "NormalizedSource",
    "RawSource",
    "SearchBackend",
    "SearchBackendError",
    "SearchSourceType",
    "SerpApiSearchBackend",
    "build_search_backend",
    "canonicalize_url",
    "classify_domain",
    "normalize_sources",
]


def build_search_backend(
    name: SearchBackendName | None = None,
    settings: Settings | None = None,
) -> SearchBackend:
    """The backend `SEARCH_BACKEND` selects (plan section 10).

    The one place a backend name becomes a class, so changing provider stays a `.env`
    edit — `web_search` composes whatever comes back and never names a provider itself.
    `name` overrides the configured value for a CLI flag or a test, the same shape
    `build_provider` takes for models.
    """
    settings = settings or get_settings()
    backend = name or settings.search_backend
    if backend == "serpapi":
        return SerpApiSearchBackend(settings)
    if backend == "academic":
        return AcademicSearchBackend(settings)
    if backend == "ddgs":
        # The plan's optional last rung of web_search's ladder; adding it means adding a
        # production dependency, so it fails loudly rather than resolving to something
        # that merely looks like what was asked for.
        raise SearchBackendError(
            "ddgs",
            "the ddgs backend is not implemented. Use fixture, serpapi or academic.",
            retryable=False,
        )
    return FixtureSearchBackend(settings)
