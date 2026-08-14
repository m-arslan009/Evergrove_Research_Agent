"""`read_document`: does each format come back readable, and does each failure come back
as the right code instead of an exception?

Every document here is built in `tmp_path` — a PDF with a real text layer, a DOCX zip —
so the suite stays offline, deterministic and free of committed binaries. The reading
budget and the attachment directory are patched per test; the real `.env` never
participates.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from evergrove_agent.config import Settings
from evergrove_agent.documents import GAP_MARKER
from evergrove_agent.schemas import ErrorCode
from evergrove_agent.tools import RunContext, ToolRegistry
from evergrove_agent.tools.read_document import ReadDocumentTool

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

MARKDOWN = """\
# Indexes

A B-tree index stores keys in sorted order.

## EXPLAIN

EXPLAIN reports whether the planner chose an index scan.
"""


def build_docx(paragraphs: list[tuple[str, str | None]]) -> bytes:
    """A minimal DOCX from (text, style) pairs; a style of `None` is body text."""
    body = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
        if style
        else f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        for text, style in paragraphs
    )
    document = (
        f'<?xml version="1.0"?><w:document xmlns:w="{WORD_NS}">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


@pytest.fixture
def attachments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_pdf: Callable[[list[str]], bytes],
) -> Path:
    """`tmp_path` as the allowed attachment directory, holding one file per format."""
    (tmp_path / "guide.md").write_text(MARKDOWN, encoding="utf-8")
    (tmp_path / "notes.txt").write_text(
        "A B-tree index stores keys in sorted order.\n\nEXPLAIN reports the plan.\n",
        encoding="utf-8",
    )
    (tmp_path / "paper.pdf").write_bytes(
        build_pdf(["Indexes", "A B-tree index stores keys in sorted order."])
    )
    (tmp_path / "brief.docx").write_bytes(
        build_docx(
            [
                ("Indexes", "Heading1"),
                ("A B-tree index stores keys in sorted order.", None),
                ("EXPLAIN", "Heading1"),
                ("EXPLAIN reports whether the planner chose an index scan.", None),
            ]
        )
    )
    _use(tmp_path, monkeypatch)
    return tmp_path


def _use(directory: Path, monkeypatch: pytest.MonkeyPatch, **overrides: object) -> Settings:
    """Point the reader at `directory` with defaults-only settings."""
    settings = Settings(_env_file=None, allowed_attachment_dir=directory, **overrides)
    monkeypatch.setattr(
        "evergrove_agent.documents.reader.get_settings", lambda: settings
    )
    return settings


async def _read(**args: object):
    """Call the tool the way the agent will — through the registry."""
    registry = ToolRegistry()
    registry.register(ReadDocumentTool())
    return await registry.call("read_document", args, RunContext())


# --- the formats -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "file_type", "page_count"),
    [
        ("guide.md", "md", None),
        ("notes.txt", "txt", None),
        ("paper.pdf", "pdf", 1),
        ("brief.docx", "docx", None),
    ],
)
async def test_full_mode_reads_every_supported_format(
    attachments: Path, name: str, file_type: str, page_count: int | None
) -> None:
    """Catches a reader that returns nothing, or one wired to the wrong suffix."""
    result = await _read(path=name, mode="full")

    assert result.ok, result.error
    assert "B-tree index stores keys in sorted order" in result.data.text
    assert result.data.file_type == file_type
    assert result.data.page_count == page_count
    assert result.data.truncated is False


@pytest.mark.parametrize("name", ["guide.md", "brief.docx"])
async def test_outline_mode_returns_headings_and_no_body(
    attachments: Path, name: str
) -> None:
    """Catches heading detection breaking, and outline mode leaking the whole document."""
    result = await _read(path=name, mode="outline")

    assert result.ok, result.error
    assert [entry.title for entry in result.data.outline] == ["Indexes", "EXPLAIN"]
    assert result.data.text == ""


@pytest.mark.parametrize("name", ["guide.md", "brief.docx"])
async def test_section_mode_returns_only_that_section(
    attachments: Path, name: str
) -> None:
    """Catches a slice that runs past its heading into the next section."""
    result = await _read(path=name, mode="section", section_hint="EXPLAIN")

    assert result.ok, result.error
    assert "planner chose an index scan" in result.data.text
    assert "sorted order" not in result.data.text


async def test_section_mode_falls_back_when_there_is_no_outline(
    attachments: Path,
) -> None:
    """A plain .txt has nothing to slice on; the hint must still return text, not fail."""
    result = await _read(path="notes.txt", mode="section", section_hint="EXPLAIN")

    assert result.ok, result.error
    assert "EXPLAIN" in result.data.text


async def test_unmatched_section_reports_the_headings_that_exist(
    attachments: Path,
) -> None:
    """Catches a silent empty result: the next call needs to know what it can ask for."""
    result = await _read(path="guide.md", mode="section", section_hint="replication")

    assert result.error.code is ErrorCode.NOT_FOUND
    assert "EXPLAIN" in result.error.message


# --- the committed attachments ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "file_type", "has_outline"),
    [
        ("documents/indexing-brief.md", "md", True),
        ("documents/session-notes.txt", "txt", False),
    ],
)
async def test_the_committed_attachments_open_under_the_default_directory(
    monkeypatch: pytest.MonkeyPatch, name: str, file_type: str, has_outline: bool
) -> None:
    """`ALLOWED_ATTACHMENT_DIR` defaults to `fixtures/`, so these two open with no
    configuration at all — that is what makes a fresh clone able to read something.

    Catches the fixtures being moved out from under the default directory, which would
    answer `PATH_NOT_ALLOWED` only when a person tried the CLI. The pair also keeps both
    outline paths exercised by real committed data: the Markdown file has headings, the
    text file deliberately has none.
    """
    _use(Settings(_env_file=None).allowed_attachment_dir, monkeypatch)

    result = await _read(path=name, mode="full")

    assert result.ok, result.error
    assert result.data.file_type == file_type
    assert "index" in result.data.text.lower()
    assert bool(result.data.outline) is has_outline


# --- budget, encoding, arguments -------------------------------------------------------


async def test_full_mode_respects_the_reading_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a future edit that returns raw text instead of routing to select_passages."""
    (tmp_path / "long.md").write_text(
        "# Indexes\n\n" + ("A B-tree index stores keys in sorted order. " * 200), encoding="utf-8"
    )
    settings = _use(tmp_path, monkeypatch, source_excerpt_chars=400)
    monkeypatch.setattr(
        "evergrove_agent.documents.excerpt.get_settings", lambda: settings
    )

    result = await _read(path="long.md", mode="full")

    assert result.ok, result.error
    assert len(result.data.text) <= 400
    assert GAP_MARKER in result.data.text
    assert result.data.truncated is True


