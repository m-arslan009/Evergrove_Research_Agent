"""HTML into the readable text of a page, using the standard library only.

A fetched page is mostly not the article: navigation, cookie banners, sidebars, scripts
and footers outweigh the prose, and every one of those characters costs prefill time on a
CPU-bound machine. This module throws them away and keeps what someone would actually
read.

`html.parser` rather than `trafilatura`: the extractor sits behind one function, so
swapping in a library later is this file's body and no caller's business — and the
project has twice made the same call already (no `python-docx` for `.docx`, no PyYAML for
`domains.json`). The output is deliberately shaped like the document readers' output:
blank-line-separated paragraphs, Markdown `#` headings and four-space-indented code, which
is exactly what `excerpt.select_passages` splits, recognises and scores.

Never raises. A malformed page yields whatever text did parse; `fetch_url` decides that an
empty result is a failure the agent should be told about.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from evergrove_agent.documents.base import BLOCK_SEPARATOR

_DISCARDED_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "iframe",
        "nav",
        "header",
        "footer",
        "aside",
        "form",
        "button",
        "select",
        "textarea",
    }
)
"""Tags whose entire contents are dropped. The first group is never prose; the second is
page furniture that reads as prose and is the reason raw HTML wastes a model's budget."""

_HEADING_TAGS = {f"h{level}": level for level in range(1, 7)}

_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "main",
        "li",
        "dd",
        "dt",
        "pre",
        "blockquote",
        "table",
        "tr",
        "td",
        "th",
        "ul",
        "ol",
        "figcaption",
        "hr",
    }
)
"""Tags that end the current paragraph. `br` is deliberately absent — a line break inside
a sentence is a space, not a new block."""

_WHITESPACE_RE = re.compile(r"\s+")
_MAX_TITLE_CHARS = 300


class _Extractor(HTMLParser):
    """Collects text between block boundaries, skipping whole discarded subtrees."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocks: list[str] = []
        self._buffer: list[str] = []
        self._skip_tag: str | None = None
        self._skip_depth = 0
        self._in_title = False
        self._title = ""
        self._first_heading = ""
        self._heading_level: int | None = None
        self._pre_depth = 0

    # --- parser callbacks ---------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_tag is not None:
            # Nested copies of the tag we are skipping, so the *matching* end tag wins.
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag in _DISCARDED_TAGS:
            self._flush()
            self._skip_tag = tag
            self._skip_depth = 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "br":
            self._buffer.append(" ")
            return
        if tag in _BLOCK_TAGS or tag in _HEADING_TAGS:
            self._flush()
        if tag in _HEADING_TAGS:
            self._heading_level = _HEADING_TAGS[tag]
        if tag == "pre":
            self._pre_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth <= 0:
                    self._skip_tag = None
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in _BLOCK_TAGS or tag in _HEADING_TAGS:
            # Flushed before the depth drops, so the block is still recognised as code.
            self._flush()
        if tag == "pre" and self._pre_depth:
            self._pre_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_tag is not None:
            return
        if self._in_title:
            self._title += data
            return
        self._buffer.append(data)

    # --- results ---------------------------------------------------------------------------

    def finish(self) -> tuple[str, str]:
        """The page title and its text. Flushes whatever the last tag left behind."""
        self._flush()
        title = _collapse(self._title) or self._first_heading
        return title[:_MAX_TITLE_CHARS], BLOCK_SEPARATOR.join(self._blocks)

    # --- internals ---------------------------------------------------------------------------

    def _flush(self) -> None:
        """Turn the buffered text into one block, or drop it if it is only whitespace."""
        raw = "".join(self._buffer)
        self._buffer.clear()
        level, self._heading_level = self._heading_level, None

        text = _indent_code(raw) if self._pre_depth > 0 else _collapse(raw)
        if not text:
            return
        if level is not None:
            if not self._first_heading:
                self._first_heading = text[:_MAX_TITLE_CHARS]
            text = f"{'#' * level} {text}"
        self._blocks.append(text)


def extract_html(markup: str) -> tuple[str, str]:
    """Return `(title, text)` for an HTML page.

    The title is its `<title>`, falling back to the first heading. The text is the page's
    readable blocks, separated by blank lines. Both are empty for a page that is entirely
    boilerplate — the caller reports that, since only it knows a URL to name.
    """
    parser = _Extractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        # Truncated or invalid markup: keep the blocks parsed so far rather than losing
        # a page that was 95% readable.
        pass
    return parser.finish()


def _collapse(text: str) -> str:
    """One line of prose: runs of whitespace, including newlines, become single spaces."""
    return _WHITESPACE_RE.sub(" ", text).strip()


def _indent_code(text: str) -> str:
    """A `<pre>` block, indented four spaces so `select_passages` scores it as code.

    Blank lines are dropped rather than kept, because a blank line would split the
    listing into separate blocks downstream and lose the very structure `<pre>` marks.
    """
    lines = [line.rstrip() for line in text.strip("\n").splitlines()]
    return "\n".join(f"    {line}" for line in lines if line.strip())
