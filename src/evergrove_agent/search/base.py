"""The one interface every search backend sits behind (plan section 10).

The point of this file is that the search provider is a config value, not a code change:
`web_search` composes a backend it never names, so swapping fixture for SerpAPI is a
`SEARCH_BACKEND` edit and nothing else. It is also what keeps the rest of the build free —
the fixture backend satisfies this contract with no network and no quota.

Nothing here touches HTTP, SQLite, a model or the tool registry. The search cache, the
monthly quota guard, ranking and the failure ladder all belong to `web_search`, above
this line.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from evergrove_agent.search.normalize import RawSource

SearchSourceType = Literal["docs", "technical", "academic", "general"]
"""What kind of source a query is after — `web_search`'s one routing enum (plan section
9.2). A backend maps it onto whatever its own API calls a query strategy: a site filter,
a different index, a different endpoint, or nothing at all."""


class SearchBackendError(RuntimeError):
    """A backend could not answer, or answered with something unusable.

    An exception rather than a `ToolResult`, exactly as `LLMError` is: the ladder that
    turns a backend failure into a value the model can reason about — retry, fall back to
    `source_type="general"`, then `SEARCH_UNAVAILABLE` — is `web_search`'s job, not each
    provider's. An empty result list is *not* this: a query that genuinely found nothing
    succeeded.
    """

    def __init__(self, backend: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(f"[{backend}] {message}")
        self.backend = backend
        # False for a failure a second identical call cannot fix — an exhausted quota, a
        # rejected key. It is what stops the retry step spending the rest of the budget.
        self.retryable = retryable


@runtime_checkable
class SearchBackend(Protocol):
    """The whole search surface: a query in, unranked sources out.

    Planned implementations: `fixture` (recorded JSON, the committed default), `serpapi`,
    `academic`, and optionally `ddgs`. Each translates its own response shape into
    `RawSource` and does nothing else.
    """

    name: str
    """Matches the `SEARCH_BACKEND` config value, and is stamped onto every
    `RawSource.source_backend` so a trace can say where a source came from."""

    async def search(
        self,
        query: str,
        *,
        source_type: SearchSourceType,
        max_results: int,
    ) -> list[RawSource]:
        """Answer `query` with at most `max_results` sources, best first.

        Raw on purpose: canonicalisation, deduplication and authority ranking belong to
        `normalize_sources`, so a backend only has to translate. Returns an empty list
        when there are no results, and raises `SearchBackendError` when it could not
        look.
        """
        ...
