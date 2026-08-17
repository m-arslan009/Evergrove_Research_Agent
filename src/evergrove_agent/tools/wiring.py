"""The composition root for tools: where the finished tools meet the registry.

Every tool is complete on its own and the registry is complete on its own, but until
something assembles them each caller has to remember which tools exist and how each one is
constructed. That is the knowledge this module owns, in one place, so the CLI (S8) and the
agent loop later ask for an assembled registry rather than reconstructing the menu.

Nothing here decides *behaviour*. It builds tools and calls `register`; the failure
ladders, caches, quota and parsing all stay inside the tools, and the registry stays free
of every one of them. The hooks are the same kind of knowledge and are assembled the same
way: `tools/hooks.py` decides what tracing and budget enforcement *do* and in what order,
and this module only says that an assembled registry has them (Day 4 T2/T3).

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
from evergrove_agent.tools.hooks import install_registry_hooks
from evergrove_agent.tools.memory_tools import (
    RecallPreviousPreparationTool,
    RecordRunMemoryTool,
    SavePreparationTool,
)
from evergrove_agent.tools.normalize_sources import NormalizeSourcesTool
from evergrove_agent.tools.read_document import ReadDocumentTool
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tools.validate_report import ValidateReportTool
from evergrove_agent.tools.web_search import WebSearchTool
from evergrove_agent.tracing.tracer import Tracer

TOOL_NAMES: tuple[str, ...] = (
    "fetch_url",
    "normalize_sources",
    "read_document",
    "recall_previous_preparation",
    "record_run_memory",
    "save_preparation",
    "validate_report",
    "web_search",
)
"""The wired menu, sorted to match `ToolRegistry.names`.

Declared rather than derived: a tool that is finished but never wired, or a name that drifts
from the tool's own `name`, is then a failing assertion instead of a capability the agent
silently lost. `normalize_sources` is a pipeline step rather than a model-facing tool — it is
registered so a run's trace shows what normalisation discarded, and which subset is
*advertised* to a model is the tool-spec layer's decision, not the registry's.
`validate_report` is registered on the same terms and for the same reason: the trace should
show what grounding rejected, but whether a report passes is not the model's call. The three
memory tools (T4) join that group: recall happens once at the start of a run and a save once
after validation, both decided by our own code, which is the only way "never save an invalid
preparation" can be structurally true.
"""


def build_tool_registry(
    settings: Settings | None = None,
    *,
    connection: sqlite3.Connection | None = None,
    tracer: Tracer | None = None,
) -> ToolRegistry:
    """A registry holding every finished tool, with its hooks installed — the one call path.

    `settings` and `connection` are the same injection seams the tools already take, passed
    straight through: given no connection, `fetch_url` and `web_search` keep opening and
    closing their own per call, exactly as they do today. A long-lived caller that wants one
    database handle for the whole run passes it here instead.

    `tracer` is the trace's seam and is deliberately separate from `connection`: a caller
    that shares a database handle with the tools has not thereby asked for its run to be
    recorded, and a registry that started writing spans because a connection happened to be
    available would be tracing by accident. Given none, the registry still enforces the run
    budget — that is not optional — and simply keeps no record of the calls.

    Note that `read_document` takes neither `settings` nor `connection` — it resolves
    `ALLOWED_ATTACHMENT_DIR` through `get_settings()` when it reads, so a `settings`
    override given here does not reach it.

    Raises `ValueError` if two tools claim one name, which is a wiring bug and not something
    a run should start with.
    """
    registry = ToolRegistry()
    for tool in (
        ReadDocumentTool(),
        NormalizeSourcesTool(),
        ValidateReportTool(),
        FetchUrlTool(settings, connection=connection),
        WebSearchTool(settings, connection=connection),
        RecallPreviousPreparationTool(settings, connection=connection),
        SavePreparationTool(settings, connection=connection),
        RecordRunMemoryTool(settings, connection=connection),
    ):
        registry.register(tool)
    install_registry_hooks(registry, tracer=tracer)
    return registry
