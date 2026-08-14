"""Day 2 acceptance: the finished tools, assembled, exercised the way a run will use them.

Every tool already has a suite proving it in isolation, with a stub backend, an injected
connection and a seeded cache. Nothing here re-proves any of that. What this file proves is
the part no unit test can: that the pieces still fit *together* — one `build_tool_registry`,
one SQLite file, the committed `fixtures/` tree, and each tool opening its own connection
per call exactly as the CLI leaves it.

Four claims, in order of what they would cost if they were wrong:

1. an offline run cannot reach the network or move the live-search counter;
2. what a tool writes to a cache, the same tool can read back on the next call — across
   two separate connections, which is what "one command is one call" actually means;
3. a search served from the cache costs no quota, on the real key with the real ledger;
4. every failure arrives as a code and an exit status, never a traceback.

Offline throughout: `respx` intercepts httpx, the fixture backend replays committed
recordings, and both the database and the search fixtures are pointed away from `DB_PATH`
or held to the committed tree. Where a route is registered it stands in for a server; where
none is, any request at all fails the test rather than leaving the machine.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from evergrove_agent.config import Settings
from evergrove_agent.memory import db, get_search_usage
from evergrove_agent.schemas import ErrorCode
from evergrove_agent.tools.base import RunContext
from evergrove_agent.tools.cli import build_parser, main, run
from evergrove_agent.tools.wiring import build_tool_registry

DOCS_QUERY = "postgresql b-tree index"
"""Recorded in `fixtures/search/postgresql-indexing.json` under `source_type=docs`."""

TOP_SOURCE = "https://www.postgresql.org/docs/current/indexes-types.html"
"""The first result that recording should rank to: official documentation, ahead of the
two non-`postgresql.org` entries in the same file."""

ARTICLE = Path(__file__).resolve().parents[2] / "fixtures" / "html" / "article.html"

COMMITTED_RECORDINGS = [
    ("postgresql b-tree index", "docs", 3),
    ("how to read a postgresql explain plan", "general", 3),
    ("learned index structures", "academic", 3),
    ("python asyncio task cancellation", "technical", 3),
    ("qwyzzx nonexistent research topic", "general", 0),
]
"""Every committed recording, as `(query, source_type, results)`. The last one records a
query that found nothing — an empty *success*, which is the one outcome no other fixture
can show offline."""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Committed fixtures for input, a temporary file for state.

    The search fixture directory and the attachment directory are left at their defaults,
    because a fresh clone answering with no configuration is part of what is being
    accepted. Only `DB_PATH` moves — a test must never write to the real cache or the real
    budget ledger.
    """
    settings = Settings(_env_file=None, db_path=tmp_path / "agent.sqlite3")
    # `read_document` resolves its own settings (the S9 limitation), so the default
    # attachment directory has to be installed where it reads it.
    monkeypatch.setattr(
        "evergrove_agent.documents.reader.get_settings",
        lambda: Settings(_env_file=None),
    )
    return settings


@pytest.fixture
def ledger(workspace: Settings) -> Iterator[sqlite3.Connection]:
    """A second handle on the same file, for reading what the tools recorded.

    Deliberately not injected into the registry: the point is that the tools manage their
    own connections, so this one only observes.
    """
    with db.open_database(workspace.db_path) as connection:
        yield connection


async def call(settings: Settings, name: str, **args: Any) -> Any:
    """One tool call through the assembled registry — the only sanctioned path."""
    return await build_tool_registry(settings).call(name, args, RunContext())


# --- the whole pass ---------------------------------------------------------------------------


