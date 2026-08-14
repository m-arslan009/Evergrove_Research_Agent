"""Live web search through SerpAPI's Google results (plan section 10).

The only metered backend in the project: the free tier is 250 searches a month, which is
why `memory/budget.py` charges `serpapi` and nothing else, and why `fixture` — not this —
is the committed default.

One GET request, parsed. No SDK: `google-search-results` is synchronous and would be a
dependency wrapping a single endpoint, while `httpx` is already here for both model
providers and is already what `respx` mocks in the tests.

There is no retry in this file. The ladder — retry once, fall back to
`source_type="general"`, then `SEARCH_UNAVAILABLE` — belongs to `web_search`, which is
also the only thing that knows whether the quota can afford a second attempt.
"""

from __future__ import annotations

from typing import Any

import httpx

from evergrove_agent.config import Settings, get_settings
from evergrove_agent.search.base import SearchBackendError, SearchSourceType
from evergrove_agent.search.normalize import RawSource

_ENDPOINT = "https://serpapi.com/search.json"

_QUERY_HINT: dict[str, str] = {"docs": "documentation"}
"""How `source_type` becomes a Google query strategy. Only `docs` steers the wording;
`technical`, `academic` and `general` pass through untouched — `academic` has its own
backend, and inventing site filters for the other two would silently narrow the search."""

_NOT_RETRYABLE_STATUSES = frozenset({401, 403, 429})
"""A rejected key and an exhausted plan. A second identical call cannot fix either, and
marking them retryable is how a retry step would spend the rest of the month."""


class SerpApiSearchBackend:
    """Queries SerpAPI's Google engine and returns its organic results."""

    name = "serpapi"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    async def search(
        self,
        query: str,
        *,
        source_type: SearchSourceType,
        max_results: int,
    ) -> list[RawSource]:
        api_key = self._api_key()
        hint = _QUERY_HINT.get(source_type, "")

        params = {
            "q": f"{query} {hint}".strip(),
            "api_key": api_key,
            "engine": "google",
            "num": max_results,
        }

        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._settings.search_timeout_s)
        )
        try:
            response = await client.get(_ENDPOINT, params=params)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise SearchBackendError(
                self.name,
                f"SerpAPI returned {status}: {exc.response.text[:200]}",
                retryable=status not in _NOT_RETRYABLE_STATUSES,
            ) from exc
        except httpx.HTTPError as exc:
            raise SearchBackendError(self.name, f"could not reach SerpAPI: {exc}") from exc
        except ValueError as exc:  # includes json.JSONDecodeError
            raise SearchBackendError(
                self.name, f"SerpAPI response was not JSON: {exc}", retryable=False
            ) from exc
        finally:
            if self._owns_client:
                await client.aclose()

        return self._parse(body, max_results)

    def _api_key(self) -> str:
        key = self._settings.serpapi_api_key
        if key is None or not key.get_secret_value():
            raise SearchBackendError(
                self.name,
                "SERPAPI_API_KEY is not set. Get a free key at serpapi.com, put it in "
                ".env, or run with SEARCH_BACKEND=fixture.",
                retryable=False,
            )
        return key.get_secret_value()

    def _parse(self, body: dict[str, Any], max_results: int) -> list[RawSource]:
        """`organic_results` into `RawSource`, telling "found nothing" from "broke".

        SerpAPI answers a query with no hits by returning `error` alongside
        `organic_results_state: "Fully empty"`. That is a successful search — an empty
        list, which `web_search` will not retry — so it must not be raised. Any other
        `error` is a real failure.
        """
        search_information = body.get("search_information") or {}
        if search_information.get("organic_results_state") == "Fully empty":
            return []
        if body.get("error"):
            raise SearchBackendError(
                self.name, f"SerpAPI reported: {body['error']}", retryable=False
            )

        results = []
        for item in (body.get("organic_results") or [])[:max_results]:
            url = item.get("link")
            if not url:
                continue
            results.append(
                RawSource(
                    url=url,
                    title=item.get("title") or "",
                    snippet=item.get("snippet") or "",
                    source_backend=self.name,
                )
            )
        return results
