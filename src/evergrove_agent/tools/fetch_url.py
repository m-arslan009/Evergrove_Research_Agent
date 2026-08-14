"""`fetch_url` — the agent's one way onto the web (plan section 11).

One URL in, the readable text of that page out. No crawling, no link following and no
JavaScript rendering: the caller has already chosen the URL, and this tool only opens it.

Almost nothing here is new. `canonicalize_url` (S4) decides what one URL *is*, the source
cache (S5) answers before the network is touched, `read_pdf` (S3) handles a PDF and its
three failure codes, `extract_html` strips a page down to its prose, and `select_passages`
(S2) trims what is left to the model's budget. What this module owns is the order those
run in, and turning every way a fetch can go wrong into a `ToolResult` the agent can act
on — "timed out, try again" has to stay distinguishable from "that PDF is a scan".

The cache stores the whole *extracted* text, never raw HTML and never an excerpt. An
excerpt depends on the question asked: cache one and the next question about the same page
gets the previous question's paragraphs. What is cached is therefore bounded by
`MAX_SOURCE_TEXT_CHARS` — a safety ceiling on a source — and never by a reading budget,
neither `SOURCE_EXCERPT_CHARS` nor the caller's own `max_chars`, both of which describe
one answer rather than the source behind it.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from evergrove_agent.config import Settings, get_settings
from evergrove_agent.documents import (
    DocumentReadError,
    ParsedDocument,
    extract_html,
    read_pdf,
    select_passages,
)
from evergrove_agent.memory import (
    CachedSource,
    get_cached_source,
    open_database,
    store_cached_source,
)
from evergrove_agent.schemas import ErrorCode, ToolError, ToolResult
from evergrove_agent.search import canonicalize_url
from evergrove_agent.tools.base import RunContext

logger = logging.getLogger(__name__)
"""Only the degraded paths speak: a cache that could not be read or written is survivable,
but it must not be invisible — a run silently refetching every page looks like a slow
network. Handlers are the application's business; a library only names the logger."""

_USER_AGENT = "EvergroveResearchAgent/0.1"
"""We identify ourselves. An unlabelled client is what gets a research agent blocked."""

_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_PDF_TYPES = frozenset({"application/pdf", "application/x-pdf"})
_TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})

_NOT_FOUND_STATUSES = frozenset({404, 410})
_RETRYABLE_STATUSES = frozenset({429})
"""429 joins the 5xx family: both are "not now", which a second attempt can survive. Every
other 4xx is about the request itself, and repeating it verbatim cannot help."""

_MAX_ERROR_BODY_CHARS = 200
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")


