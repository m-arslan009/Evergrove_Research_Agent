"""Source normalisation: canonicalisation, duplicate collapse, classification, ranking.

Offline and pure — no model, network, SQLite or search backend is involved.
"""

from __future__ import annotations

import pytest

from evergrove_agent.search import (
    RawSource,
    canonicalize_url,
    classify_domain,
    normalize_sources,
)
from evergrove_agent.tools import RunContext, ToolRegistry
from evergrove_agent.tools.normalize_sources import NormalizeSourcesTool


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # host and scheme casing
        (
            "https://Docs.Python.ORG/3/library/re.html",
            "https://docs.python.org/3/library/re.html",
        ),
        (
            "HTTPS://docs.python.org/3/library/re.html",
            "https://docs.python.org/3/library/re.html",
        ),
        # fragments and trailing slashes
        (
            "https://docs.python.org/3/library/re.html#search",
            "https://docs.python.org/3/library/re.html",
        ),
        ("https://docs.python.org/3/library/", "https://docs.python.org/3/library"),
        ("https://docs.python.org", "https://docs.python.org/"),
        # tracking parameters, and the ones that must survive them
        ("https://x.com/a?utm_source=n&utm_medium=e&id=7", "https://x.com/a?id=7"),
        ("https://x.com/a?utm_campaign=n&fbclid=abc", "https://x.com/a"),
        ("https://x.com/a?b=2&a=1", "https://x.com/a?b=2&a=1"),
        # ports, trailing dot, surrounding whitespace
        ("https://x.com:443/a", "https://x.com/a"),
        ("http://x.com:80/a", "http://x.com/a"),
        ("https://x.com:8443/a", "https://x.com:8443/a"),
        ("https://x.com./a", "https://x.com/a"),
        ("  https://x.com/a  ", "https://x.com/a"),
        # a scheme-less URL is assumed https rather than discarded
        ("example.com/a", "https://example.com/a"),
        # not usable as a source
        ("mailto:someone@x.com", None),
        ("javascript:alert(1)", None),
        ("ftp://x.com/a", None),
        ("https:///a", None),
        ("", None),
        ("   ", None),
        # A phrase, not a URL. The scheme-less branch above would otherwise make it
        # `https://not a url/` — a host no resolver can answer for, which `fetch_url`
        # would then spend two network attempts failing to reach.
        ("not a url", None),
    ],
)
def test_canonicalize_url(raw: str, expected: str | None) -> None:
    """The canonical form is what the cache and the dedup key are built on. Any drift
    here re-fetches pages we already hold and lists one page twice in a report."""
    assert canonicalize_url(raw) == expected


def test_duplicates_collapse_into_one_entry_that_keeps_the_best_metadata() -> None:
    """A backend routinely returns the same page more than once, with different tracking
    tails and different halves of its metadata. Without this the agent spends two of its
    four fetch calls on one page and cites it twice."""
    result = normalize_sources(
        [
            RawSource(
                url="https://docs.python.org/3/library/re.html?utm_source=news",
                snippet="Regular expression operations",
                source_backend="fixture",
            ),
            RawSource(
                url="https://docs.python.org/3/library/re.html#search",
                title="re - Regular expression operations",
            ),
            RawSource(
                url="https://DOCS.python.org/3/library/re.html/", title="ignored"
            ),
        ]
    )

    assert result.duplicates_removed == 2
    assert result.dropped == 0
    assert [source.url for source in result.sources] == [
        "https://docs.python.org/3/library/re.html"
    ]

    kept = result.sources[0]
    assert kept.snippet == "Regular expression operations"  # from the first occurrence
    assert kept.title == "re - Regular expression operations"  # filled by a duplicate
    assert kept.source_backend == "fixture"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("python.org", "official"),
        ("docs.python.org", "official"),  # matched through a subdomain
        ("www.python.org", "official"),  # www equivalence applies at lookup
        ("PYTHON.ORG", "official"),
        ("w3.org", "standards"),
        ("nist.gov", "standards"),  # a specific entry beats the broad "gov" rule
        ("data.cdc.gov", "primary"),  # ...which still applies everywhere else
        ("mit.edu", "primary"),
        ("arxiv.org", "primary"),
        ("stackoverflow.com", "secondary"),
        ("some-blog.example", "unknown"),
        ("", "unknown"),
    ],
)
def test_classify_domain(host: str, expected: str) -> None:
    """The class decides which source the report cites first; a domain landing in the
    wrong one is how a blog gets presented to the user as official documentation."""
    assert classify_domain(host) == expected


def test_ranking_is_authority_first_and_stable_within_a_class() -> None:
    """Two identical runs must produce the same order, and official documentation must
    outrank a blog that merely mentions the same API."""
    raw = [
        RawSource(url="https://medium.com/@someone/regex-tips"),
        RawSource(url="https://mit.edu/course/notes"),
        RawSource(url="https://stackoverflow.com/questions/1"),
        RawSource(url="https://docs.python.org/3/library/re.html"),
        RawSource(url="https://some-blog.example/whatever"),
        RawSource(url="https://www.w3.org/TR/html52/"),
    ]

    assert [source.url for source in normalize_sources(raw).sources] == [
        "https://docs.python.org/3/library/re.html",
        "https://www.w3.org/TR/html52",  # classified via www, URL keeps its host
        "https://mit.edu/course/notes",
        "https://medium.com/@someone/regex-tips",  # first secondary in input order
        "https://stackoverflow.com/questions/1",
        "https://some-blog.example/whatever",
    ]


async def test_tool_runs_through_the_registry_and_reports_unusable_urls() -> None:
    """The tool is reached the same way every other tool is. An unusable URL has to be a
    counted outcome, because an exception here would take down the whole run."""
    registry = ToolRegistry()
    registry.register(NormalizeSourcesTool())

    result = await registry.call(
        "normalize_sources",
        {
            "sources": [
                {"url": "mailto:someone@example.com"},
                {"url": "   "},
                {
                    "url": "https://www.postgresql.org/docs/current/indexes.html?utm_campaign=x",
                    "title": "PostgreSQL: Indexes",
                },
            ]
        },
        RunContext(),
    )

    assert result.ok
    assert result.error is None
    assert result.data.dropped == 2
    assert [source.url for source in result.data.sources] == [
        "https://www.postgresql.org/docs/current/indexes.html"
    ]
    assert result.data.sources[0].domain_class == "official"
