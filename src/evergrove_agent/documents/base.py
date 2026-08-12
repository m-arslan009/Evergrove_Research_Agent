"""What every document reader returns, and how it fails.

The readers (`text.py`, `pdf.py`, `docx.py`) differ only in how they get from bytes to
paragraphs. Everything downstream — the `read_document` tool, the outline, `section`
slicing — works on the shapes below, so a new format costs one module and no changes
anywhere else.

Readers *raise* `DocumentReadError`; the tool converts it to a `ToolResult` in one
place. Same split as `SearchBackendError` and `LLMError`: the failure ladder is the
tool's control flow, and one conversion point is what makes "tools never raise" true
rather than hoped for.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from evergrove_agent.schemas import ErrorCode

BLOCK_SEPARATOR = "\n\n"
"""Blank line between blocks — what `excerpt.select_passages` splits paragraphs on."""


class OutlineEntry(BaseModel):
    """One heading found in a document, and where it starts.

    `char_offset` indexes into `ParsedDocument.text`, which is what lets `section` mode
    slice a heading's contents without re-parsing the file.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    level: int = Field(ge=1, le=6)
    char_offset: int = Field(ge=0)
    page: int | None = Field(default=None, ge=1, description="PDF page, 1-based.")


@dataclass(frozen=True)
class Block:
    """One paragraph or heading, before the blocks are joined into a document."""

    text: str
    heading_level: int | None = None
    page: int | None = None


@dataclass(frozen=True)
class ParsedDocument:
    """A file, as plain text plus whatever structure the reader could recover."""

    text: str
    outline: list[OutlineEntry] = field(default_factory=list)
    page_count: int | None = None


class DocumentReadError(Exception):
    """A read that failed in a way the agent should be told about, with its code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def assemble(blocks: Sequence[Block]) -> tuple[str, list[int]]:
    """Join `blocks` with blank lines; return the text and each block's char offset.

    The offsets are the only reason this is shared: every reader needs them to point an
    `OutlineEntry` at the right place, and computing them twice is how they drift.
    """
    parts: list[str] = []
    offsets: list[int] = []
    position = 0
    for block in blocks:
        offsets.append(position)
        parts.append(block.text)
        position += len(block.text) + len(BLOCK_SEPARATOR)
    return BLOCK_SEPARATOR.join(parts), offsets


def outline_from_blocks(
    blocks: Sequence[Block], offsets: Sequence[int]
) -> list[OutlineEntry]:
    """The heading blocks, as outline entries. Formats with inline headings use this."""
    return [
        OutlineEntry(
            title=block.text.lstrip("#").strip() or block.text,
            level=block.heading_level,
            char_offset=offset,
            page=block.page,
        )
        for block, offset in zip(blocks, offsets, strict=True)
        if block.heading_level is not None
    ]