class FetchUrlInput(BaseModel):
    """Which page to read, how much of it to keep, and what to look for in it."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=1,
        max_length=2048,
        description="The http(s) URL to fetch. One page per call; links on it are not "
        "followed.",
    )
    max_chars: int = Field(
        default=20000,
        ge=500,
        le=200000,
        description="Ceiling on how much text this call returns. It does not decide what "
        "is cached: the cache keeps the whole source so a later, differently-worded "
        "question can still find different passages in it.",
    )
    excerpt_for: str | None = Field(
        default=None,
        max_length=200,
        description="The question this page is being read for. Given one, only the "
        "passages that answer it come back instead of the whole page.",
    )


class FetchUrlOutput(BaseModel):
    """The page as the model will see it."""

    model_config = ConfigDict(extra="forbid")

    url: str
    """The canonical form of the requested URL — what the cache is keyed on."""
    final_url: str
    """Where the request actually landed after redirects."""
    title: str = ""
    text: str = ""
    char_count: int = Field(default=0, ge=0)
    truncated: bool = False
    """The returned text is shorter than what was extracted."""
    from_cache: bool = False
    retrieved_at: datetime
    """When the page was fetched — the original fetch time on a cache hit, not now."""


@dataclass(frozen=True)
class _Page:
    """A successfully fetched and extracted page, before the cache and the excerpt."""

    text: str
    title: str
    final_url: str
    content_type: str


class FetchUrlTool:
    """Fetch one URL and return its readable text, from the cache when it can."""

    name = "fetch_url"
    description = (
        "Fetch one web page or PDF by URL and return its readable text, without the "
        "navigation and boilerplate. Answers from the local cache when the page was "
        "fetched recently. Pass excerpt_for to get only the passages relevant to a "
        "question. Does not follow links and does not run JavaScript."
    )
    input_model = FetchUrlInput
    output_model = FetchUrlOutput

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """`client` and `connection` are injection seams — the shape the search backends
        already use. Given neither, each call opens and closes its own."""
        self._settings = settings or get_settings()
        self._client = client
        self._connection = connection

    async def run(
        self, args: FetchUrlInput, ctx: RunContext
    ) -> ToolResult[FetchUrlOutput]:
        """Canonicalise → cache → fetch → extract → cache → trim. `duration_ms` is the
        registry's job."""
        url = canonicalize_url(args.url)
        if url is None:
            return _failure(
                ErrorCode.BAD_ARGUMENTS,
                f"{args.url!r} is not a usable http(s) URL.",
                retryable=False,
            )

        cached = self._cache_read(url)
        if cached is not None:
            return _success(
                args,
                url,
                final_url=cached.final_url,
                title=cached.title,
                extracted=cached.text,
                source_length=len(cached.text),
                excerpt_limit=self._excerpt_limit(args),
                retrieved_at=cached.fetched_at,
                from_cache=True,
            )

        page = await self._fetch(url)
        if isinstance(page, ToolResult):
            return page

        if not page.text.strip():
            return _failure(
                ErrorCode.EMPTY_FILE,
                f"{page.final_url} was fetched but no readable text could be extracted "
                "from it. It is probably built by JavaScript, which this tool does not "
                "run — try a different source.",
                retryable=False,
            )

        # Bounded by the *source* ceiling, not by this call's reading budget: the next
        # call may ask a different question of the same page.
        extracted = page.text[: self._settings.max_source_text_chars]
        self._cache_write(url, page, extracted)
        return _success(
            args,
            url,
            final_url=page.final_url,
            title=page.title,
            extracted=extracted,
            source_length=len(page.text),
            excerpt_limit=self._excerpt_limit(args),
            retrieved_at=datetime.now(UTC),
            from_cache=False,
        )

    def _excerpt_limit(self, args: FetchUrlInput) -> int:
        """The budget `select_passages` packs against: the tighter of what the model is
        allowed to see and what this call asked for."""
        return min(args.max_chars, self._settings.source_excerpt_chars)

    # --- the network ------------------------------------------------------------------------

    async def _fetch(self, url: str) -> _Page | ToolResult[FetchUrlOutput]:
        """One request, and at most one retry.

        The retry fires exactly when the failure was marked retryable — a timeout, a
        network blip, a 5xx or a 429. A rejected or missing page is never asked for
        twice, so a bad URL costs one request and no more.
        """
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._settings.fetch_timeout_s),
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        owns_client = self._client is None
        try:
            result = await self._attempt(client, url)
            if isinstance(result, ToolResult) and result.error is not None:
                if result.error.retryable:
                    result = await self._attempt(client, url)
            return result
        finally:
            if owns_client:
                await client.aclose()

    async def _attempt(
        self, client: httpx.AsyncClient, url: str
    ) -> _Page | ToolResult[FetchUrlOutput]:
        """A single GET, streamed so an oversized body is abandoned mid-transfer."""
        limit = self._settings.max_fetch_bytes
        try:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    await response.aread()
                    return _http_failure(response)

                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > limit:
                        return _failure(
                            ErrorCode.BUDGET_EXCEEDED,
                            f"{url} is larger than the {limit} byte fetch limit; "
                            "it was not downloaded.",
                            retryable=False,
                        )
                final_url = str(response.url)
                content_type = _media_type(response.headers.get("content-type"))
                encoding = response.encoding or "utf-8"
        except httpx.TimeoutException as exc:
            return _failure(
                ErrorCode.TIMEOUT,
                f"{url} did not respond within {self._settings.fetch_timeout_s}s: "
                f"{type(exc).__name__}",
                retryable=True,
            )
        except httpx.HTTPError as exc:
            return _failure(
                ErrorCode.FETCH_FAILED,
                f"could not reach {url}: {type(exc).__name__}: {exc}",
                retryable=True,
            )

        return self._extract(
            bytes(body), final_url=final_url, content_type=content_type, encoding=encoding
        )

    # --- bytes into text --------------------------------------------------------------------

    def _extract(
        self, body: bytes, *, final_url: str, content_type: str, encoding: str
    ) -> _Page | ToolResult[FetchUrlOutput]:
        """Route on the content type. A server that sent none is judged by its URL."""
        looks_like_pdf = not content_type and final_url.lower().endswith(".pdf")
        if content_type in _PDF_TYPES or looks_like_pdf:
            return self._read_pdf(
                body,
                final_url=final_url,
                content_type=content_type or "application/pdf",
            )

        if content_type in _HTML_TYPES or not content_type:
            title, text = extract_html(body.decode(encoding, errors="replace"))
            return _Page(
                text=text,
                title=title,
                final_url=final_url,
                content_type=content_type or "text/html",
            )

        if content_type in _TEXT_TYPES:
            return _Page(
                text=body.decode(encoding, errors="replace"),
                title="",
                final_url=final_url,
                content_type=content_type,
            )

        return _failure(
            ErrorCode.UNSUPPORTED_TYPE,
            f"{final_url} is {content_type}, which fetch_url cannot read. It handles "
            "HTML, PDF and plain text.",
            retryable=False,
        )

    @staticmethod
    def _read_pdf(
        body: bytes, *, final_url: str, content_type: str
    ) -> _Page | ToolResult[FetchUrlOutput]:
        """Hand the bytes to S3's PDF reader through a temporary file.

        `read_pdf` takes a path, and `read_document_file` cannot be used at all: its guard
        resolves everything inside `ALLOWED_ATTACHMENT_DIR`, which protects *user
        attachments* and would refuse bytes we downloaded ourselves. A temp file costs one
        write of at most `MAX_FETCH_BYTES` and keeps S3 unchanged — the alternative was
        widening a finished reader's signature for one caller. The file is named after the
        URL so the reader's own error messages still name something recognisable.
        """
        with tempfile.TemporaryDirectory(prefix="evergrove-fetch-") as directory:
            path = Path(directory) / _pdf_filename(final_url)
            path.write_bytes(body)
            try:
                document = read_pdf(path)
            except DocumentReadError as exc:
                return _failure(exc.code, exc.message, retryable=False)

        return _Page(
            text=document.text,
            title=_pdf_title(document),
            final_url=final_url,
            content_type=content_type,
        )

    # --- the cache ----------------------------------------------------------------------------

    @contextmanager
    def _connection_scope(self) -> Iterator[sqlite3.Connection]:
        """The injected connection, or one opened and closed for this call."""
        if self._connection is not None:
            yield self._connection
            return
        with open_database(self._settings.db_path) as connection:
            yield connection

    def _cache_read(self, url: str) -> CachedSource | None:
        """A valid cached page, or `None`. A broken cache degrades to a miss, and says so.

        Non-fatal because the network can still answer — but logged, because a cache that
        never answers turns every repeat look into traffic, and that is indistinguishable
        from a slow network unless something says which it is.
        """
        try:
            with self._connection_scope() as connection:
                return get_cached_source(connection, url)
        except sqlite3.Error as exc:
            logger.warning(
                "source cache read failed for %s (%s: %s); fetching instead",
                url,
                type(exc).__name__,
                exc,
            )
            return None

    def _cache_write(self, url: str, page: _Page, text: str) -> None:
        """Store what we fetched. A storage failure is survivable but never silent: the
        page we already read is returned, and the next call pays to fetch it again."""
        try:
            with self._connection_scope() as connection:
                store_cached_source(
                    connection,
                    url=url,
                    text=text,
                    final_url=page.final_url,
                    title=page.title,
                    content_type=page.content_type,
                    ttl_days=self._settings.cache_ttl_days,
                )
        except (sqlite3.Error, ValueError) as exc:
            logger.warning(
                "source cache write failed for %s (%s: %s); the page was fetched but "
                "will not be reused",
                url,
                type(exc).__name__,
                exc,
            )


