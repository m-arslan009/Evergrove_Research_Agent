"""A person-sized front door to the finished tools (plan section 10, S8).

The tools were built before the agent so that when the Day 3 research loop misbehaves,
they are already ruled out. Ruling them out means running one by hand and reading what it
actually returned — which is all this module is: argument parsing, one `ToolRegistry.call`,
and a formatter.

Deliberately thin. Nothing here decides anything a tool decides: no backend selection, no
cache read, no retry ladder, no normalisation, and no model. Every flag maps onto a field
that already exists on the tool's own input model, and the bounds on those fields are *not*
re-checked here — the args go to the registry, which validates them against the input model
and answers `BAD_ARGUMENTS`. A second copy of the limits in argparse is a second place for
them to drift.

    python -m evergrove_agent.tools.cli search "postgresql indexing" --type docs
    python -m evergrove_agent.tools.cli fetch https://example.com/page
    python -m evergrove_agent.tools.cli read documents/indexing-brief.md --mode outline
    python -m evergrove_agent.tools.cli normalize https://a.dev/x https://a.dev/x/

Exit codes follow `main.py`: 0 the tool succeeded, 1 the tool returned a `ToolError`
(printed as its code and message, never a traceback — `ToolRegistry.call` does not raise),
2 the arguments were unusable, which is argparse's own.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Iterable, Sequence
from typing import Any, get_args

from pydantic import BaseModel

from evergrove_agent.config import SearchBackendName, Settings, get_settings
from evergrove_agent.schemas import ToolResult
from evergrove_agent.search import SearchSourceType
from evergrove_agent.tools.base import RunContext
from evergrove_agent.tools.fetch_url import FetchUrlInput
from evergrove_agent.tools.read_document import ReadDocumentInput, ReadMode
from evergrove_agent.tools.web_search import WebSearchInput
from evergrove_agent.tools.wiring import build_tool_registry

_SNIPPET_CHARS = 160
"""How much of a snippet one listing line shows. Display only — the full text is in
`--json`."""


def _default(model: type[BaseModel], field: str) -> object:
    """A field's default, read off the model so help text cannot drift from it."""
    return model.model_fields[field].default


# --- the command surface ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Four subcommands, one per registered tool.

    `choices` come from the existing literals rather than a hand-written list, so adding a
    source type or a backend is not also a CLI edit.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Print the whole ToolResult envelope as JSON instead of a summary.",
    )

    parser = argparse.ArgumentParser(
        prog="evergrove-tools",
        description="Run one research tool directly. No agent, no model.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    search = subcommands.add_parser(
        "search", parents=[common], help="web_search: find sources for a query."
    )
    search.add_argument("query", help="What to search for, as search-engine keywords.")
    search.add_argument(
        "--type",
        dest="source_type",
        choices=get_args(SearchSourceType),
        default=None,
        help=f"The kind of source wanted. Default "
        f"{_default(WebSearchInput, 'source_type')}.",
    )
    search.add_argument(
        "--max-results",
        dest="max_results",
        type=int,
        default=None,
        help=f"Ceiling on how many sources come back. Default "
        f"{_default(WebSearchInput, 'max_results')}.",
    )
    search.add_argument(
        "--backend",
        choices=get_args(SearchBackendName),
        default=None,
        help="Override SEARCH_BACKEND for this call. 'fixture' (the default) replays "
        "recordings and costs no quota.",
    )

    fetch = subcommands.add_parser(
        "fetch", parents=[common], help="fetch_url: read one page as text."
    )
    fetch.add_argument(
        "url", help="The http(s) URL to fetch. Links on it are not followed."
    )
    fetch.add_argument(
        "--excerpt-for",
        dest="excerpt_for",
        default=None,
        help="The question the page is being read for. Given one, only the passages that "
        "answer it come back.",
    )
    fetch.add_argument(
        "--max-chars",
        dest="max_chars",
        type=int,
        default=None,
        help=f"Ceiling on the text this call returns; it does not bound what is cached. "
        f"Default {_default(FetchUrlInput, 'max_chars')}.",
    )

    read = subcommands.add_parser(
        "read", parents=[common], help="read_document: read a local attachment."
    )
    read.add_argument(
        "path",
        help="A .txt/.md/.pdf/.docx file. A relative path resolves inside "
        "ALLOWED_ATTACHMENT_DIR.",
    )
    read.add_argument(
        "--mode",
        choices=get_args(ReadMode),
        default=None,
        help=f"outline = headings only; section = one heading's contents (needs "
        f"--section); full = the whole document. Default "
        f"{_default(ReadDocumentInput, 'mode')}.",
    )
    read.add_argument(
        "--section",
        dest="section_hint",
        default=None,
        help="The heading wanted. Required by mode=section; steers trimming in mode=full.",
    )

    normalize = subcommands.add_parser(
        "normalize",
        parents=[common],
        help="normalize_sources: canonicalise, dedupe, classify and rank URLs.",
    )
    normalize.add_argument("urls", nargs="+", help="One or more candidate source URLs.")

    return parser