@respx.mock
async def test_the_wired_stack_runs_a_research_pass_offline_end_to_end(
    workspace: Settings, ledger: sqlite3.Connection
) -> None:
    """Search, rank, open the best source, read the local brief — the Day 2 stack doing
    the job it was built for, in one process against one database.

    Catches what only composition can break: a registry whose tools no longer agree on the
    shapes they hand each other (`web_search` returning something `fetch_url` cannot be
    given a URL from), a fixture tree that moved out from under the committed defaults, and
    ranking that stops putting official documentation first once the real recording — not a
    hand-built stub — goes through it. The single registered route is the only server that
    exists here; the counter is read at the end because an offline pass that spent quota
    would still have looked like a pass.
    """
    page = respx.get(TOP_SOURCE).mock(
        return_value=httpx.Response(200, html=ARTICLE.read_text(encoding="utf-8"))
    )

    found = await call(workspace, "web_search", query=DOCS_QUERY, source_type="docs")
    assert found.ok, found.error
    assert found.data.results[0].url == TOP_SOURCE
    assert found.data.results[0].domain_class == "official"

    # The same URLs again, with one duplicate spelling, through the pipeline step the
    # agent will use to merge two searches.
    ranked = await call(
        workspace,
        "normalize_sources",
        sources=[
            {"url": source.url, "source_backend": "test"}
            for source in found.data.results
        ]
        + [{"url": f"{TOP_SOURCE}?utm_source=newsletter", "source_backend": "test"}],
    )
    assert ranked.ok, ranked.error
    assert ranked.data.duplicates_removed == 1
    assert [source.url for source in ranked.data.sources] == [
        source.url for source in found.data.results
    ]

    read_page = await call(
        workspace,
        "fetch_url",
        url=found.data.results[0].url,
        excerpt_for="how does a b-tree index answer a lookup",
    )
    assert read_page.ok, read_page.error
    assert page.call_count == 1
    assert "sorted order" in read_page.data.text
    assert "Cookie preferences" not in read_page.data.text  # boilerplate, not prose

    brief = await call(
        workspace, "read_document", path="documents/indexing-brief.md", mode="outline"
    )
    assert brief.ok, brief.error
    assert [entry.title for entry in brief.data.outline]
    assert brief.data.text == ""

    assert get_search_usage(ledger) == 0


# --- the caches, across calls that each open their own connection ------------------------------


@respx.mock
async def test_both_caches_answer_the_second_call_without_repeating_the_work(
    workspace: Settings,
) -> None:
    """Miss → work → hit, with nothing injected: each call opens and closes its own
    connection, the way the CLI and the agent loop leave it.

    The unit suites prove each cache by seeding one side and reading the other on a
    connection held open for the test. That cannot catch a write that never reaches the
    file, or a read that keys the row differently from the write — the two failures that
    would leave the caches looking healthy while every repeat pays full price: a second
    request to a stranger's server, and a second search against a 250-a-month tier.
    """
    page = respx.get(TOP_SOURCE).mock(
        return_value=httpx.Response(200, html=ARTICLE.read_text(encoding="utf-8"))
    )

    first_fetch = await call(workspace, "fetch_url", url=TOP_SOURCE)
    second_fetch = await call(workspace, "fetch_url", url=TOP_SOURCE)

    assert first_fetch.ok and second_fetch.ok, (first_fetch.error, second_fetch.error)
    assert first_fetch.from_cache is False
    assert second_fetch.from_cache is True
    assert page.call_count == 1
    assert second_fetch.data.text == first_fetch.data.text
    # The hit reports when the page was fetched, never when it was served: stamping `now`
    # on a hit would make a week-old cached page look freshly retrieved. (Not equality —
    # the row is written a beat before the fresh call takes its own timestamp.)
    assert second_fetch.data.retrieved_at <= first_fetch.data.retrieved_at

    first_search = await call(
        workspace, "web_search", query=DOCS_QUERY, source_type="docs"
    )
    second_search = await call(
        workspace, "web_search", query=f"  {DOCS_QUERY.upper()}  ", source_type="docs"
    )

    assert first_search.ok and second_search.ok, (first_search.error, second_search.error)
    assert first_search.from_cache is False
    # Case and whitespace variants share one entry, which is what makes the cache worth
    # having against a model that rewords the same question.
    assert second_search.from_cache is True
    assert [source.url for source in second_search.data.results] == [
        source.url for source in first_search.data.results
    ]


async def test_a_repeated_metered_search_is_served_from_the_cache_for_free(
    workspace: Settings, ledger: sqlite3.Connection
) -> None:
    """The quota claim, on the metered backend, with the real key and the real ledger.

    `fixture` can never move the counter, so proving "a cache hit costs no quota" against
    it proves nothing. This configures `serpapi`, stands a mocked server in for it, and
    checks the ledger either side: the first search is charged once, and the repeat is
    charged nothing because the cache answered before the reservation was claimed. Catches
    an ordering change — cache read after the quota check — that no unit test sees, because
    each of them seeds the cache instead of filling it through the tool.
    """
    metered = Settings(
        _env_file=None,
        db_path=workspace.db_path,
        search_backend="serpapi",
        serpapi_api_key="test-key",
    )
    with respx.mock:
        route = respx.get("https://serpapi.com/search.json").mock(
            return_value=httpx.Response(
                200,
                json={
                    "organic_results": [
                        {
                            "link": TOP_SOURCE,
                            "title": "PostgreSQL: Index Types",
                            "snippet": "B-tree is the default index type.",
                        }
                    ]
                },
            )
        )

        first = await call(metered, "web_search", query=DOCS_QUERY, source_type="docs")
        assert first.ok, first.error
        assert first.from_cache is False
        assert route.call_count == 1
        assert get_search_usage(ledger) == 1

        second = await call(metered, "web_search", query=DOCS_QUERY, source_type="docs")
        assert second.ok, second.error
        assert second.from_cache is True
        assert route.call_count == 1
        assert get_search_usage(ledger) == 1


