"""The passage selector's job is to shrink a page without losing the answer.

Each test below protects one half of that bargain: the budget is respected, and what
survives is still the relevant, ordered, readable part of the source. Everything here is
offline — the module has no model, no network and no database to reach.
"""

from __future__ import annotations

import pytest

from evergrove_agent.config import Settings
from evergrove_agent.documents import GAP_MARKER, select_passages

QUESTION = "How do PostgreSQL B-tree indexes affect query planning?"

BTREE_SNIPPET = "A B-tree index stores keys in sorted order"
EXPLAIN_SNIPPET = "EXPLAIN reports whether the planner chose an index scan"

PAGE = """\
PostgreSQL Documentation

Home · Docs · Download · Community · Support

This website stores cookies on your computer to improve the browsing experience.

Introduction

The manual is organised into several parts and each part is maintained separately.

B-tree indexes

A B-tree index stores keys in sorted order, which is why the planner can use one for
equality and for range lookups alike.

Mailing lists

Subscribe to pgsql-general to ask other users about anything covered by the manual.

Reading EXPLAIN output

EXPLAIN reports whether the planner chose an index scan or a sequential scan for the
query you gave it.

Legal notice

Copyright 1996 to 2026 by the worldwide development group.
"""

_FILLER = (
    "Mailing lists\n\n"
    "Subscribe to pgsql-general to ask other users about anything covered by the "
    "manual, or browse the archives going back to 1997.\n\n"
)


def long_page() -> str:
    """A 20,000+ character page: two relevant sections buried in repeated boilerplate."""
    page = PAGE + _FILLER * 150
    assert len(page) > 20_000  # the size the plan sizes the selector against
    return page


@pytest.mark.parametrize(
    "text",
    [
        "",
        "PostgreSQL uses B-tree indexes by default.",
        "x" * 200,  # exactly the budget
    ],
)
def test_text_within_the_budget_is_returned_unchanged(text: str) -> None:
    """A source that already fits must not be reformatted, reordered, or clipped."""
    assert select_passages(text, QUESTION, max_chars=200) == text


def test_a_long_page_is_cut_to_the_budget_and_keeps_the_relevant_passages() -> None:
    """The whole point: honour the ceiling *and* keep the part that answers the question.

    Truncating the first 3,000 characters would also satisfy the ceiling, which is why
    the boilerplate assertions matter as much as the length one.
    """
    result = select_passages(long_page(), QUESTION, max_chars=3000)

    assert len(result) <= 3000
    assert BTREE_SNIPPET in result
    assert EXPLAIN_SNIPPET in result
    assert "cookies" not in result
    assert "Copyright" not in result


def test_passages_keep_document_order_and_mark_what_was_dropped() -> None:
    """Out-of-order passages read as non sequiturs to the model; an unmarked jump lies."""
    result = select_passages(PAGE, QUESTION, max_chars=400)

    assert result.index(BTREE_SNIPPET) < result.index(EXPLAIN_SNIPPET)
    assert GAP_MARKER in result


def test_a_selected_passage_carries_its_heading() -> None:
    """A passage saying "it stores keys in sorted order" is unusable without its section."""
    result = select_passages(PAGE, QUESTION, max_chars=400)

    assert result.index("B-tree indexes") < result.index(BTREE_SNIPPET)


def test_a_question_nothing_matches_still_returns_the_start_of_the_page() -> None:
    """A silent empty excerpt would reach the Researcher as a source with no content."""
    result = select_passages(long_page(), "medieval falconry techniques", max_chars=500)

    assert len(result) <= 500
    assert result.startswith("PostgreSQL Documentation")


def test_a_block_larger_than_the_whole_budget_is_truncated_not_dropped() -> None:
    """Same silent-empty failure, reached the other way: a page that is one long block."""
    single_block = "The query planner " * 500

    result = select_passages(single_block, QUESTION, max_chars=300)

    assert 0 < len(result) <= 300
    assert result.startswith("The query planner")
    assert result.endswith(GAP_MARKER)


def test_the_default_budget_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """SOURCE_EXCERPT_CHARS has to stay the one place the ceiling is set."""
    settings = Settings(_env_file=None, source_excerpt_chars=400)
    monkeypatch.setattr(
        "evergrove_agent.documents.excerpt.get_settings", lambda: settings
    )

    assert len(select_passages(long_page(), QUESTION)) <= 400
