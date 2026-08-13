"""The composition root for tools: where the finished tools meet the registry.

Every tool is complete on its own and the registry is complete on its own, but until
something assembles them each caller has to remember which tools exist and how each one is
constructed. That is the knowledge this module owns, in one place, so the CLI (S8) and the
agent loop later ask for an assembled registry rather than reconstructing the menu.

Nothing here decides *behaviour*. It builds tools and calls `register`; the failure
ladders, caches, quota and parsing all stay inside the tools, and the registry stays free
of every one of them. The hook lists are deliberately left untouched — the pre/post
extension points belong to the tracing capability (plan section 22, feature 1), and a
composition root that quietly installed a hook would be the first thing to break that.

A fresh registry per call, and no module-level singleton: duplicate names raise at wiring
time by design, so a shared registry would turn a second build into a spurious `ValueError`
and leak state between callers.

Not re-exported from `evergrove_agent.tools`. That package deliberately imports only
`base` and `registry`; re-exporting this factory would pull `httpx`, `sqlite3`, `pypdf` and
the search backends into every import of `RunContext`.
"""

from __future__ import annotations

import sqlite3

from evergrove_agent.config import Settings
from evergrove_agent.tools.fetch_url import FetchUrlTool
from evergrove_agent.tools.normalize_sources import NormalizeSourcesTool
from evergrove_agent.tools.read_document import ReadDocumentTool
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tools.web_search import WebSearchTool

TOOL_NAMES: tuple[str, ...] = (
    "fetch_url",
    "normalize_sources",
    "read_document",
    "web_search",
)
"""The wired menu, sorted to match `ToolRegistry.names`.

Declared rather than derived: a tool that is finished but never wired, or a name that drifts
from the tool's own `name`, is then a failing assertion instead of a capability the agent
silently lost. `normalize_sources` is a pipeline step rather than a model-facing tool — it is
registered so a run's trace shows what normalisation discarded, and which subset is
*advertised* to a model is the tool-spec layer's decision, not the registry's.
"""


def build_tool_registry(
    settings: Settings | None = None,
    *,
    connection: sqlite3.Connection | None = None,
) -> ToolRegistry:
    """A registry holding every finished tool — the one assembled call path.

    `settings` and `connection` are the same injection seams the tools already take, passed
    straight through: given no connection, `fetch_url` and `web_search` keep opening and
    closing their own per call, exactly as they do today. A long-lived caller that wants one
    database handle for the whole run passes it here instead.

    Note that `read_document` takes neither — it resolves `ALLOWED_ATTACHMENT_DIR` through
    `get_settings()` when it reads, so a `settings` override given here does not reach it.

    Raises `ValueError` if two tools claim one name, which is a wiring bug and not something
    a run should start with.
    """
    registry = ToolRegistry()
    for tool in (
        ReadDocumentTool(),
        NormalizeSourcesTool(),
        FetchUrlTool(settings, connection=connection),
        WebSearchTool(settings, connection=connection),
    ):
        registry.register(tool)
    return registry
