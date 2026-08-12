"""DOCX, with no dependency.

A `.docx` is a zip holding `word/document.xml`, so stdlib `zipfile` and `ElementTree`
read it directly. `python-docx` would add tables-and-numbering handling and a compiled
`lxml` to a runtime that is otherwise three pure-Python packages — and `outline` and
`section` need only paragraphs and their heading styles, which is the part that is
trivial to read.

Word marks a heading with a paragraph style, not with markup, so that style name is the
only structure signal here. A document written without heading styles gets an empty
outline, exactly like a `.txt` with no `#` lines.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from evergrove_agent.documents.base import (
    Block,
    DocumentReadError,
    ParsedDocument,
    assemble,
    outline_from_blocks,
)
from evergrove_agent.schemas import ErrorCode

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DOCUMENT_PART = "word/document.xml"
_HEADING_STYLE_RE = re.compile(r"^heading\s*([1-6])$", re.IGNORECASE)
# The two styles Word applies to a document's own title block.
_TITLE_STYLES = {"title": 1, "subtitle": 2}


def read_docx(path: Path) -> ParsedDocument:
    """Extract paragraphs and heading structure. Raises `DocumentReadError` on a bad file."""
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read(_DOCUMENT_PART)
        root = ElementTree.fromstring(xml)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, OSError) as exc:
        raise DocumentReadError(
            ErrorCode.CORRUPT_DOCX,
            f"{path.name} could not be read as a DOCX: {type(exc).__name__}: {exc}",
        ) from exc

    blocks = _blocks(root)
    text, offsets = assemble(blocks)
    return ParsedDocument(text=text, outline=outline_from_blocks(blocks, offsets))


def _blocks(root: ElementTree.Element) -> list[Block]:
    """Every non-empty paragraph in document order, including those inside tables."""
    blocks: list[Block] = []
    for paragraph in root.iter(f"{_W}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{_W}t")).strip()
        if text:
            blocks.append(Block(text=text, heading_level=_heading_level(paragraph)))
    return blocks


def _heading_level(paragraph: ElementTree.Element) -> int | None:
    """The paragraph's heading level from its Word style, or `None` for body text."""
    style = paragraph.find(f"{_W}pPr/{_W}pStyle")
    if style is None:
        return None
    name = (style.get(f"{_W}val") or "").strip()
    match = _HEADING_STYLE_RE.match(name)
    if match:
        return int(match.group(1))
    return _TITLE_STYLES.get(name.lower())