async def test_malformed_encoding_is_read_rather_than_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches an UnicodeDecodeError escaping the reader over one bad byte."""
    (tmp_path / "latin.txt").write_bytes(b"caf\xe9 indexes are sorted")
    _use(tmp_path, monkeypatch)

    result = await _read(path="latin.txt", mode="full")

    assert result.ok, result.error
    assert "indexes are sorted" in result.data.text


async def test_section_mode_without_a_hint_is_rejected(attachments: Path) -> None:
    """The registry must reject the argument combination before any file is touched."""
    result = await _read(path="guide.md", mode="section")

    assert result.error.code is ErrorCode.BAD_ARGUMENTS


# --- the failure table -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("missing.md", None, ErrorCode.NOT_FOUND),
        ("empty.md", b"", ErrorCode.EMPTY_FILE),
        ("notes.rtf", b"plain enough, wrong suffix", ErrorCode.UNSUPPORTED_TYPE),
        ("broken.pdf", b"%PDF-1.4\nnot really a pdf", ErrorCode.CORRUPT_PDF),
        ("broken.docx", b"PK\x03\x04 not really a zip", ErrorCode.CORRUPT_DOCX),
    ],
)
async def test_each_failure_returns_its_own_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    content: bytes | None,
    expected: ErrorCode,
) -> None:
    """The contract the agent reasons about: every failure is a distinct, readable code."""
    if content is not None:
        (tmp_path / name).write_bytes(content)
    _use(tmp_path, monkeypatch)

    result = await _read(path=name, mode="full")

    assert result.ok is False
    assert result.error.code is expected
    assert result.error.retryable is False


@pytest.mark.parametrize("path", ["../outside.md", "sub/../../outside.md"])
async def test_paths_outside_the_attachment_directory_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """The security guard: an attachment path must not be able to walk out of its directory."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "outside.md").write_text("secrets", encoding="utf-8")
    _use(allowed, monkeypatch)

    result = await _read(path=path, mode="full")

    assert result.error.code is ErrorCode.PATH_NOT_ALLOWED


async def test_an_oversized_file_is_refused_before_it_is_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches the size guard being skipped — the reason it exists is to not parse the file."""
    (tmp_path / "big.txt").write_text("x" * 5000, encoding="utf-8")
    _use(tmp_path, monkeypatch, max_document_bytes=1024)

    result = await _read(path="big.txt", mode="full")

    assert result.error.code is ErrorCode.BUDGET_EXCEEDED


@pytest.mark.parametrize(
    ("name", "encrypt", "expected"),
    [
        ("scan.pdf", False, ErrorCode.NO_TEXT_LAYER),
        ("locked.pdf", True, ErrorCode.ENCRYPTED_PDF),
    ],
)
async def test_pdfs_that_cannot_be_read_say_why(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    encrypt: bool,
    expected: ErrorCode,
) -> None:
    """A scan and a locked file are different problems: only one is worth asking the user about."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if encrypt:
        writer.encrypt("secret")
    with (tmp_path / name).open("wb") as handle:
        writer.write(handle)
    _use(tmp_path, monkeypatch)

    result = await _read(path=name, mode="full")

    assert result.error.code is expected
