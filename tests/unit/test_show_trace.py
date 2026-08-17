"""The trace renderer: the tree it rebuilds, and the states it must not confuse.

Every test here builds `RunRecord` / `SpanRecord` values directly. `render.py` is pure, so
proving it needs no database, no model and no network — and the rows it consumes are
already proven to reach SQLite correctly by `test_tracing.py`, which is not re-asserted.

Nothing asserts exact spacing, column widths or box-drawing characters: those are cosmetic
and would turn every layout tweak into a failing test. What is asserted is what a reader
would be misled by if it were wrong — the nesting, the three outcome states, and the
refusal to crash on malformed rows.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from evergrove_agent.tracing.render import build_forest, render_trace
from evergrove_agent.tracing.store import RunRecord, SpanRecord

STARTED_AT = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)

SHOW_TRACE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "show_trace.py"


@pytest.fixture(scope="module")
def show_trace() -> ModuleType:
    """`scripts/show_trace.py`, loaded by path.

    `scripts/` is deliberately not a package — it holds operator entry points, not library
    code, which is exactly why the logic under test lives in `tracing/render.py` and this
    file is thin enough that one test covers it.
    """
    spec = importlib.util.spec_from_file_location("show_trace", SHOW_TRACE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def span(
    span_id: str,
    *,
    parent: str | None = None,
    name: str = "web_search",
    kind: str = "tool",
    offset_seconds: float = 0.0,
    ok: bool | None = True,
    error_code: str | None = None,
    from_cache: bool = False,
    duration_ms: int | None = 10,
) -> SpanRecord:
    """One span row, with only the fields a test cares about spelled out."""
    started_at = STARTED_AT + timedelta(seconds=offset_seconds)
    return SpanRecord(
        span_id=span_id,
        run_id="a71c3f",
        parent_span_id=parent,
        name=name,
        kind=kind,  # type: ignore[arg-type]
        started_at=started_at,
        ended_at=None if ok is None else started_at + timedelta(milliseconds=duration_ms or 0),
        duration_ms=None if ok is None else duration_ms,
        ok=ok,
        error_code=error_code,
        from_cache=from_cache,
        input_summary='{"query":"postgres indexing"}',
        output_summary='{"results":[]}',
    )


def run_record(**overrides: object) -> RunRecord:
    """A finished, successful run header."""
    fields: dict[str, object] = {
        "run_id": "a71c3f",
        "task_title": "Learn PostgreSQL indexing",
        "session_minutes": 25,
        "started_at": STARTED_AT,
        "ended_at": STARTED_AT + timedelta(minutes=4, seconds=12),
        "status": "ok",
        "hops_used": 2,
        "model_calls": 8,
        "search_calls": 3,
        "fetch_calls": 4,
    }
    fields.update(overrides)
    return RunRecord(**fields)  # type: ignore[arg-type]


# --- the tree ------------------------------------------------------------------------------


def test_the_tree_follows_parent_span_id_at_every_depth() -> None:
    """Nesting is rebuilt from `parent_span_id`, and sibling order is preserved.

    The highest-value test in this file, and the one regression that is invisible without
    it: a wrong derivation still renders a plausible-looking tree, still prints every span
    and still exits 0 — it just quietly reports a different run than the one that happened.
    Sibling order matters for the same reason, since `get_spans` orders by `started_at,
    rowid` precisely so a trace reads back as the sequence that occurred.

    Today only flat tool spans exist (T2 wired no agent or llm spans), so this is also the
    guard that the renderer is ready for the day Day 5 opens them.
    """
    spans = [
        span("aaa", name="researcher.loop", kind="agent", offset_seconds=0.1),
        span("bbb", parent="aaa", name="web_search", offset_seconds=0.2),
        span("ccc", parent="bbb", name="fetch_url", offset_seconds=0.3),
        span("ddd", parent="aaa", name="fetch_url", offset_seconds=0.4),
        span("eee", name="supervisor.plan", kind="llm", offset_seconds=0.5),
    ]

    roots = build_forest(spans)

    assert [node.span.span_id for node in roots] == ["aaa", "eee"]
    assert [child.span.span_id for child in roots[0].children] == ["bbb", "ddd"]
    assert [child.span.span_id for child in roots[0].children[0].children] == ["ccc"]
    assert roots[0].children[1].children == []


def test_a_lost_parent_and_a_cycle_are_rendered_rather_than_dropped() -> None:
    """Malformed parent ids cost no span and cannot hang the renderer.

    Both shapes are reachable: `spans.run_id` carries no foreign key by design, so a span
    can genuinely outlive the parent whose write failed, and a hand-edited or
    partially-written pair can point at each other. A trace is read at exactly the moment
    something already went wrong, so losing rows or recursing forever there is the worst
    possible behaviour.
    """
    orphaned = span("bbb", parent="never-written", offset_seconds=0.2)
    left = span("ccc", parent="ddd", offset_seconds=0.3)
    right = span("ddd", parent="ccc", offset_seconds=0.4)

    roots = build_forest([orphaned, left, right])

    rendered = {node.span.span_id for node in roots}
    assert rendered == {"bbb", "ccc", "ddd"}
    assert all(node.orphan for node in roots)

    lines = render_trace(run_record(), [orphaned, left, right])
    assert sum("orphan" in line for line in lines) == 3


# --- what a span's line has to say ----------------------------------------------------------


def test_ok_failed_and_unfinished_stay_three_distinct_states() -> None:
    """`ok is None` is not `ok is False`, and a cache hit is visible.

    `store.py` keeps the three apart deliberately — a run killed mid-flight leaves spans
    that never finished, and reporting those as failures would turn every abandoned run
    into a run of failures, which is the opposite of a diagnosis. The error code has to
    reach the line too: "it failed" without `TIMEOUT` is what sends someone back to SQL.
    """
    spans = [
        span("aaa", offset_seconds=0.1, from_cache=True),
        span("bbb", name="fetch_url", offset_seconds=0.2, ok=False, error_code="TIMEOUT"),
        span("ccc", name="validate_report", offset_seconds=0.3, ok=None),
    ]

    lines = render_trace(run_record(), spans)
    body = "\n".join(lines)

    cached, failed, unfinished = (
        next(line for line in lines if name in line)
        for name in ("web_search", "fetch_url", "validate_report")
    )
    assert "ok" in cached and "cache" in cached
    assert "FAILED" in failed and "TIMEOUT" in failed
    assert "unfinished" in unfinished and "FAILED" not in unfinished
    assert "1 failed" in body and "1 unfinished" in body


def test_a_run_with_no_header_or_no_spans_still_renders() -> None:
    """Neither half of a trace is required for the other to be readable.

    Both cases are real. A span with no run row happens when the header write failed —
    which is when a trace matters most — and a run with no spans is a run killed before its
    first tool call. A running run's counters are NULL, and printing `0` for them would
    claim it spent nothing rather than that nothing is recorded yet.
    """
    headerless = render_trace(None, [span("aaa")])
    assert any("no run header" in line for line in headerless)
    assert any("web_search" in line for line in headerless)

    empty = render_trace(run_record(), [])
    assert any("no spans recorded" in line for line in empty)

    running = render_trace(
        run_record(status="running", ended_at=None, hops_used=None, model_calls=None,
                   search_calls=None, fetch_calls=None),
        [span("aaa", ok=None)],
    )
    body = "\n".join(running)
    assert "hops=—" in body
    assert "never finished" in body


# --- the script ------------------------------------------------------------------------------


def test_an_unknown_run_id_reports_and_exits_one(
    show_trace: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run id with nothing behind it is a message and exit 1, never a traceback.

    The one behaviour of the script itself worth pinning: it is the failure an operator
    actually hits — a mistyped id, or the wrong database — and a stack trace there says
    "this tool is broken" when the correct answer is "that run is not in this file". Exit 1
    matches `main.py`'s convention for a run that produced nothing.
    """
    exit_code = show_trace.main([
        "does-not-exist", "--db", str(tmp_path / "agent.sqlite3")
    ])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "does-not-exist" in captured.err