# --- shaping the answer -------------------------------------------------------------------


def _success(
    args: FetchUrlInput,
    url: str,
    *,
    final_url: str,
    title: str,
    extracted: str,
    source_length: int,
    excerpt_limit: int,
    retrieved_at: datetime,
    from_cache: bool,
) -> ToolResult[FetchUrlOutput]:
    """The one place the returned text is decided, for a cache hit and a fresh fetch alike.

    Both paths end here so the same URL and the same question produce the same answer
    whether or not the page had been seen before — and, crucially, so a *different*
    question run against the same cached source selects different passages from it.
    `extracted` is the whole source; the trimming happens here and nowhere else.
    `source_length` is what the page extracted to before any trimming, so `truncated`
    reports the whole loss, the same meaning `read_document` gives the field.
    """
    question = (args.excerpt_for or "").strip()
    text = (
        select_passages(extracted, question, max_chars=excerpt_limit)
        if question
        else extracted[: args.max_chars]
    )

    return ToolResult(
        ok=True,
        data=FetchUrlOutput(
            url=url,
            final_url=final_url,
            title=title,
            text=text,
            char_count=len(text),
            truncated=len(text) < source_length,
            from_cache=from_cache,
            retrieved_at=retrieved_at,
        ),
        duration_ms=0,
        from_cache=from_cache,
    )


