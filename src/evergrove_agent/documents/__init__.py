"""Turning a source into text the model can afford to read.

Two halves: the readers (`text.py`, `pdf.py`, `docx.py`, entered through `reader.py`)
turn a file into plain text plus its outline, and `excerpt.py` decides how much of that
text the model ever sees. `read_document` uses both; `fetch_url` will reuse the PDF
reader and the selector.
"""

from __future__ import annotations

from evergrove_agent.documents.base import (
    Block,
    DocumentReadError,
    OutlineEntry,
    ParsedDocument,
)
from evergrove_agent.documents.docx import read_docx
from evergrove_agent.documents.excerpt import GAP_MARKER, select_passages
from evergrove_agent.documents.pdf import read_pdf
from evergrove_agent.documents.reader import (
    READERS,
    read_document_file,
    select_section,
)
from evergrove_agent.documents.text import read_text

__all__ = [
    "READERS",
    "Block",
    "DocumentReadError",
    "GAP_MARKER",
    "OutlineEntry",
    "ParsedDocument",
    "read_docx",
    "read_document_file",
    "read_pdf",
    "read_text",
    "select_passages",
    "select_section",
]
