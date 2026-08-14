"""Scholarly search across three keyless APIs (plan section 10, README's backend table).

OpenAlex, Crossref and arXiv are free, unmetered and need no key, which is why
`memory/budget.py` never charges this backend. They are tried in order and the first one
that returns anything wins, so an ordinary search is one HTTP call rather than three:
OpenAlex has the broadest index, Crossref is authoritative for published DOIs, and arXiv
is the only one of them that has preprints.

One backend, not three, because the caller has nothing to choose between them — the
provider is an implementation detail behind a single `academic` name, exactly as
`SEARCH_BACKEND` promises. arXiv's Atom feed is parsed with the stdlib `ElementTree`, the
same call the `.docx` reader already makes; `feedparser` would be a dependency for ten
lines of `findall`.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any
from xml.etree import ElementTree

import httpx

from evergrove_agent.config import Settings, get_settings
from evergrove_agent.search.base import SearchBackendError, SearchSourceType
from evergrove_agent.search.normalize import RawSource

_BACKEND = "academic"
_USER_AGENT = "evergrove-research-agent/0.1"
"""Crossref and OpenAlex both ask callers to identify themselves; an anonymous client is
served from a slower pool."""

_SNIPPET_CHARS = 300
_ATOM = "{http://www.w3.org/2005/Atom}"
_TAG_RE = re.compile(r"<[^>]+>")


async def _openalex(
    client: httpx.AsyncClient, query: str, max_results: int
) -> list[RawSource]:
    body = await _get_json(
        client,
        "https://api.openalex.org/works",
        {"search": query, "per-page": max_results},
    )
    results = []
    for item in (body.get("results") or [])[:max_results]:
        # The DOI is the citable, resolvable address; the OpenAlex id is the fallback.
        url = item.get("doi") or item.get("id")
        if not url:
            continue
        results.append(
            _source(
                url,
                item.get("display_name") or "",
                _abstract_from_index(item.get("abstract_inverted_index")),
            )
        )
    return results


async def _crossref(
    client: httpx.AsyncClient, query: str, max_results: int
) -> list[RawSource]:
    body = await _get_json(
        client,
        "https://api.crossref.org/works",
        {"query": query, "rows": max_results},
    )
    results = []
    for item in ((body.get("message") or {}).get("items") or [])[:max_results]:
        url = item.get("URL")
        if not url:
            continue
        results.append(
            _source(
                url,
                _first(item.get("title")),
                _strip_markup(item.get("abstract")) or _first(item.get("container-title")),
            )
        )
    return results


async def _arxiv(
    client: httpx.AsyncClient, query: str, max_results: int
) -> list[RawSource]:
    response = await _get(
        client,
        "https://export.arxiv.org/api/query",
        {"search_query": f"all:{query}", "max_results": max_results},
    )
    try:
        feed = ElementTree.fromstring(response.text)
    except ElementTree.ParseError as exc:
        raise SearchBackendError(
            _BACKEND, f"arXiv returned unparseable Atom: {exc}", retryable=False
        ) from exc

    results = []
    for entry in feed.findall(f"{_ATOM}entry")[:max_results]:
        url = _text(entry.find(f"{_ATOM}id"))
        if not url:
            continue
        results.append(
            _source(
                url,
                _text(entry.find(f"{_ATOM}title")),
                _text(entry.find(f"{_ATOM}summary")),
            )
        )
    return results


_PROVIDERS: tuple[Callable[..., Awaitable[list[RawSource]]], ...] = (
    _openalex,
    _crossref,
    _arxiv,
)


class AcademicSearchBackend:
    """Searches OpenAlex, then Crossref, then arXiv — first answer wins."""

    name = _BACKEND

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
        """`source_type` is accepted and ignored on purpose: every provider behind this
        backend is already scholarly, so there is no strategy left for it to select.

        A provider that fails is stepped over, not fatal — the point of three of them is
        that one being down is survivable. Only a clean sweep of failures is an error;
        three providers that simply found nothing is an honest empty list.
        """
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._settings.search_timeout_s)
        )
        failures: list[SearchBackendError] = []
        try:
            for provider in _PROVIDERS:
                try:
                    results = await provider(client, query, max_results)
                except SearchBackendError as exc:
                    failures.append(exc)
                    continue
                if results:
                    return results
        finally:
            if self._owns_client:
                await client.aclose()

        if len(failures) == len(_PROVIDERS):
            raise SearchBackendError(
                self.name,
                "every academic provider failed: "
                + "; ".join(str(failure) for failure in failures),
                retryable=any(failure.retryable for failure in failures),
            )
        return []


async def _get(
    client: httpx.AsyncClient, url: str, params: dict[str, Any]
) -> httpx.Response:
    """One provider request, with its failures already classified.

    A 4xx is this build asking wrongly and will ask wrongly again; a 5xx or a dropped
    connection is the provider having a moment and is worth one more try later.
    """
    try:
        response = await client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SearchBackendError(
            _BACKEND,
            f"{url} returned {exc.response.status_code}",
            retryable=exc.response.status_code >= 500,
        ) from exc
    except httpx.HTTPError as exc:
        raise SearchBackendError(_BACKEND, f"could not reach {url}: {exc}") from exc
    return response


async def _get_json(
    client: httpx.AsyncClient, url: str, params: dict[str, Any]
) -> dict[str, Any]:
    response = await _get(client, url, params)
    try:
        return response.json()
    except ValueError as exc:
        raise SearchBackendError(
            _BACKEND, f"{url} response was not JSON: {exc}", retryable=False
        ) from exc


def _source(url: str, title: str, snippet: str) -> RawSource:
    return RawSource(
        url=url,
        title=title.strip(),
        snippet=snippet[:_SNIPPET_CHARS].strip(),
        source_backend=_BACKEND,
    )


def _abstract_from_index(inverted: dict[str, list[int]] | None) -> str:
    """Rebuild OpenAlex's abstract, which it stores as word -> positions.

    Worth the six lines: without it an OpenAlex result reaches the model as a bare title,
    and a title alone is a poor basis for choosing which source to read.
    """
    if not inverted:
        return ""
    words = sorted(
        (position, word) for word, positions in inverted.items() for position in positions
    )
    return " ".join(word for _, word in words)


def _strip_markup(value: str | None) -> str:
    """Crossref abstracts arrive as JATS XML. A snippet only needs the words."""
    if not value:
        return ""
    return " ".join(_TAG_RE.sub(" ", value).split())


def _first(value: Any) -> str:
    """Crossref returns `title` and `container-title` as lists."""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value else ""


def _text(element: ElementTree.Element | None) -> str:
    return " ".join(element.text.split()) if element is not None and element.text else ""
