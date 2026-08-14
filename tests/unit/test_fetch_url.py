"""`fetch_url`: does a page come back readable, does a cached one come back without a
request, and does every failure come back as its own code instead of an exception?

Nothing here reaches the network. `respx` intercepts httpx, the PDF is built in memory by
the shared `build_pdf` fixture, and the cache is a SQLite file in `tmp_path` — never
`DB_PATH`. Every call goes through `ToolRegistry`, because that is the only sanctioned way
to reach a tool.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from evergrove_agent.config import Settings
from evergrove_agent.documents import extract_html
from evergrove_agent.memory import db, get_cached_source, store_cached_source
from evergrove_agent.schemas import ErrorCode, ToolResult
from evergrove_agent.tools import RunContext, ToolRegistry
from evergrove_agent.tools.fetch_url import FetchUrlTool

URL = "https://example.com/guide/indexes"

PAGE = """\
<html>
  <head><title>Indexes &amp; you</title></head>
  <body>
    <nav>Home | Docs | Blog</nav>
    <script>track({page: "indexes"});</script>
    <h1>Indexes</h1>
    <p>A B-tree index stores keys in   sorted order.</p>
    <pre>CREATE INDEX ON orders (customer_id);</pre>
    <footer>Copyright 2026. Cookie preferences.</footer>
  </body>
</html>
"""

LONG_PAGE = (
    "<html><head><title>Planner</title></head><body>"
    + "".join(
        f"<p>Filler paragraph {index} about matters that answer nothing at all.</p>"
        for index in range(120)
    )
    + "<p>An index scan reads only the rows that match the predicate.</p>"
    + "<p>VACUUM reclaims the bloat left behind by updated tuples.</p>"
    + "</body></html>"
)
"""Two paragraphs on unrelated topics, buried in filler: what makes it possible to prove
that one cached page answers two different questions with two different excerpts."""


class _BrokenConnection:
    """A connection that refuses every statement — a locked or read-only database."""

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    def cursor(self) -> Any:
        raise sqlite3.OperationalError("database is locked")


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialised cache in a temporary file."""
    with db.open_database(tmp_path / "agent.sqlite3") as conn:
        yield conn


def registry_for(settings: Settings, connection: sqlite3.Connection) -> ToolRegistry:
    """The tool behind the registry, with its cache pointed at the temporary database."""
    registry = ToolRegistry()
    registry.register(FetchUrlTool(settings, connection=connection))
    return registry


async def fetch(registry: ToolRegistry, **args: Any) -> ToolResult[Any]:
    return await registry.call("fetch_url", args, RunContext())


@respx.mock
async def test_an_html_page_comes_back_as_readable_text(
    settings: Settings, connection: sqlite3.Connection
) -> None:
    """The prose survives and the furniture does not.

    Catches the whole point of the tool going wrong: an extractor that hands the model
    navigation, scripts and cookie notices, a lost title, or an output shape that no
    longer reports what it fetched.
    """
    respx.get(URL).mock(return_value=httpx.Response(200, html=PAGE))

    result = await fetch(registry_for(settings, connection), url=URL)

    assert result.ok, result.error
    page = result.data
    assert page.title == "Indexes & you"
    assert "A B-tree index stores keys in sorted order." in page.text
    assert "CREATE INDEX ON orders (customer_id);" in page.text
    assert "Home | Docs | Blog" not in page.text
    assert "track(" not in page.text
    assert "Cookie preferences" not in page.text
    assert page.char_count == len(page.text)
    assert page.from_cache is False and result.from_cache is False
    assert page.final_url == URL