def _build_call(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """The parsed arguments as (tool name, arguments for its input model).

    The only place in this module that knows the commands exist. Options left unset are
    dropped rather than passed as `None`, so every default stays where it belongs — on the
    tool's own input model.
    """
    if args.command == "search":
        return "web_search", _given(
            query=args.query,
            source_type=args.source_type,
            max_results=args.max_results,
        )
    if args.command == "fetch":
        return "fetch_url", _given(
            url=args.url,
            max_chars=args.max_chars,
            excerpt_for=args.excerpt_for,
        )
    if args.command == "read":
        return "read_document", _given(
            path=args.path,
            mode=args.mode,
            section_hint=args.section_hint,
        )
    return "normalize_sources", {
        "sources": [{"url": url, "source_backend": "cli"} for url in args.urls]
    }


def _given(**values: Any) -> dict[str, Any]:
    """Only the arguments the caller actually gave."""
    return {key: value for key, value in values.items() if value is not None}


# --- running it ------------------------------------------------------------------------------


async def run(args: argparse.Namespace, *, settings: Settings | None = None) -> int:
    """Build the registry, make the one call, print the outcome, return the exit code.

    `--backend` is the one flag that is not a tool argument: `web_search` reads it from
    `Settings.search_backend`. It is applied as a copy of the settings rather than a routing
    decision here, and the copy keeps the process-wide settings object unchanged.
    """
    resolved = settings if settings is not None else get_settings()
    backend = getattr(args, "backend", None)
    if backend is not None:
        resolved = resolved.model_copy(update={"search_backend": backend})

    name, call_args = _build_call(args)
    registry = build_tool_registry(resolved)
    result = await registry.call(name, call_args, RunContext())
    return _report(name, result, as_json=args.as_json)


def _report(name: str, result: ToolResult[Any], *, as_json: bool) -> int:
    """Print the result and answer with the exit code."""
    if as_json:
        print(result.model_dump_json(indent=2))
        return 0 if result.ok else 1

    if not result.ok and result.error is not None:
        retryable = "  retryable=true" if result.error.retryable else ""
        print(f"FAILED  {name}  {result.error.code.value}{retryable}", file=sys.stderr)
        print(result.error.message, file=sys.stderr)
        return 1

    print(
        f"ok  {name}  {result.duration_ms}ms  from_cache={_flag(result.from_cache)}"
    )
    for line in _RENDERERS[name](result.data):
        print(line)
    return 0


# --- formatting ------------------------------------------------------------------------------


def _render_search(data: Any) -> list[str]:
    return [_count(len(data.results), "result"), *_sources(data.results)]


def _render_normalize(data: Any) -> list[str]:
    return [
        f"{_count(len(data.sources), 'source')}  dropped={data.dropped}  "
        f"duplicates_removed={data.duplicates_removed}",
        *_sources(data.sources),
    ]


def _render_fetch(data: Any) -> list[str]:
    header = _labelled(
        [
            ("url", data.url),
            ("final_url", data.final_url),
            ("title", data.title or "(none)"),
            ("chars", f"{data.char_count}  truncated={_flag(data.truncated)}"),
            ("cached", _flag(data.from_cache)),
            ("retrieved", data.retrieved_at.isoformat()),
        ]
    )
    return [*header, *_body(data.text)]


def _render_read(data: Any) -> list[str]:
    fields = [("path", data.path), ("type", data.file_type)]
    if data.page_count is not None:
        fields.append(("pages", str(data.page_count)))
    fields.append(("truncated", _flag(data.truncated)))

    lines = _labelled(fields)
    if data.outline:
        lines.append(_count(len(data.outline), "heading"))
        lines += [
            f"     {'  ' * (entry.level - 1)}{entry.title}"
            f"{f'  (p{entry.page})' if entry.page else ''}"
            for entry in data.outline
        ]
    return [*lines, *_body(data.text)]


_RENDERERS: dict[str, Callable[[Any], list[str]]] = {
    "web_search": _render_search,
    "normalize_sources": _render_normalize,
    "fetch_url": _render_fetch,
    "read_document": _render_read,
}
"""One renderer per tool: a new tool is an entry here, not another branch elsewhere."""


def _sources(items: Iterable[Any]) -> list[str]:
    """A `NormalizedSource` list as indented blocks — the shape both listings return."""
    lines: list[str] = []
    for position, source in enumerate(items, start=1):
        lines.append(f"{position:>3}  {source.domain_class:<9}  {source.domain}")
        if source.title:
            lines.append(f"     {source.title}")
        lines.append(f"     {source.url}")
        if source.snippet:
            lines.append(f"     {_one_line(source.snippet)}")
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _labelled(pairs: Sequence[tuple[str, str]]) -> list[str]:
    """Aligned `label  value` lines."""
    width = max((len(label) for label, _ in pairs), default=0)
    return [f"{label:<{width}}  {value}" for label, value in pairs]


def _body(text: str) -> list[str]:
    """The long text, raw, under a rule — empty when there is none (mode=outline)."""
    return ["---", text] if text.strip() else []


def _one_line(text: str) -> str:
    collapsed = " ".join(text.split())
    return (
        collapsed
        if len(collapsed) <= _SNIPPET_CHARS
        else f"{collapsed[:_SNIPPET_CHARS].rstrip()}…"
    )


def _count(total: int, noun: str) -> str:
    return f"{total} {noun}" if total == 1 else f"{total} {noun}s"


def _flag(value: bool) -> str:
    return "true" if value else "false"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
