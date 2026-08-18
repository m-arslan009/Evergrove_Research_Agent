"""Tracing: one `run_id` per run, one `span_id` per operation, `parent_span_id` for the tree.

Plan section 13. Two layers and a clean split between them:

- `store.py` — the `runs` and `spans` rows. Storage only, and it raises on a database error.
- `tracer.py` — the `Tracer` a registry hook calls. Coordinates `RunContext`'s span stack
  with those writes, and is the one place a tracing failure is prevented from ending a run.
  `agent_span` (Day 5 T6) is the same pair as a context manager, for the agent boundaries a
  single frame opens and closes.
- `render.py` — the read side (T6). Pure: rows in, lines out. It writes nothing, opens no
  connection and imports no `sqlite3`, so displaying a trace can never disturb one.

The span stack itself lives on `RunContext` in `tools/base.py`, because minting an id and
knowing what it nests under is pure — and `tools/base.py` may not import `sqlite3`.

Nothing in this package calls a model, a tool, the network, or an agent. A trace is written
*about* a run; it never takes part in one.
"""

from __future__ import annotations

from evergrove_agent.tracing.render import SpanNode, build_forest, render_trace
from evergrove_agent.tracing.store import (
    RunRecord,
    RunStatus,
    SpanKind,
    SpanRecord,
    finish_run,
    finish_span,
    get_run,
    get_spans,
    start_run,
    start_span,
)
from evergrove_agent.tracing.tracer import AgentSpan, Tracer, agent_span

__all__ = [
    "AgentSpan",
    "RunRecord",
    "RunStatus",
    "SpanKind",
    "SpanNode",
    "SpanRecord",
    "Tracer",
    "agent_span",
    "build_forest",
    "finish_run",
    "finish_span",
    "get_run",
    "get_spans",
    "render_trace",
    "start_run",
    "start_span",
]