@respx.mock
async def test_a_valid_cache_hit_never_touches_the_network(
    settings: Settings, connection: sqlite3.Connection
) -> None:
    """The single most valuable property of this tool: a hit costs no request.

    Catches a cache read that is skipped, keyed differently from `store`, or consulted
    only after the fetch — each of which silently turns every repeat look into traffic.
    """
    route = respx.get(URL).mock(
        return_value=httpx.Response(200, html="<p>fetched from the network</p>")
    )
    store_cached_source(
        connection,
        url=URL,
        text="A B-tree index stores keys in sorted order.",
        final_url=URL,
        title="Indexes",
        content_type="text/html",
    )

    # The trailing slash and tracking parameter must resolve to the same cached page.
    result = await fetch(registry_for(settings, connection), url=f"{URL}/?utm_source=x")

    assert result.ok, result.error
    assert route.called is False
    assert result.data.from_cache is True and result.from_cache is True
    assert result.data.text == "A B-tree index stores keys in sorted order."


@respx.mock
async def test_the_cache_stores_the_extracted_text_not_the_excerpt(
    settings: Settings, connection: sqlite3.Connection
) -> None:
    """What lands in the cache is the whole clean page, not this question's answer.

    Catches the representation decision being reversed. Caching the excerpt would make
    the second question about a page receive the first question's paragraphs; caching raw
    HTML would re-run extraction on every hit.
    """
    respx.get(URL).mock(return_value=httpx.Response(200, html=LONG_PAGE))

    result = await fetch(
        registry_for(settings, connection), url=URL, excerpt_for="index scan predicate"
    )

    assert result.ok, result.error
    assert "An index scan reads only the rows" in result.data.text
    assert result.data.truncated is True

    cached = get_cached_source(connection, URL)
    assert cached is not None
    assert "<p>" not in cached.text
    assert "Filler paragraph 0" in cached.text  # the whole page, not the excerpt
    assert len(cached.text) > len(result.data.text)


@respx.mock
async def test_one_cached_source_answers_two_questions_differently(
    settings: Settings, connection: sqlite3.Connection
) -> None:
    """A second question about the same page selects different passages from the cache.

    Catches the failure this whole cache design exists to prevent: a cache holding one
    question's answer instead of the source. If the stored text were an excerpt — or were
    trimmed to a reading budget — the second question would come back with the first
    question's paragraphs, from a page that was never re-fetched to correct it.
    """
    route = respx.get(URL).mock(return_value=httpx.Response(200, html=LONG_PAGE))
    registry = registry_for(settings, connection)

    first = await fetch(registry, url=URL, excerpt_for="index scan predicate")
    second = await fetch(registry, url=URL, excerpt_for="vacuum bloat tuples")

    assert first.ok and second.ok, (first.error, second.error)
    assert route.call_count == 1  # the second answer came out of the cache
    assert second.data.from_cache is True

    assert "An index scan reads only the rows" in first.data.text
    assert "VACUUM reclaims the bloat" not in first.data.text
    assert "VACUUM reclaims the bloat" in second.data.text
    assert "An index scan reads only the rows" not in second.data.text


