"""A recorded run, as a tree someone can read (Day 4 T6).

Plan section 13. The rows already say everything — who called what, in what order, how
long it took and where it failed — but they say it as two SQL tables, so reading a run
means writing a query by hand. This turns those rows into the tree the plan draws.

**Pure, and read-only twice over.** Nothing here opens a connection, executes a statement
or imports `sqlite3`: it takes the `RunRecord` and `SpanRecord` values `store.get_run` and
`store.get_spans` already return, and answers with a list of lines. That keeps the
renderer testable without a database, and keeps a display concern out of the module that
owns persistence. Lines rather than printed output for the same reason — the surface that
prints them is `scripts/show_trace.py`, and a test should not have to capture stdout to
assert a tree.

**It renders what is there, including what is missing.** A run killed mid-flight leaves
spans with `ended_at IS NULL`, which `store.py` deliberately preserves as `ok is None`
rather than collapsing into a failure. That distinction survives to the screen: a span
reads `ok`, `FAILED` or `unfinished`, and the third is a diagnosis rather than a gap. The
same stance covers a missing run header, a parent id that names no span in this run, and a
parent cycle — each is reported and none of them raises, because the moment someone reaches
for a trace is the moment the run already went wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from evergrove_agent.tracing.store import RunRecord, SpanRecord

_MISSING = "—"
"""What an unrecorded number reads as. A running run has NULL counters and an unfinished
span has no duration; printing `None` or `0` would both claim something the row does not
say."""

_NAME_WIDTH = 32
"""How wide the name column is, tree prefix included. Every deeper level spends part of it
on indentation, which is what keeps the columns after it aligned down the whole tree."""


@dataclass
class SpanNode:
    """One span plus the spans opened inside it.

    A tree node rather than a second copy of the row: `span` is the record exactly as
    storage returned it, and only the parent/child relationship is added. `orphan` marks a
    span whose `parent_span_id` names nothing in this run — kept at the top level rather
    than dropped, because a span with a lost parent is still evidence that the operation
    happened.
    """

    span: SpanRecord
    children: list[SpanNode] = field(default_factory=list)
    orphan: bool = False


# --- the tree -----------------------------------------------------------------------------


def build_forest(spans: list[SpanRecord]) -> list[SpanNode]:
    """Reconstruct the hierarchy `parent_span_id` describes.

    Top-level spans come back in the order `get_spans` gave them, and so do the children of
    every node — that ordering is `started_at, rowid`, which is the only honest reading of
    "what happened next" when two operations start inside the same millisecond.

    Two malformed shapes are handled rather than trusted, because a trace is read at
    precisely the moment something has already gone wrong:

    - a `parent_span_id` naming a span that is not in this run (the tables carry no foreign
      key, by design) promotes the node to the top level, marked `orphan`;
    - a parent cycle — impossible from `RunContext`'s stack, reachable from hand-edited or
      partially-written rows — is broken by walking each node's ancestry once, so the
      renderer cannot recurse forever on a corrupt pair.

    Every span in, every span out. Nothing is dropped by either guard.
    """
    nodes = {span.span_id: SpanNode(span=span) for span in spans}
    roots: list[SpanNode] = []

    for span in spans:
        node = nodes[span.span_id]
        parent = nodes.get(span.parent_span_id) if span.parent_span_id else None

        if span.parent_span_id and parent is None:
            node.orphan = True
            roots.append(node)
        elif parent is None or _would_cycle(node, parent, nodes):
            node.orphan = parent is not None
            roots.append(node)
        else:
            parent.children.append(node)

    return roots


def _would_cycle(
    node: SpanNode, parent: SpanNode, nodes: dict[str, SpanNode]
) -> bool:
    """Would attaching `node` under `parent` close a loop?

    Walks the proposed parent's own ancestry, guarding the walk itself with a seen-set so a
    cycle that already exists among earlier rows cannot trap this check. Cheap: a trace is
    tens of spans deep at worst, and the alternative is a `RecursionError` inside whichever
    surface was trying to explain a broken run.
    """
    seen: set[str] = set()
    current: SpanNode | None = parent
    while current is not None:
        if current.span.span_id == node.span.span_id:
            return True
        if current.span.span_id in seen:
            return True
        seen.add(current.span.span_id)
        parent_id = current.span.parent_span_id
        current = nodes.get(parent_id) if parent_id else None
    return False


# --- rendering ----------------------------------------------------------------------------


def render_trace(
    run: RunRecord | None,
    spans: list[SpanRecord],
    *,
    summaries: bool = False,
) -> list[str]:
    """The whole trace: a header describing the run, then its spans as a tree.

    `run` is optional because `spans.run_id` carries no foreign key (`memory/db.py`), so
    spans can genuinely outlive a run header whose own write failed — and that case is
    exactly when a trace is wanted. The tree still renders; the header says what is missing.

    `summaries` adds the bounded `input_summary` / `output_summary` already stored on each
    span. Off by default: the tree answers "what happened, in what order, how did it go",
    and two extra lines per span is a different question.
    """
    origin = _origin(run, spans)
    lines = _header(run, spans)

    if not spans:
        lines.append("")
        lines.append("no spans recorded for this run")
        return lines

    lines.append("")
    roots = build_forest(spans)
    for index, node in enumerate(roots):
        _append_node(
            lines,
            node,
            origin,
            prefix="",
            last=index == len(roots) - 1,
            summaries=summaries,
        )
    return lines


def _header(run: RunRecord | None, spans: list[SpanRecord]) -> list[str]:
    """What the run was, how it ended, and what it spent.

    Absolute instants appear here and nowhere else — every span below is placed by its
    offset from the run's start, which is what makes a tree readable at a glance.
    """
    if run is None:
        return [
            "run     (no run header recorded — showing spans only)",
            f"spans   {_span_tally(spans)}",
        ]

    window = run.started_at.isoformat()
    if run.ended_at is not None:
        window += f" → {run.ended_at.isoformat()}  ({_duration(run.ended_at - run.started_at)})"
    else:
        window += "  (never finished)"

    return [
        f"run     {run.run_id}   {run.status}",
        f"task    {run.task_title}   ({run.session_minutes} min session)",
        f"window  {window}",
        f"spend   hops={_number(run.hops_used)}  model_calls={_number(run.model_calls)}"
        f"  searches={_number(run.search_calls)}  fetches={_number(run.fetch_calls)}",
        f"spans   {_span_tally(spans)}",
    ]


def _span_tally(spans: list[SpanRecord]) -> str:
    """`6   (1 failed, 1 unfinished)` — the counts worth seeing before the tree."""
    failed = sum(1 for span in spans if span.ok is False)
    unfinished = sum(1 for span in spans if span.ok is None)
    notes = []
    if failed:
        notes.append(f"{failed} failed")
    if unfinished:
        notes.append(f"{unfinished} unfinished")
    return f"{len(spans)}" + (f"   ({', '.join(notes)})" if notes else "")


def _append_node(
    lines: list[str],
    node: SpanNode,
    origin: datetime | None,
    *,
    prefix: str,
    last: bool,
    summaries: bool,
) -> None:
    """One span's line, then its children, indented under it.

    The run header above is the tree's implicit root, so every span — including a top-level
    one — carries a connector, exactly as the plan's section 13 sample draws it. Recursion
    is safe here because `build_forest` has already guaranteed the tree is acyclic.
    """
    connector = "└── " if last else "├── "
    lines.append(f"{prefix}{connector}{_span_line(node, origin, len(prefix) + len(connector))}")

    child_prefix = prefix + ("    " if last else "│   ")
    if summaries:
        for line in _summary_lines(node.span):
            lines.append(f"{child_prefix}  {line}")

    for index, child in enumerate(node.children):
        _append_node(
            lines,
            child,
            origin,
            prefix=child_prefix,
            last=index == len(node.children) - 1,
            summaries=summaries,
        )


def _span_line(node: SpanNode, origin: datetime | None, indent: int) -> str:
    """`web_search   tool   +0.31s   410 ms   ok  cache`.

    The column order the plan's section 13 sample uses — name, kind, then timing, then how
    it went. `cache` appears only on a hit and `orphan` only when the parent was lost, so a
    line carries a flag when the flag means something rather than a column of `false`.

    `indent` is how wide the tree prefix already is, and the name column is narrowed by it
    so that every span's kind, offset and duration line up in the same columns however deep
    it sits. Without that the timings step to the right with each level, which is precisely
    where they stop being comparable at a glance. A name too long for what is left simply
    overflows — truncating an operation's name to protect a column would hide the one field
    a reader is scanning for.
    """
    span = node.span
    parts = [
        f"{span.name:<{max(1, _NAME_WIDTH - indent)}}",
        f"{span.kind:<6}",
        f"{_offset(span.started_at, origin):>8}",
        f"{_millis(span.duration_ms):>10}",
        f"  {_state(span)}",
    ]
    if span.from_cache:
        parts.append("  cache")
    if node.orphan:
        parts.append("  orphan")
    return "".join(parts).rstrip()


def _state(span: SpanRecord) -> str:
    """`ok`, `FAILED  TIMEOUT`, or `unfinished`.

    Three states, not two. `ok is None` means the row was written when the operation began
    and never updated — the run was killed, or the process died — and reporting that as a
    failure would turn every abandoned run into a run of failures.
    """
    if span.ok is None:
        return "unfinished"
    if span.ok:
        return "ok"
    return f"FAILED  {span.error_code}" if span.error_code else "FAILED"


def _summary_lines(span: SpanRecord) -> list[str]:
    """The stored summaries, already bounded to `TRACE_SUMMARY_CHARS` at write time."""
    lines = []
    if span.input_summary:
        lines.append(f"in   {span.input_summary}")
    if span.output_summary:
        lines.append(f"out  {span.output_summary}")
    return lines


# --- values -------------------------------------------------------------------------------


def _origin(run: RunRecord | None, spans: list[SpanRecord]) -> datetime | None:
    """What every span's offset is measured from.

    The run's own start when there is a header, and the earliest span otherwise — so a
    trace with no run row still reads as a sequence rather than a column of absolute
    timestamps nobody can subtract at a glance.
    """
    if run is not None:
        return run.started_at
    return min((span.started_at for span in spans), default=None)


def _offset(started_at: datetime, origin: datetime | None) -> str:
    """How far into the run this span began, as `+1.31s`.

    A negative offset is possible only from hand-written rows; it is printed as it is
    rather than clamped, because a span that claims to precede its run is a fact worth
    seeing.
    """
    if origin is None:
        return _MISSING
    return f"{(started_at - origin).total_seconds():+.2f}s"


def _millis(duration_ms: int | None) -> str:
    """`1,120 ms`, thousands separated as the plan's sample has them."""
    return _MISSING if duration_ms is None else f"{duration_ms:,} ms"


def _duration(delta: timedelta) -> str:
    """A run's wall-clock length as `4m 12s`, or `0.8s` under a minute."""
    seconds = delta.total_seconds()
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m {int(seconds % 60)}s"


def _number(value: int | None) -> str:
    """A counter that is NULL until the run finishes."""
    return _MISSING if value is None else str(value)
