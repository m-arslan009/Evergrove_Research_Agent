"""PDF, via `pypdf` — the library the plan names.

Three failures matter here and each gets its own code, because the agent can act on the
difference: a password-protected file is hopeless, a corrupt one is hopeless in a
different way, and a scanned one has pages but no text layer — the case where a user
thinks they attached a readable document and did not. There is no OCR, by decision.

Structure comes from the PDF's own bookmarks when it has them. When it does not, the
outline is empty rather than guessed: invented headings would make `section` mode slice
at the wrong place, which is worse than falling back to keyword selection.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from evergrove_agent.documents.base import (
    Block,
    DocumentReadError,
    OutlineEntry,
    ParsedDocument,
    assemble,
)
from evergrove_agent.schemas import ErrorCode

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_MAX_OUTLINE_LEVEL = 6


def read_pdf(path: Path) -> ParsedDocument:
    """Extract text and bookmark structure. Raises `DocumentReadError`, never `pypdf`'s."""
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentReadError(
                ErrorCode.ENCRYPTED_PDF,
                f"{path.name} is password protected and cannot be read.",
            )
        blocks = _extract_blocks(reader)
        page_count = len(reader.pages)
    except DocumentReadError:
        raise
    except Exception as exc:  # every pypdf failure mode, including a truncated file
        raise DocumentReadError(
            ErrorCode.CORRUPT_PDF,
            f"{path.name} could not be parsed as a PDF: {type(exc).__name__}: {exc}",
        ) from exc

    if not blocks:
        raise DocumentReadError(
            ErrorCode.NO_TEXT_LAYER,
            f"{path.name} has {page_count} page(s) but no extractable text — it is "
            "probably a scan. Ask for a text-based copy; this reader does no OCR.",
        )

    text, offsets = assemble(blocks)
    return ParsedDocument(
        text=text,
        outline=_outline(reader, blocks, offsets),
        page_count=page_count,
    )


def _extract_blocks(reader: PdfReader) -> list[Block]:
    """Paragraphs across every page, each tagged with the page it came from.

    A page that fails to extract is skipped rather than fatal: one damaged page should
    not cost the reader the other two hundred.
    """
    blocks: list[Block] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception:
            continue
        for chunk in _PARAGRAPH_SPLIT_RE.split(raw.replace("\r\n", "\n")):
            cleaned = chunk.strip()
            if cleaned:
                blocks.append(Block(text=cleaned, page=number))
    return blocks


def _outline(
    reader: PdfReader, blocks: list[Block], offsets: list[int]
) -> list[OutlineEntry]:
    """The PDF's bookmarks, resolved to positions in the extracted text.

    Bookmarks address pages, not characters, so each one lands on the first block of its
    page (or the next page that produced one). A malformed outline yields no entries —
    it must not cost us a readable document.
    """
    page_offsets: dict[int, int] = {}
    for block, offset in zip(blocks, offsets, strict=True):
        if block.page is not None:
            page_offsets.setdefault(block.page, offset)
    if not page_offsets:
        return []

    entries: list[OutlineEntry] = []
    try:
        _walk(reader, reader.outline, 1, page_offsets, entries)
    except Exception:
        return []
    entries.sort(key=lambda entry: entry.char_offset)
    return entries


def _walk(
    reader: PdfReader,
    nodes: Any,
    level: int,
    page_offsets: dict[int, int],
    entries: list[OutlineEntry],
) -> None:
    """Flatten pypdf's nested bookmark list, nesting depth becoming the heading level."""
    for node in nodes:
        if isinstance(node, list):
            _walk(reader, node, min(level + 1, _MAX_OUTLINE_LEVEL), page_offsets, entries)
            continue
        title = str(getattr(node, "title", "") or "").strip()
        if not title:
            continue
        page = reader.get_destination_page_number(node) + 1
        offset = next(
            (page_offsets[candidate] for candidate in sorted(page_offsets) if candidate >= page),
            None,
        )
        if offset is not None:
            entries.append(
                OutlineEntry(title=title, level=level, char_offset=offset, page=page)
            )