@respx.mock
async def test_a_broken_cache_is_survivable_and_visible(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """A database that refuses every statement costs the cache, not the fetch.

    Catches both halves of the degraded path: a storage failure turning into a failed
    tool call, and a storage failure being swallowed so quietly that a run refetching
    every page looks merely slow.
    """
    respx.get(URL).mock(return_value=httpx.Response(200, html=PAGE))
    registry = ToolRegistry()
    registry.register(FetchUrlTool(settings, connection=_BrokenConnection()))

    with caplog.at_level(logging.WARNING, logger="evergrove_agent.tools.fetch_url"):
        result = await fetch(registry, url=URL)

    assert result.ok, result.error
    assert "B-tree index" in result.data.text
    messages = [record.getMessage() for record in caplog.records]
    assert any("cache read failed" in message for message in messages), messages
    assert any("cache write failed" in message for message in messages), messages


@respx.mock
async def test_a_pdf_is_read_through_the_document_reader(
    settings: Settings,
    connection: sqlite3.Connection,
    build_pdf: Callable[[list[str]], bytes],
) -> None:
    """Downloaded PDF bytes reach S3's reader and come back as text.

    Catches a broken bytes-to-reader handoff — the one place `fetch_url` has to leave the
    network path and enter the document path.
    """
    respx.get("https://example.com/paper.pdf").mock(
        return_value=httpx.Response(
            200,
            content=build_pdf(["Indexes", "A B-tree index stores keys in sorted order."]),
            headers={"content-type": "application/pdf"},
        )
    )

    result = await fetch(
        registry_for(settings, connection), url="https://example.com/paper.pdf"
    )

    assert result.ok, result.error
    assert "B-tree index" in result.data.text


@respx.mock
async def test_a_scanned_pdf_keeps_the_readers_own_error_code(
    settings: Settings,
    connection: sqlite3.Connection,
    build_pdf: Callable[[list[str]], bytes],
) -> None:
    """A PDF with pages but no text layer is `NO_TEXT_LAYER`, not a generic failure.

    Catches a `DocumentReadError` escaping as an exception, or being flattened into
    `FETCH_FAILED` — the agent can only find a different source if it is told which
    problem it hit.
    """
    respx.get("https://example.com/scan.pdf").mock(
        return_value=httpx.Response(
            200, content=build_pdf([]), headers={"content-type": "application/pdf"}
        )
    )

    result = await fetch(
        registry_for(settings, connection), url="https://example.com/scan.pdf"
    )

    assert result.ok is False
    assert result.error.code is ErrorCode.NO_TEXT_LAYER
    assert result.error.retryable is False
    assert get_cached_source(connection, "https://example.com/scan.pdf") is None


@respx.mock
async def test_a_redirect_reports_where_it_landed_and_caches_what_was_asked_for(
    settings: Settings, connection: sqlite3.Connection
) -> None:
    """`final_url` is the destination; the cache key stays the requested URL.

    Catches the two being confused. Keyed on the destination, the next call for the
    original link would miss and fetch the same page again.
    """
    respx.get("https://example.com/old").mock(
        return_value=httpx.Response(301, headers={"location": "https://docs.example.com/new"})
    )
    respx.get("https://docs.example.com/new").mock(
        return_value=httpx.Response(200, html=PAGE)
    )

    result = await fetch(
        registry_for(settings, connection), url="https://example.com/old"
    )

    assert result.ok, result.error
    assert result.data.url == "https://example.com/old"
    assert result.data.final_url == "https://docs.example.com/new"

    cached = get_cached_source(connection, "https://example.com/old")
    assert cached is not None and cached.final_url == "https://docs.example.com/new"


@pytest.mark.parametrize(
    ("response", "code", "retryable", "requests_made"),
    [
        (httpx.ConnectTimeout("too slow"), ErrorCode.TIMEOUT, True, 2),
        (httpx.ConnectError("refused"), ErrorCode.FETCH_FAILED, True, 2),
        (httpx.Response(404), ErrorCode.NOT_FOUND, False, 1),
        (httpx.Response(403, text="forbidden"), ErrorCode.FETCH_FAILED, False, 1),
        (httpx.Response(503, text="down"), ErrorCode.FETCH_FAILED, True, 2),
    ],
)
@respx.mock
async def test_network_failures_carry_their_own_code_and_retry_policy(
    settings: Settings,
    connection: sqlite3.Connection,
    response: httpx.Response | Exception,
    code: ErrorCode,
    retryable: bool,
    requests_made: int,
) -> None:
    """Each failure reports what happened, and only a retryable one is asked twice.

    Catches three regressions at once: a wrong code (the agent cannot choose its next
    move), a wrong `retryable` (a dead link retried forever, or a blip given up on), and
    a retry that repeats past its single attempt.
    """
    route = respx.get(URL)
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)

    result = await fetch(registry_for(settings, connection), url=URL)

    assert result.ok is False
    assert result.error.code is code
    assert result.error.retryable is retryable
    assert route.call_count == requests_made
    assert get_cached_source(connection, URL) is None