# --- the offline default cannot go live --------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(("query", "source_type", "results"), COMMITTED_RECORDINGS)
async def test_every_committed_recording_replays_through_the_registry_for_free(
    workspace: Settings,
    ledger: sqlite3.Connection,
    query: str,
    source_type: str,
    results: int,
) -> None:
    """The committed default, answering all four source types out of a fresh clone.

    No routes are registered, so a fall-through to a live API raises instead of quietly
    spending the free tier — the one wiring mistake that costs real money. The backend
    suite replays these files at the backend; this replays them through the whole tool,
    which is where the routing rule ("`fixture` stays `fixture` for every source type") and
    the quota guard both live. The empty recording is included because a query that found
    nothing must stay a success, not a failure the ladder retries.
    """
    result = await call(
        workspace, "web_search", query=query, source_type=source_type, max_results=6
    )

    assert result.ok, result.error
    assert len(result.data.results) == results
    assert all(source.source_backend == "fixture" for source in result.data.results)
    assert get_search_usage(ledger) == 0


@respx.mock
async def test_an_unrecorded_query_is_refused_rather_than_sent_to_a_live_api(
    workspace: Settings, ledger: sqlite3.Connection
) -> None:
    """A miss in fixture mode is where a fall-through would actually happen.

    Every recorded query answers from disk whatever the code does; only an *un*recorded one
    reaches the end of the ladder. It must arrive as `SEARCH_UNAVAILABLE` — including after
    the widening rung retries under `general` — with no request attempted and the counter
    untouched. Catches a future fallback that resolves a fixture miss onto a live backend.
    """
    result = await call(
        workspace, "web_search", query="a topic nobody has recorded", source_type="docs"
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.SEARCH_UNAVAILABLE
    assert result.error.retryable is False  # a missing file will not appear on a retry
    assert get_search_usage(ledger) == 0


# --- failures reach the operator as codes, not tracebacks ---------------------------------------


@respx.mock
@pytest.mark.parametrize(
    ("argv", "code"),
    [
        # The registry's own validation, before any tool runs.
        (["search", "ab"], ErrorCode.BAD_ARGUMENTS),
        # A guard inside a tool.
        (["fetch", "not a url"], ErrorCode.BAD_ARGUMENTS),
        # The attachment containment check.
        (["read", "../../../etc/passwd"], ErrorCode.PATH_NOT_ALLOWED),
        # A real server answering badly.
        (["fetch", "https://example.com/gone"], ErrorCode.NOT_FOUND),
    ],
    ids=["registry-validation", "tool-guard", "path-containment", "http-status"],
)
async def test_each_failure_layer_reaches_the_cli_as_a_code_and_exit_1(
    workspace: Settings,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    code: ErrorCode,
) -> None:
    """A refusal has to be readable and scriptable, from whichever layer produced it.

    The four cases are four different layers, and the contract is one contract: the code on
    stderr, exit 1, and no traceback anywhere. Catches the failure that would make the CLI
    useless as the "rule the tools out first" instrument it exists to be — an exception
    escaping to the terminal, or a refusal exiting 0 so a shell cannot tell it from an
    answer.
    """
    respx.get("https://example.com/gone").mock(return_value=httpx.Response(404))

    status = await run(build_parser().parse_args(argv), settings=workspace)

    captured = capsys.readouterr()
    assert status == 1
    assert code.value in captured.err
    assert "Traceback" not in captured.err + captured.out
    assert captured.out == ""


@pytest.mark.parametrize(
    "argv", [["search"], ["frobnicate", "x"]], ids=["missing-argument", "unknown-command"]
)
def test_unusable_arguments_exit_2_before_a_tool_is_built(argv: list[str]) -> None:
    """Argparse's own code, kept distinct from a tool's refusal.

    Three exit codes only mean something if they stay three: 0 an answer, 1 a tool that
    said no, 2 a command that was never runnable. Catches a `main` that swallows
    `SystemExit` and reports an unusable command as an ordinary failure — at which point a
    script cannot tell a typo from an exhausted budget.
    """
    with pytest.raises(SystemExit) as exit_status:
        main(argv)

    assert exit_status.value.code == 2
