"""`read_document` — the agent's one way into a local file (plan section 11).

One tool with a `mode` enum rather than three tools: a small local model's tool selection
degrades as the menu grows, so the routing behind the enum is deterministic Python. The
modes trade cost against completeness — `outline` is a table of contents, `section` is one
part of the document, `full` is the document narrowed to the reading budget.

The tool owns the modes and the failure conversion; `documents/` owns the parsing. Every
`DocumentReadError` a reader raises leaves here as a `ToolResult`, which is what makes the
"tools never raise" promise structural rather than aspirational.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evergrove_agent.documents import (
    DocumentReadError,
    OutlineEntry,
    ParsedDocument,
    read_document_file,
    select_passages,
    select_section,
)
from evergrove_agent.schemas import ErrorCode, ToolError, ToolResult
from evergrove_agent.tools.base import RunContext

ReadMode = Literal["outline", "full", "section"]

_MAX_LISTED_HEADINGS = 20


class ReadDocumentInput(BaseModel):
    """Which file to read, and how much of it."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        min_length=1,
        description="File to read. Relative paths resolve inside the allowed "
        "attachment directory. Supported: .txt, .md, .pdf, .docx.",
    )
    mode: ReadMode = Field(
        default="full",
        description="outline = headings only; full = the whole document, trimmed to "
        "the reading budget; section = one heading's contents.",
    )
    section_hint: str | None = Field(
        default=None,
        max_length=200,
        description="Required for mode=section: the heading wanted. In mode=full it "
        "steers which passages survive trimming.",
    )

    @model_validator(mode="after")
    def _section_needs_a_hint(self) -> ReadDocumentInput:
        if self.mode == "section" and not (self.section_hint or "").strip():
            raise ValueError("mode='section' requires section_hint")
        return self


class ReadDocumentOutput(BaseModel):
    """The document as the model will see it."""

    model_config = ConfigDict(extra="forbid")

    path: str
    file_type: str
    page_count: int | None = None
    outline: list[OutlineEntry] = Field(default_factory=list)
    text: str = ""
    truncated: bool = False


class ReadDocumentTool:
    """Read a local document: its outline, one section of it, or all of it."""

    name = "read_document"
    description = (
        "Read a local .txt, .md, .pdf or .docx attachment. Use mode='outline' to see "
        "its headings first, mode='section' with section_hint for one part, or "
        "mode='full' for the whole document trimmed to the reading budget."
    )
    input_model = ReadDocumentInput
    output_model = ReadDocumentOutput

    async def run(
        self, args: ReadDocumentInput, ctx: RunContext
    ) -> ToolResult[ReadDocumentOutput]:
        """Read, then shape by mode. `duration_ms` is left to the registry."""
        try:
            document = read_document_file(args.path)
        except DocumentReadError as exc:
            return _failure(exc.code, exc.message)

        if args.mode == "outline":
            return _success(args, document, text="", source_length=0)

        if args.mode == "section":
            body = self._section_body(args, document)
            if isinstance(body, ToolResult):
                return body
        else:
            body = document.text

        hint = (args.section_hint or "").strip()
        return _success(
            args,
            document,
            text=select_passages(body, hint),
            source_length=len(body),
        )

    @staticmethod
    def _section_body(
        args: ReadDocumentInput, document: ParsedDocument
    ) -> str | ToolResult[ReadDocumentOutput]:
        """The requested section, the whole text, or a failure the agent can act on.

        A document with no outline at all (a plain `.txt`, a PDF without bookmarks) has
        nothing to slice on, so the hint falls through to keyword selection over the whole
        text. When there *is* an outline and the hint matches none of it, that is worth
        reporting — with the headings that do exist, so the next call can succeed.
        """
        match = select_section(document, args.section_hint or "")
        if match is not None:
            return match[1]
        if not document.outline:
            return document.text
        titles = [entry.title for entry in document.outline[:_MAX_LISTED_HEADINGS]]
        return _failure(
            ErrorCode.NOT_FOUND,
            f"no section matching {args.section_hint!r}. Available headings: "
            f"{'; '.join(titles)}",
        )


def _success(
    args: ReadDocumentInput, document: ParsedDocument, *, text: str, source_length: int
) -> ToolResult[ReadDocumentOutput]:
    return ToolResult(
        ok=True,
        data=ReadDocumentOutput(
            path=args.path,
            file_type=Path(args.path).suffix.lower().lstrip("."),
            page_count=document.page_count,
            outline=document.outline,
            text=text,
            truncated=len(text) < source_length,
        ),
        duration_ms=0,
    )


def _failure(code: ErrorCode, message: str) -> ToolResult[ReadDocumentOutput]:
    """Reads are deterministic, so nothing here is retryable — an identical call fails alike."""
    return ToolResult(
        ok=False,
        error=ToolError(code=code, message=message[:500], retryable=False),
        duration_ms=0,
    )