@respx.mock
async def test_one_retry_rescues_a_transient_server_error(
    settings: Settings, connection: sqlite3.Connection
) -> None:
    """A 500 followed by a 200 succeeds on the second attempt.

    Catches the retry never firing, which would make every momentary 5xx a lost source.
    """
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(500, text="boom"), httpx.Response(200, html=PAGE)]
    )

    result = await fetch(registry_for(settings, connection), url=URL)

    assert result.ok, result.error
    assert route.call_count == 2


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (
            httpx.Response(200, content=b"\x89PNG\r\n", headers={"content-type": "image/png"}),
            ErrorCode.UNSUPPORTED_TYPE,
        ),
        (
            httpx.Response(200, html="<html><body><nav>Menu</nav></body></html>"),
            ErrorCode.EMPTY_FILE,
        ),
    ],
)
@respx.mock
async def test_content_we_cannot_use_fails_without_being_cached(
    settings: Settings,
    connection: sqlite3.Connection,
    response: httpx.Response,
    code: ErrorCode,
) -> None:
    """An image and a page that extracts to nothing are distinct, actionable failures.

    Catches an unreadable body being cached as an empty page, which would keep answering
    every later call with nothing until the entry expired.
    """
    respx.get(URL).mock(return_value=response)

    result = await fetch(registry_for(settings, connection), url=URL)

    assert result.ok is False
    assert result.error.code is code
    assert result.error.retryable is False
    assert get_cached_source(connection, URL) is None


@respx.mock
async def test_an_oversized_body_is_refused(connection: sqlite3.Connection) -> None:
    """The download stops at `MAX_FETCH_BYTES` instead of reading whatever is offered.

    Catches an unbounded read — the failure mode that ends with a multi-gigabyte response
    in memory on a 16 GB machine.
    """
    settings = Settings(_env_file=None, max_fetch_bytes=1024)
    respx.get(URL).mock(return_value=httpx.Response(200, html="<p>x</p>" + "y" * 4096))

    result = await fetch(registry_for(settings, connection), url=URL)

    assert result.ok is False
    assert result.error.code is ErrorCode.BUDGET_EXCEEDED
    assert get_cached_source(connection, URL) is None


def test_the_committed_html_fixture_extracts_to_prose_only() -> None:
    """`extract_html` against a page shaped like a real one, which is why
    `fixtures/html/article.html` exists.

    Every other page in this file is tidy inline markup written to make one assertion pass,
    so nothing else proves the chrome rules survive contact with a real layout — nested
    `<main>`, a sidebar, a subscribe form, a `<pre><code>` listing. A regression here is
    silent and expensive: the model still gets text, it is just navigation and cookie
    notices eating the prefill budget.
    """
    markup = (
        Settings(_env_file=None).allowed_attachment_dir / "html" / "article.html"
    ).read_text(encoding="utf-8")

    title, text = extract_html(markup)

    assert title == "Understanding B-tree indexes"
    assert "# Understanding B-tree indexes" in text
    assert "## Why the tree stays shallow" in text
    assert "    SELECT id, title" in text
    for furniture in (
        "window.analytics",  # script
        "last reviewed",  # header
        "Glossary",  # nav
        "Related reading",  # aside
        "Get the newsletter",  # form
        "Cookie settings",  # footer
    ):
        assert furniture not in text


async def test_an_unusable_url_is_rejected_before_anything_is_opened(
    settings: Settings, connection: sqlite3.Connection
) -> None:
    """A URL that cannot be canonicalised fails as bad arguments, with no request at all.

    Catches an uncanonicalizable URL reaching the cache or the network, where it would
    either poison the table or spend a fetch on nothing.
    """
    result = await fetch(registry_for(settings, connection), url="ftp://example.com/x")

    assert result.ok is False
    assert result.error.code is ErrorCode.BAD_ARGUMENTS
    assert result.error.retryable is False
