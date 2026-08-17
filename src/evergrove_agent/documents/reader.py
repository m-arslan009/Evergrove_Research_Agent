"""Opening a file safely, and finding one section inside it.

Everything that is true of *every* format lives here — the path guard, the size guard,
and which reader handles which suffix — so the format modules stay about parsing and the
tool stays about modes. `read_document` and, later, `fetch_url`'s PDF branch both enter
through this module.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from evergrove_agent.config import Settings, get_settings
from evergrove_agent.documents.base import (
    BLOCK_SEPARATOR,
    DocumentReadError,
    OutlineEntry,
    ParsedDocument,
)
from evergrove_agent.documents.docx import read_docx
from evergrove_agent.documents.pdf import read_pdf
from evergrove_agent.documents.text import read_text
from evergrove_agent.schemas import ErrorCode

READERS: dict[str, Callable[[Path], ParsedDocument]] = {
    ".txt": read_text,
    ".md": read_text,
    ".pdf": read_pdf,
    ".docx": read_docx,
}
"""Suffix → reader. The supported-type list is this mapping and nothing else."""

_WORD_RE = re.compile(r"[a-z0-9]+")


def read_document_file(
    path: str | Path, *, settings: Settings | None = None
) -> ParsedDocument:
    """Read an attachment, or raise `DocumentReadError` with the code that explains why."""
    active = settings if settings is not None else get_settings()
    resolved = _resolve(path, active.allowed_attachment_dir)

    reader = READERS.get(resolved.suffix.lower())
    if reader is None:
        raise DocumentReadError(
            ErrorCode.UNSUPPORTED_TYPE,
            f"{resolved.name} is not a supported document type. Supported: "
            f"{', '.join(sorted(READERS))}.",
        )

    size = resolved.stat().st_size
    if size == 0:
        raise DocumentReadError(ErrorCode.EMPTY_FILE, f"{resolved.name} is empty.")
    if size > active.max_document_bytes:
        raise DocumentReadError(
            ErrorCode.BUDGET_EXCEEDED,
            f"{resolved.name} is {size} bytes, over the {active.max_document_bytes} "
            "byte limit for an attachment.",
        )

    document = reader(resolved)
    if not document.text.strip():
        raise DocumentReadError(
            ErrorCode.EMPTY_FILE, f"{resolved.name} contains no readable text."
        )
    return document


def resolve_attachment(path: str | Path, *, settings: Settings | None = None) -> Path:
    """Where `read_document` would look for `path`, or `DocumentReadError` saying why not.

    The guard `read_document_file` already applies, exposed on its own so a caller can ask
    the question *before* committing to a run. The CLI does exactly that: a mistyped
    `--attachment` otherwise surfaces as a `NOT_FOUND` tool failure several model calls into
    a research run, and the user pays for the whole run to be told about a typo.

    Deliberately the same function rather than a second existence check at the call site.
    The containment rule, the relative-path rule and the error codes have one definition,
    and a pre-flight that disagreed with the tool would be worse than none.
    """
    active = settings if settings is not None else get_settings()
    return _resolve(path, active.allowed_attachment_dir)


def _resolve(path: str | Path, allowed_dir: Path) -> Path:
    """Resolve `path` inside the attachment directory, or refuse it.

    A relative path is taken as relative to the allowed directory rather than the working
    directory — the caller may be an MCP client with no useful cwd (plan 30). The
    containment check runs before the existence check, so a probe outside the directory
    cannot learn whether a file is there.
    """
    allowed = Path(allowed_dir).expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = allowed / candidate

    try:
        # resolve() follows symlinks, so a link pointing out of the directory is caught.
        resolved = candidate.resolve()
    except OSError as exc:
        raise DocumentReadError(
            ErrorCode.NOT_FOUND, f"{path} could not be resolved: {exc}"
        ) from exc

    if resolved != allowed and not resolved.is_relative_to(allowed):
        raise DocumentReadError(
            ErrorCode.PATH_NOT_ALLOWED,
            f"{path} resolves outside the allowed attachment directory.",
        )
    if not resolved.is_file():
        raise DocumentReadError(
            ErrorCode.NOT_FOUND, f"{resolved.name} does not exist in the attachment directory."
        )
    return resolved


def select_section(document: ParsedDocument, hint: str) -> tuple[OutlineEntry, str] | None:
    """The outline entry best matching `hint` and its text, or `None` if nothing matches.

    Heading lookup, not passage selection: an exact or contained title wins outright,
    otherwise the most overlapping words. `select_passages` still does all the choosing
    *within* whatever this returns — the two do different jobs on different inputs.
    """
    if not document.outline:
        return None

    wanted = set(_WORD_RE.findall(hint.lower()))
    normalised = hint.strip().lower()
    best: tuple[int, OutlineEntry] | None = None

    for entry in document.outline:
        title = entry.title.strip().lower()
        if title == normalised:
            score = 1000
        elif normalised and (normalised in title or title in normalised):
            score = 500 + len(set(_WORD_RE.findall(title)) & wanted)
        else:
            score = len(set(_WORD_RE.findall(title)) & wanted)
        if score and (best is None or score > best[0]):
            best = (score, entry)

    if best is None:
        return None
    entry = best[1]
    return entry, document.text[entry.char_offset : _section_end(document, entry)]


def _section_end(document: ParsedDocument, entry: OutlineEntry) -> int:
    """Where a section stops: the next heading at the same or a higher level.

    Subsections therefore stay inside their parent — asking for a chapter gives the whole
    chapter, not the paragraph before its first subheading.
    """
    for candidate in document.outline:
        if candidate.char_offset > entry.char_offset and candidate.level <= entry.level:
            return candidate.char_offset - len(BLOCK_SEPARATOR)
    return len(document.text)
