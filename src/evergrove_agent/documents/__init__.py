"""Turning a source into text the model can afford to read.

The readers (`pdf.py`, `text.py`, `html.py`) arrive with `read_document` and
`fetch_url`; the passage selector every one of them ends with is here first, because
it is the piece that decides how much of a source the model ever sees.
"""

from __future__ import annotations

from evergrove_agent.documents.excerpt import GAP_MARKER, select_passages

__all__ = ["GAP_MARKER", "select_passages"]