def _failure(
    code: ErrorCode, message: str, *, retryable: bool
) -> ToolResult[FetchUrlOutput]:
    """A failure the agent can route on. `retryable` says whether a second identical
    call could possibly do better — the same meaning `SearchBackendError` gives it."""
    return ToolResult(
        ok=False,
        error=ToolError(code=code, message=message[:500], retryable=retryable),
        duration_ms=0,
    )


def _http_failure(response: httpx.Response) -> ToolResult[FetchUrlOutput]:
    """A non-2xx status, split by what the agent should do about it."""
    status = response.status_code
    if status in _NOT_FOUND_STATUSES:
        return _failure(
            ErrorCode.NOT_FOUND,
            f"{response.url} returned {status}: the page is gone. Try another source.",
            retryable=False,
        )
    return _failure(
        ErrorCode.FETCH_FAILED,
        f"{response.url} returned {status}: {response.text[:_MAX_ERROR_BODY_CHARS]}",
        retryable=status >= 500 or status in _RETRYABLE_STATUSES,
    )


def _media_type(header: str | None) -> str:
    """`text/html; charset=utf-8` → `text/html`."""
    return (header or "").split(";")[0].strip().lower()


def _pdf_filename(final_url: str) -> str:
    """A safe local name for the downloaded PDF, taken from the URL's last segment."""
    name = _SAFE_FILENAME_RE.sub("_", Path(urlsplit(final_url).path).name)[:60]
    return name if name.lower().endswith(".pdf") else f"{name or 'fetched'}.pdf"


def _pdf_title(document: ParsedDocument) -> str:
    """A PDF's first bookmark, when it has one. Never guessed from the text — the same
    refusal to invent structure that `pdf.py` already makes."""
    return document.outline[0].title if document.outline else ""
