"""Plain text and Markdown.

The simplest reader, and the one that defines what "structure" means for the others:
blank-line-separated blocks, with Markdown headings kept in the text (so the passage
selector can carry a heading with its body) and lifted into the outline.

A `.txt` file with no Markdown headings gets an empty outline. That is deliberate —
treating every short line as a heading would fill the outline with noise, and `section`
mode already falls back to keyword selection when there is nothing to slice on.
"""

from __future__ import annotations

import re
from pathlib import Path

from evergrove_agent.documents.base import (
    Block,
    ParsedDocument,
    assemble,
    outline_from_blocks,
)

_ATX_RE = re.compile(r"^(#{1,6})\s+(\S.*)$")
_SETEXT_RE = re.compile(r"^(=+|-+)$")


def read_text(path: Path) -> ParsedDocument:
    """Read a `.txt` or `.md` file. Never raises: bad bytes degrade, they do not fail."""
    blocks = _split_blocks(_decode(path.read_bytes()))
    text, offsets = assemble(blocks)
    return ParsedDocument(text=text, outline=outline_from_blocks(blocks, offsets))


def _decode(data: bytes) -> str:
    """UTF-8, tolerating a BOM; malformed bytes become replacement characters.

    A file we cannot decode cleanly is still worth reading — losing a few characters
    beats failing a research run over one bad byte. Line endings are normalised so a
    Windows-authored file blocks the same way a Unix one does.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_blocks(text: str) -> list[Block]:
    """Blank-line-separated paragraphs, with ATX and setext headings as their own blocks."""
    lines = text.split("\n")
    blocks: list[Block] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            joined = "\n".join(paragraph).strip()
            if joined:
                blocks.append(Block(text=joined))
            paragraph.clear()

    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            flush()
            index += 1
            continue

        atx = _ATX_RE.match(stripped)
        if atx:
            flush()
            blocks.append(Block(text=stripped, heading_level=len(atx.group(1))))
            index += 1
            continue

        # A setext underline only makes a heading of the line that opens a block.
        following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        underline = _SETEXT_RE.match(following) if not paragraph else None
        if underline:
            blocks.append(
                Block(text=stripped, heading_level=1 if following[0] == "=" else 2)
            )
            index += 2
            continue

        paragraph.append(lines[index].rstrip())
        index += 1

    flush()
    return blocks
