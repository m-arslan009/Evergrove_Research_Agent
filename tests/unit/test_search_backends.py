"""Backend tests. Nothing here reaches SerpAPI, OpenAlex, Crossref or arXiv.

The live backends go through `respx`, which intercepts httpx; the fixture backend reads
files written into `tmp_path`, so a test never depends on what is committed under
`fixtures/search/`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from evergrove_agent.config import Settings
from evergrove_agent.search import (
    AcademicSearchBackend,
    FixtureSearchBackend,
    RawSource,
    SearchBackend,
    SearchBackendError,
    SerpApiSearchBackend,
    build_search_backend,
)

OPENALEX = "https://api.openalex.org/works"
CROSSREF = "https://api.crossref.org/works"
ARXIV = "https://export.arxiv.org/api/query"
SERPAPI = "https://serpapi.com/search.json"

ARXIV_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Learned Index Structures</title>
    <summary>We show that indexes can be replaced by learned models.</summary>
  </entry>
</feed>
"""


def write_fixture(
    directory: Path,
    filename: str,
    *,
    query: str,
    source_type: str = "docs",
    results: list[dict[str, str]] | None = None,
) -> Path:
    path = directory / filename
    path.write_text(
        json.dumps(
            {
                "query": query,
                "source_type": source_type,
                "results": results
                if results is not None
                else [{"url": "https://example.org/a", "title": "A", "snippet": "a"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def serpapi_body(*links: str) -> dict[str, Any]:
    return {
        "organic_results": [
            {"link": link, "title": f"title {n}", "snippet": f"snippet {n}"}
            for n, link in enumerate(links)
        ]
    }


class TestFixtureBackend:
    @pytest.mark.parametrize(
        "query",
        [
            "postgresql b-tree index",
            "  PostgreSQL   b-tree   index  ",
            "POSTGRESQL B-TREE INDEX",
        ],
    )
    async def test_replays_a_recording_however_the_query_is_spelled(
        self, tmp_path: Path, query: str
    ) -> None:
        """The index key must survive whitespace and case, or a recorded answer silently
        stops being found and development starts spending live quota."""
        write_fixture(tmp_path, "pg.json", query="postgresql b-tree index")

        results = await FixtureSearchBackend(directory=tmp_path).search(
            query, source_type="docs", max_results=6
        )

        assert [source.url for source in results] == ["https://example.org/a"]

    async def test_truncates_to_max_results_and_stamps_the_backend(
        self, tmp_path: Path
    ) -> None:
        """A replay that returns more than was asked for would blow a later budget, and
        an unstamped source cannot be traced back to where it came from."""
        write_fixture(
            tmp_path,
            "many.json",
            query="indexes",
            results=[
                {"url": f"https://example.org/{n}", "title": "t", "snippet": "s"}
                for n in range(5)
            ],
        )

        results = await FixtureSearchBackend(directory=tmp_path).search(
            "indexes", source_type="docs", max_results=2
        )

        assert len(results) == 2
        assert {source.source_backend for source in results} == {"fixture"}

    async def test_a_different_source_type_is_a_different_recording(
        self, tmp_path: Path
    ) -> None:
        """`web_search`'s ladder falls back to source_type="general"; if the fixture
        backend ignored the type, that fallback would replay the wrong recording."""
        write_fixture(tmp_path, "docs.json", query="indexes", source_type="docs")

        with pytest.raises(SearchBackendError):
            await FixtureSearchBackend(directory=tmp_path).search(
                "indexes", source_type="general", max_results=6
            )

    @pytest.mark.parametrize(
        ("scenario", "expected"),
        [
            ("missing", "no recorded response"),
            ("malformed", "unreadable search fixture"),
            ("duplicate", "replay would be ambiguous"),
        ],
    )
    async def test_a_recording_it_cannot_replay_is_a_loud_failure(
        self, tmp_path: Path, scenario: str, expected: str
    ) -> None:
        """Every one of these must be distinguishable from "that query found nothing" —
        an empty list here would look like a successful search and never be retried."""
        if scenario == "malformed":
            (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
        if scenario == "duplicate":
            write_fixture(tmp_path, "one.json", query="indexes")
            write_fixture(tmp_path, "two.json", query="INDEXES")

        with pytest.raises(SearchBackendError, match=expected):
            await FixtureSearchBackend(directory=tmp_path).search(
                "indexes", source_type="docs", max_results=6
            )

    async def test_the_committed_fixture_answers_the_default_configuration(
        self, settings: Settings
    ) -> None:
        """SEARCH_BACKEND=fixture is the committed default, so an out-of-the-box clone
        must be able to answer at least one query without recording anything first."""
        results = await FixtureSearchBackend(settings).search(
            "postgresql b-tree index", source_type="docs", max_results=6
        )

        assert results
        assert results[0].url.startswith("https://")


class TestSerpApiBackend:
    @pytest.fixture
    def settings(self) -> Settings:
        return Settings(_env_file=None, serpapi_api_key="test-key")

    @respx.mock
    async def test_parses_organic_results_and_sends_the_query(
        self, settings: Settings
    ) -> None:
        """The field mapping is the whole backend; a response-shape change has to fail
        here rather than on the first live call."""
        route = respx.get(SERPAPI).mock(
            return_value=httpx.Response(200, json=serpapi_body("https://example.org/a"))
        )

        results = await SerpApiSearchBackend(settings).search(
            "b-tree", source_type="docs", max_results=6
        )

        request = route.calls.last.request
        assert results == [
            RawSource(
                url="https://example.org/a",
                title="title 0",
                snippet="snippet 0",
                source_backend="serpapi",
            )
        ]
        assert request.url.params["q"] == "b-tree documentation"
        assert request.url.params["num"] == "6"
        assert request.url.params["api_key"] == "test-key"

    @respx.mock
    async def test_a_query_with_no_hits_is_an_empty_list_not_a_failure(
        self, settings: Settings
    ) -> None:
        """SerpAPI reports "found nothing" through its `error` field. Raising on it would
        send web_search down the retry ladder for a search that already succeeded."""
        respx.get(SERPAPI).mock(
            return_value=httpx.Response(
                200,
                json={
                    "error": "Google hasn't returned any results for this query.",
                    "search_information": {"organic_results_state": "Fully empty"},
                },
            )
        )

        results = await SerpApiSearchBackend(settings).search(
            "zzzz", source_type="general", max_results=6
        )

        assert results == []

    @pytest.mark.parametrize(
        ("response", "retryable"),
        [
            (httpx.Response(401, text="bad key"), False),
            (httpx.Response(429, text="run out"), False),
            (httpx.Response(500, text="boom"), True),
            (httpx.Response(200, text="<html>not json</html>"), False),
        ],
        ids=["rejected-key", "tier-exhausted", "server-error", "not-json"],
    )
    @respx.mock
    async def test_classifies_failures_by_whether_a_retry_could_help(
        self, settings: Settings, response: httpx.Response, retryable: bool
    ) -> None:
        """`retryable` is what stops the retry step spending the rest of the month on a
        rejected key or an exhausted tier."""
        respx.get(SERPAPI).mock(return_value=response)

        with pytest.raises(SearchBackendError) as caught:
            await SerpApiSearchBackend(settings).search(
                "b-tree", source_type="general", max_results=6
            )

        assert caught.value.retryable is retryable

    @respx.mock
    async def test_an_unreachable_provider_is_retryable(self, settings: Settings) -> None:
        respx.get(SERPAPI).mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(SearchBackendError, match="could not reach SerpAPI") as caught:
            await SerpApiSearchBackend(settings).search(
                "b-tree", source_type="general", max_results=6
            )

        assert caught.value.retryable is True

    @respx.mock
    async def test_without_a_key_it_never_reaches_the_network(self) -> None:
        """A keyless run must fail before the request, not after — SerpAPI counts calls
        it rejects."""
        route = respx.get(SERPAPI)

        with pytest.raises(SearchBackendError, match="SERPAPI_API_KEY"):
            await SerpApiSearchBackend(Settings(_env_file=None)).search(
                "b-tree", source_type="general", max_results=6
            )

        assert route.called is False


class TestAcademicBackend:
    @respx.mock
    async def test_openalex_results_prefer_the_doi_and_rebuild_the_abstract(
        self, settings: Settings
    ) -> None:
        """OpenAlex stores abstracts as word -> positions; without the reconstruction an
        academic result reaches the model as a bare title."""
        respx.get(OPENALEX).mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "https://openalex.org/W123",
                            "doi": "https://doi.org/10.1000/abc",
                            "display_name": "On B-trees",
                            "abstract_inverted_index": {
                                "B-trees": [0],
                                "are": [1],
                                "sorted": [2],
                            },
                        }
                    ]
                },
            )
        )

        results = await AcademicSearchBackend(settings).search(
            "b-tree", source_type="academic", max_results=6
        )

        assert results[0].url == "https://doi.org/10.1000/abc"
        assert results[0].title == "On B-trees"
        assert results[0].snippet == "B-trees are sorted"
        assert results[0].source_backend == "academic"

    @respx.mock
    async def test_steps_over_a_broken_or_empty_provider_to_the_next_one(
        self, settings: Settings
    ) -> None:
        """Three providers exist so one being down is survivable; if the ladder broke,
        scholarly coverage would disappear silently."""
        respx.get(OPENALEX).mock(return_value=httpx.Response(500, text="boom"))
        crossref = respx.get(CROSSREF).mock(
            return_value=httpx.Response(200, json={"message": {"items": []}})
        )
        respx.get(ARXIV).mock(return_value=httpx.Response(200, text=ARXIV_FEED))

        results = await AcademicSearchBackend(settings).search(
            "learned index", source_type="academic", max_results=6
        )

        assert crossref.called
        assert results[0].url == "http://arxiv.org/abs/2401.00001v1"
        assert results[0].title == "Learned Index Structures"

    @respx.mock
    async def test_crossref_falls_back_to_the_journal_when_there_is_no_abstract(
        self, settings: Settings
    ) -> None:
        """Crossref abstracts are optional and arrive as JATS markup; a snippet of raw
        XML tags would be worse than the journal name."""
        respx.get(OPENALEX).mock(return_value=httpx.Response(200, json={"results": []}))
        respx.get(CROSSREF).mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": {
                        "items": [
                            {
                                "URL": "https://doi.org/10.1000/xyz",
                                "title": ["Index Anatomy"],
                                "container-title": ["Journal of Storage"],
                            },
                            {
                                "URL": "https://doi.org/10.1000/pqr",
                                "title": ["Tree Shapes"],
                                "abstract": "<jats:p>Shapes of <b>trees</b>.</jats:p>",
                            },
                        ]
                    }
                },
            )
        )

        results = await AcademicSearchBackend(settings).search(
            "indexes", source_type="academic", max_results=6
        )

        assert results[0].snippet == "Journal of Storage"
        assert results[1].snippet == "Shapes of trees ."

    @respx.mock
    async def test_every_provider_failing_is_an_error_not_an_empty_list(
        self, settings: Settings
    ) -> None:
        """A total outage must not be reported as "this question has no scholarly
        sources" — the model would accept that and move on."""
        for url in (OPENALEX, CROSSREF, ARXIV):
            respx.get(url).mock(return_value=httpx.Response(503, text="down"))

        with pytest.raises(SearchBackendError, match="every academic provider failed"):
            await AcademicSearchBackend(settings).search(
                "indexes", source_type="academic", max_results=6
            )

    @respx.mock
    async def test_no_provider_finding_anything_is_an_empty_list(
        self, settings: Settings
    ) -> None:
        respx.get(OPENALEX).mock(return_value=httpx.Response(200, json={"results": []}))
        respx.get(CROSSREF).mock(
            return_value=httpx.Response(200, json={"message": {"items": []}})
        )
        respx.get(ARXIV).mock(
            return_value=httpx.Response(
                200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            )
        )

        results = await AcademicSearchBackend(settings).search(
            "zzzz", source_type="academic", max_results=6
        )

        assert results == []


class TestBackendSelection:
    @pytest.mark.parametrize(
        "backend",
        [FixtureSearchBackend, SerpApiSearchBackend, AcademicSearchBackend],
    )
    def test_every_backend_satisfies_the_shared_contract(
        self, backend: type, settings: Settings
    ) -> None:
        """The contract is what lets web_search compose a backend it never names; a
        signature drifting out of it would only surface when that backend is selected."""
        assert isinstance(backend(settings), SearchBackend)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("fixture", FixtureSearchBackend),
            ("serpapi", SerpApiSearchBackend),
            ("academic", AcademicSearchBackend),
        ],
    )
    def test_resolves_the_configured_name(
        self, name: str, expected: type, settings: Settings
    ) -> None:
        """A SEARCH_BACKEND that silently resolved to the wrong backend could spend live
        quota when the fixture backend was asked for."""
        configured = Settings(_env_file=None, search_backend=name)

        assert isinstance(build_search_backend(settings=configured), expected)
        assert isinstance(build_search_backend(name, settings), expected)

    def test_the_unimplemented_backend_fails_loudly(self, settings: Settings) -> None:
        with pytest.raises(SearchBackendError, match="not implemented"):
            build_search_backend("ddgs", settings)
