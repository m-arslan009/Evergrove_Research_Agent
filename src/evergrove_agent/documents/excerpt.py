"""Deterministic passage selection (plan section 14.4).

Extraction is only half the job. A clean documentation page is still 20,000+ characters,
and on a CPU-bound machine every one of them costs prefill time. This module scores
paragraph blocks against the research question and returns only the best of them, in
document order, with their heading context — the difference between a small model
reasoning over 800 focused tokens and 3,000 padded with navigation and cookie banners.

Keyword overlap and nothing else: no model, no embeddings, no network, no database. The
same page and question therefore always produce the same excerpt. Both `fetch_url` and
`read_document` end in this function (plan section 11), which is why `SOURCE_EXCERPT_CHARS`
— what the model sees — is a setting separate from the `max_chars` those tools extract
and cache.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from evergrove_agent.config import get_settings

GAP_MARKER = "[...]"
"""Marks omitted text, so the model can tell a jump from a non sequitur."""

_SEPARATOR = "\n\n"

# --- scoring weights ----------------------------------------------------------------
# A weighted sum, not a tuned model. Breadth beats repetition: a question term the block
# has not mentioned yet is worth more than another occurrence of one it already has.
_DISTINCT_TERM_WEIGHT = 3
_OCCURRENCE_CAP = 10
_HEADING_MATCH_BONUS = 2
_CODE_BONUS = 1
_DEFINITION_BONUS = 1

# --- block and heading recognition ---------------------------------------------------
_BLOCK_SPLIT_RE = re.compile(r"\n\s*\n")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_SETEXT_UNDERLINE_RE = re.compile(r"^[=-]{3,}$")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s")
_MAX_HEADING_CHARS = 80

# Fenced or indented — the two forms that survive both markdown and HTML extraction.
_CODE_RE = re.compile(r"^(?:```|~~~|    \S|\t\S)", re.MULTILINE)
# Definition-shaped prose. "is"/"are" alone match almost everything, so they only count
# when followed by an article.
_DEFINITION_RE = re.compile(
    r"\b(?:is|are)\s+(?:a|an|the)\b|\brefers to\b|\bmeans\b|\bstands for\b",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    """
    about all also an and any are as at be been but by can could did do does for from
    get had has have how i if in into is it its me more my no not of on or our out over
    should so some such than that the their them then there these they this those to
    too up use used using was we were what when where which while who why will with
    would you your
    """.split()
)


@dataclass(frozen=True)
class _Block:
    """One paragraph of the source, with the heading it sits under."""

    index: int
    text: str
    is_heading: bool
    heading_index: int | None


def select_passages(text: str, question: str, *, max_chars: int | None = None) -> str:
    """Return the passages of `text` most relevant to `question`, within the budget.

    `max_chars` defaults to `Settings.source_excerpt_chars`. Text already inside the
    budget is returned unchanged; anything longer comes back as the best-scoring blocks
    in their original order, each carrying its heading, with `GAP_MARKER` where blocks
    were left out. Never raises: a page nothing matches still returns its opening.
    """
    limit = max_chars if max_chars is not None else get_settings().source_excerpt_chars
    if len(text) <= limit:
        return text

    blocks = _split_blocks(text)
    terms = set(_tokenize(question))
    if not blocks or not terms:
        return _truncate(text, limit)

    heading_terms = {
        block.index: set(_tokenize(block.text)) for block in blocks if block.is_heading
    }
    scored: list[tuple[int, _Block]] = []
    for block in blocks:
        if block.is_heading:
            continue  # a heading travels with its body; it is not a passage itself
        score = _score(block, terms, heading_terms.get(block.heading_index, set()))
        if score:
            scored.append((score, block))
    if not scored:
        return _truncate(text, limit)

    chosen = _fill(scored, blocks, limit)
    if not chosen:
        # Even the single best block is larger than the whole budget — cut it rather
        # than return nothing.
        best = max(scored, key=lambda pair: (pair[0], -pair[1].index))[1]
        return _truncate(best.text, limit)
    return _truncate(_render(chosen, blocks), limit)


# --- parsing ---------------------------------------------------------------------------


def _split_blocks(text: str) -> list[_Block]:
    """Blank-line-separated paragraphs, each tagged with the heading in force above it."""
    blocks: list[_Block] = []
    current_heading: int | None = None
    for raw in _BLOCK_SPLIT_RE.split(text):
        block_text = raw.strip()
        if not block_text:
            continue
        index = len(blocks)
        heading = _is_heading(block_text)
        blocks.append(
            _Block(
                index=index,
                text=block_text,
                is_heading=heading,
                heading_index=None if heading else current_heading,
            )
        )
        if heading:
            current_heading = index
    return blocks


def _is_heading(block: str) -> bool:
    """Markdown, setext, or a short unpunctuated line — the forms our readers emit."""
    lines = block.splitlines()
    if _MD_HEADING_RE.match(lines[0]):
        return True
    if len(lines) == 2 and _SETEXT_UNDERLINE_RE.match(lines[1].strip()):
        return True
    if len(lines) != 1:
        return False
    line = lines[0].strip()
    return (
        0 < len(line) <= _MAX_HEADING_CHARS
        and not _LIST_ITEM_RE.match(line)
        and line[-1] not in ".!?,;"
    )


def _tokenize(text: str) -> list[str]:
    """Lowercase content words, plural-folded so `indexes` meets `index`."""
    tokens = []
    for token in _TOKEN_RE.findall(text.lower()):
        if len(token) < 2 or token in _STOPWORDS:
            continue
        tokens.append(_fold_plural(token))
    return tokens


def _fold_plural(token: str) -> str:
    """The three common English plurals, by rule. Deliberately not a stemmer."""
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("es") and token[-3] in "sxzh":
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


# --- scoring and selection ----------------------------------------------------------------


def _score(block: _Block, terms: set[str], heading_terms: set[str]) -> int:
    """How well one block answers the question. Zero means it is not a candidate.

    A block that shares no term with the question scores nothing whatever its heading
    says: the excerpt has to contain the question's words to be worth the model's time.
    """
    counts = Counter(_tokenize(block.text))
    distinct = sum(1 for term in terms if counts[term])
    if not distinct:
        return 0

    occurrences = sum(counts[term] for term in terms)
    score = _DISTINCT_TERM_WEIGHT * distinct + min(occurrences, _OCCURRENCE_CAP)
    if terms & heading_terms:
        score += _HEADING_MATCH_BONUS
    if _CODE_RE.search(block.text):
        score += _CODE_BONUS
    if _DEFINITION_RE.search(block.text):
        score += _DEFINITION_BONUS
    return score


def _fill(
    scored: list[tuple[int, _Block]], blocks: list[_Block], limit: int
) -> set[int]:
    """Best score first, skipping any block that no longer fits. Ties go to the earlier one.

    The per-block reserve covers the separator and a possible gap marker, so the rendered
    excerpt cannot overrun the budget it was packed against.
    """
    reserve = len(_SEPARATOR) * 2 + len(GAP_MARKER)
    used = 0
    chosen: set[int] = set()
    headings_counted: set[int] = set()

    for _, block in sorted(scored, key=lambda pair: (-pair[0], pair[1].index)):
        cost = len(block.text) + reserve
        heading_index = block.heading_index
        if heading_index is not None and heading_index not in headings_counted:
            cost += len(blocks[heading_index].text) + len(_SEPARATOR)
        if used + cost > limit:
            continue
        used += cost
        chosen.add(block.index)
        if heading_index is not None:
            headings_counted.add(heading_index)
    return chosen


def _render(chosen: set[int], blocks: list[_Block]) -> str:
    """The chosen blocks back in document order, with headings and gap markers."""
    parts: list[str] = []
    previous: int | None = None
    last_heading: int | None = None

    for index in sorted(chosen):
        block = blocks[index]
        heading_index = block.heading_index
        emit_heading = heading_index is not None and heading_index != last_heading

        skipped = set(range(previous + 1, index)) if previous is not None else set()
        if emit_heading:
            skipped.discard(heading_index)
        if skipped:
            parts.append(GAP_MARKER)

        if emit_heading:
            parts.append(blocks[heading_index].text)
            last_heading = heading_index
        parts.append(block.text)
        previous = index

    return _SEPARATOR.join(parts)


def _truncate(text: str, limit: int) -> str:
    """Cut to `limit` at a word boundary, marking that something was dropped."""
    if len(text) <= limit:
        return text
    keep = max(1, limit - len(GAP_MARKER) - 1)
    cut = text[:keep]
    boundary = cut.rfind(" ")
    if boundary > keep // 2:
        cut = cut[:boundary]
    return f"{cut.rstrip()} {GAP_MARKER}"
