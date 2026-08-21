"""One end-to-end demo run, narrated from the trace as it happens.

    uv run python scripts/demo_e2e.py --task "..." --minutes 45 --provider hosted --backend serpapi

**A surface, not a second composition root.** It builds a `TaskContext` and a `RunContext`
and calls `service.prepare_focus_session` — the one entry point, the same call `main.py`
makes for a researched run. It decides nothing about the run: no prompt, no budget, no
retry, no topology beyond passing `--mode` through, and no field of the report it prints.

What it adds over `main.py`'s spinner is *stage-level* visibility, and every line of it is
read back from what the run already records rather than invented here:

- the `runs` / `spans` rows, polled live off a second read connection (WAL, so a reader
  cannot disturb the writer). Agent boundaries, hop numbers, tool calls, parent/child
  nesting, `ok`, durations and the stored summaries all come from those rows — the same
  ones `scripts/show_trace.py` renders afterwards;
- the shipped `evergrove_agent.trace` JSON lines, one per tool call, emitted by
  `tools/hooks.py`. Enabling them here is exactly what `main.py --trace-log` does.

So every claim the demo makes on screen is a claim someone can re-read off the database.
The narrator never takes part in the run: it opens no tool, calls no model, and writes
nothing.

Exit codes follow `main.py`: 0 the demo produced a validated report, 1 the run failed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sqlite3
import sys
import time
from collections.abc import Sequence

from evergrove_agent.agents.single_agent import PreparationFailed
from evergrove_agent.config import get_settings
from evergrove_agent.llm import LLMError
from evergrove_agent.memory import db
from evergrove_agent.schemas import FocusPreparationReport, TaskContext
from evergrove_agent.service import prepare_focus_session
from evergrove_agent.tools.base import RunBudget, RunContext
from evergrove_agent.tracing import SpanRecord, get_run, get_spans, render_trace

WIDTH = 96
POLL_SECONDS = 0.6
HEARTBEAT_SECONDS = 4.0

_ROLE_LABEL = {
    "supervisor.run": "SUPERVISOR  coordination opened",
    "supervisor.decide": "SUPERVISOR  interpreting the task and narrowing the goal",
    "researcher.loop": "RESEARCHER  searching and reading sources",
    "appraiser.judge": "APPRAISER   judging the evidence gathered so far",
    "supervisor.finalise": "SUPERVISOR  writing and validating the preparation",
    "agent.run": "AGENT       single-agent loop (all four stages)",
}
"""Friendly labels for the six agent spans the two loops open. A span whose name is not
here is still shown — by its recorded name, never hidden."""

_TOOL_LABEL = {
    "recall_previous_preparation": "session memory: recalling any earlier preparation",
    "record_run_memory": "session memory: filing this hop away",
    "save_preparation": "saving the completed focus session",
    "validate_report": "validation: grounding the report against the evidence",
    "web_search": "real tool call: web_search",
    "fetch_url": "real tool call: fetch_url",
    "read_document": "real tool call: read_document",
    "normalize_sources": "real tool call: normalize_sources",
}

_RESEARCH_TOOLS = frozenset({"web_search", "fetch_url", "read_document"})


def rule(char: str = "-") -> str:
    return char * WIDTH


def banner(title: str) -> None:
    print()
    print(rule("="))
    print(f"  {title}")
    print(rule("="))
    sys.stdout.flush()


def _clip(text: str, limit: int = 150) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


# --- the narrator: the trace, read back while it is still being written --------------------


class TraceNarrator:
    """Print each span as it opens and as it closes, from the recorded rows.

    Depth is derived from `parent_span_id` exactly as `render.build_forest` derives it, so
    the live indentation and the tree printed afterwards describe one structure. Hop numbers
    are counted from `researcher.loop` spans, because that span *is* a hop.
    """

    def __init__(self, connection: sqlite3.Connection, run_id: str, started: float) -> None:
        self.connection = connection
        self.run_id = run_id
        self.started = started
        self.depth: dict[str, int] = {}
        self.opened: set[str] = set()
        self.closed: set[str] = set()
        self.hop = 0
        self.last_line = started

    def elapsed(self) -> str:
        seconds = time.monotonic() - self.started
        return f"[t+{int(seconds) // 60}:{seconds % 60:04.1f}]"

    def emit(self, span: SpanRecord, *, closing: bool) -> None:
        pad = "  " * self.depth.get(span.span_id, 0)
        gutter = " " * 11

        if not closing:
            if span.kind == "agent":
                label = _ROLE_LABEL.get(span.name, span.name)
                if span.name == "researcher.loop":
                    self.hop += 1
                    label = f"{label}  (research hop {self.hop})"
            else:
                label = f"{span.kind}: {_TOOL_LABEL.get(span.name, span.name)}"
            print(f"{self.elapsed()} {pad}> {label}")
            print(
                f"{gutter}{pad}  {span.name}  "
                f"span_id={span.span_id[:8]}  parent_span_id={(span.parent_span_id or 'root')[:8]}"
            )
            if span.input_summary:
                print(f"{gutter}{pad}  in : {_clip(span.input_summary)}")
        else:
            status = "ok" if span.ok else f"FAILED ({span.error_code or 'no code'})"
            took = "" if span.duration_ms is None else f" in {span.duration_ms / 1000:.2f}s"
            cached = "  (served from cache)" if span.from_cache else ""
            print(f"{self.elapsed()} {pad}< {span.name}: {status}{took}{cached}")
            if span.output_summary:
                print(f"{gutter}{pad}  out: {_clip(span.output_summary)}")

        self.last_line = time.monotonic()
        sys.stdout.flush()

    def drain(self) -> None:
        """Print whatever the trace has recorded since the last poll."""
        try:
            spans = get_spans(self.connection, self.run_id)
        except sqlite3.Error as exc:  # a reader must never end the run it is watching
            print(f"{self.elapsed()} (trace read failed: {exc})")
            return

        for span in spans:
            if span.span_id not in self.opened:
                parent = span.parent_span_id
                self.depth[span.span_id] = 0 if parent is None else self.depth.get(parent, 0) + 1
                self.opened.add(span.span_id)
                self.emit(span, closing=False)
            if span.ended_at is not None and span.span_id not in self.closed:
                self.closed.add(span.span_id)
                self.emit(span, closing=True)

    def heartbeat(self, ctx: RunContext) -> None:
        """Proof of life while a model call or a page fetch is in flight.

        Reads the run's own ledger — `RunBudget`'s live counters, the same object the run is
        spending from — so it is a report rather than a guess.
        """
        if time.monotonic() - self.last_line < HEARTBEAT_SECONDS:
            return
        budget = ctx.budget
        print(
            f"{self.elapsed()} ... working: model calls {budget.model_calls_used}"
            f"/{budget.max_model_calls}  searches {budget.searches_used}/{budget.max_searches}"
            f"  page reads {budget.fetches_used}/{budget.max_fetches}"
            f"  elapsed {budget.elapsed_seconds:.0f}s"
        )
        self.last_line = time.monotonic()
        sys.stdout.flush()


async def narrate(narrator: TraceNarrator, ctx: RunContext) -> None:
    while True:
        narrator.drain()
        narrator.heartbeat(ctx)
        await asyncio.sleep(POLL_SECONDS)


class ToolLogHandler(logging.Handler):
    """The shipped JSON hook line, printed inline so it sits beside the span it mirrors.

    `tools/hooks.py` emits one of these per tool call, with its own timestamp, from the same
    values the span row is written from. Showing it is the demonstration that every tool call
    went through the shared registry hook path — the line exists nowhere else.
    """

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):
            payload = json.loads(record.getMessage())
            print(f"{' ' * 11}  hook-log: {json.dumps(payload)}")
            sys.stdout.flush()


# --- the product outcome, as a person reads it ---------------------------------------------


def _wrap(text: str, indent: int = 24, width: int = WIDTH) -> str:
    lines: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + len(word) + 1 > width - indent:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    lines.append(current)
    return f"\n{' ' * indent}".join(lines)


def show_report(report: FocusPreparationReport) -> None:
    banner("THE PREPARED FOCUS SESSION  (what the user gets)")
    print(f"  Original question   : {_wrap(report.original_task)}")
    print(f"  Session duration    : {report.session_duration_minutes} minutes")
    print()
    print(f"  Interpreted goal    : {_wrap(report.interpreted_goal)}")
    print(f"  Session objective   : {_wrap(report.session_objective)}")
    print(f"  Success criterion   : {_wrap(report.success_criteria)}")

    print("\n  Topics to cover")
    for index, topic in enumerate(report.topics_to_cover, 1):
        print(f"    {index}. {topic}")

    if report.topics_to_skip:
        print("\n  Topics to skip (deliberately out of scope for this session)")
        for topic in report.topics_to_skip:
            print(f"    - {topic}")

    print("\n  Resources — every one of these was actually read during the run")
    if not report.resources:
        print("    (none cited)")
    for index, resource in enumerate(report.resources, 1):
        print(f"    {index}. {resource.title}   [authority: {resource.authority}]")
        print(f"       {resource.url}")
        print(f"       why : {_wrap(resource.why_this_source, indent=13)}")

    if report.practice is not None:
        print("\n  Practice / action for the session")
        print(f"    do      : {_wrap(report.practice.instruction, indent=14)}")
        print(f"    outcome : {_wrap(report.practice.expected_outcome, indent=14)}")

    if report.assumptions:
        print("\n  Assumptions")
        for item in report.assumptions:
            print(f"    - {_wrap(item, indent=6)}")
    if report.unknowns:
        print("\n  Unknowns — what this preparation did not settle")
        for item in report.unknowns:
            print(f"    - {_wrap(item, indent=6)}")

    print("\n  Provenance")
    print(f"    run_id            : {report.run_id}")
    print(f"    generated_at      : {report.generated_at.isoformat()}")
    print(f"    model_used        : {report.model_used}")
    print(f"    hops_used         : {report.hops_used}")
    print(f"    sources_examined  : {report.sources_examined}")
    sys.stdout.flush()


# --- running it ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo_e2e",
        description="One narrated end-to-end run through service.prepare_focus_session.",
    )
    parser.add_argument("--task", help="The question, as the user wrote it.")
    parser.add_argument(
        "--summarise",
        metavar="RUN_ID",
        default=None,
        help="Re-render a finished run's stored trace and structural summary and exit. "
        "Read-only: it starts no run, calls no model and spends nothing.",
    )
    parser.add_argument("--minutes", type=int, default=45)
    parser.add_argument("--description", default=None)
    parser.add_argument("--mode", choices=("single", "multi"), default="multi")
    parser.add_argument(
        "--provider",
        choices=("local", "hosted"),
        default=None,
        help="Set every role's provider. The three per-role flags below override it.",
    )
    for role in ("supervisor", "researcher", "appraiser"):
        parser.add_argument(
            f"--{role}-provider",
            dest=f"{role}_provider",
            choices=("local", "hosted"),
            default=None,
            help=f"The provider serving the {role} role, overriding --provider. Day 5 T3 "
            "makes each role resolve independently, so this is configuration and nothing "
            "else — no agent reads a setting or builds a client of its own.",
        )
    parser.add_argument("--backend", choices=("fixture", "serpapi", "academic"), default=None)
    return parser


def summarise_recorded_run(args: argparse.Namespace) -> int:
    """Re-render one finished run from the rows it already wrote.

    The same two queries `scripts/show_trace.py` makes, over the same renderer, plus the
    structural summary. Whether the run produced a preparation is read the only way a
    finished run can answer it — a successful `save_preparation` span, which by the memory
    contract can only hold a report `finalise` returned and therefore validated.
    """
    settings = get_settings()
    connection = db.connect(settings.db_path)
    db.initialize_schema(connection)
    record = get_run(connection, args.summarise)
    spans = get_spans(connection, args.summarise)
    connection.close()

    if record is None and not spans:
        print(f"no trace recorded for run {args.summarise!r}", file=sys.stderr)
        return 1

    banner(f"STORED TRACE FOR run_id={args.summarise}  (read back from {settings.db_path})")
    for line in render_trace(record, spans, summaries=False):
        print(line)

    produced = any(s.name == "save_preparation" and s.ok for s in spans)
    runtime = 0.0
    if record is not None and record.ended_at is not None:
        runtime = (record.ended_at - record.started_at).total_seconds()
    show_summary(
        task=TaskContext(
            task_title=record.task_title if record else "(unknown)",
            session_minutes=record.session_minutes if record else 25,
        ),
        settings=settings,
        args=args,
        run_id=args.summarise,
        runtime=runtime,
        produced=produced,
        record=record,
        spans=spans,
        failure=None,
    )
    return 0 if produced else 1


async def run(args: argparse.Namespace) -> int:
    if args.summarise:
        return summarise_recorded_run(args)
    if not args.task:
        print("--task is required unless --summarise is given", file=sys.stderr)
        return 2

    base = get_settings()
    overrides: dict[str, object] = {}
    if args.provider is not None:
        overrides.update(
            supervisor_provider=args.provider,
            researcher_provider=args.provider,
            appraiser_provider=args.provider,
        )
    for role in ("supervisor", "researcher", "appraiser"):
        chosen = getattr(args, f"{role}_provider")
        if chosen is not None:
            overrides[f"{role}_provider"] = chosen
    if args.backend is not None:
        overrides["search_backend"] = args.backend
    settings = base.model_copy(update=overrides) if overrides else base

    task = TaskContext(
        task_title=args.task,
        session_minutes=args.minutes,
        task_description=args.description,
    )
    ctx = RunContext(budget=RunBudget.from_settings(settings))

    trace_logger = logging.getLogger("evergrove_agent.trace")
    trace_logger.addHandler(ToolLogHandler())
    trace_logger.setLevel(logging.INFO)
    trace_logger.propagate = False

    banner("EVERGROVE RESEARCH AGENT - END-TO-END DEMO")
    print(f"  Question            : {task.task_title}")
    print(f"  Session requested   : {task.session_minutes} minutes")
    print(f"  run_id              : {ctx.run_id}")
    print(f"  Topology (--mode)   : {args.mode}")
    print(
        "  Providers           : supervisor="
        f"{settings.provider_for('supervisor')}  researcher="
        f"{settings.provider_for('researcher')}  appraiser="
        f"{settings.provider_for('appraiser')}"
    )
    print(f"  Hosted / local model: {settings.hosted_model} / {settings.local_model}")
    print(f"  Search backend      : {settings.search_backend}  (real tools, real network)")
    print(
        f"  Budgets             : hops {settings.max_hops}  searches "
        f"{settings.max_search_calls}  page reads {settings.max_fetch_calls}  model calls "
        f"{settings.max_model_calls}  deadline {settings.total_run_timeout_s}s"
    )
    print(f"  Trace database      : {settings.db_path}")
    print(rule())
    print("  LIVE TRACE - span_id / parent_span_id below are the stored rows, read back as")
    print("  they are written. '>' is a span opening, '<' the same span closing.")
    print(rule())
    sys.stdout.flush()

    reader = db.connect(settings.db_path)
    db.initialize_schema(reader)
    started = time.monotonic()
    narrator = TraceNarrator(reader, ctx.run_id, started)
    watcher = asyncio.create_task(narrate(narrator, ctx))

    report: FocusPreparationReport | None = None
    failure: str | None = None
    try:
        report = await prepare_focus_session(task, mode=args.mode, settings=settings, ctx=ctx)
    except PreparationFailed as exc:
        failure = f"PreparationFailed: {exc}"
    except LLMError as exc:
        failure = f"LLMError: {exc}"
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        narrator.drain()
    runtime = time.monotonic() - started

    if failure is not None:
        print(f"\n  RUN FAILED after {runtime:.1f}s: {failure}")

    if report is not None:
        show_report(report)

    banner(f"STORED TRACE FOR run_id={ctx.run_id}  (read back from {settings.db_path})")
    record = get_run(reader, ctx.run_id)
    spans = get_spans(reader, ctx.run_id)
    for line in render_trace(record, spans, summaries=False):
        print(line)
    reader.close()

    show_summary(
        task=task,
        settings=settings,
        args=args,
        run_id=ctx.run_id,
        runtime=runtime,
        produced=report is not None,
        record=record,
        spans=spans,
        failure=failure,
    )
    return 0 if report is not None else 1


def show_summary(
    *, task, settings, args, run_id, runtime, produced, record, spans, failure
) -> None:
    """The compact verdict, computed from the recorded rows rather than from the run.

    Every structural claim is re-derived here from `spans`, so the summary is a check on the
    trace and not a restatement of what the loop believed it did. That is also why it can be
    re-run against a finished run (`--summarise`) without touching a model.

    **Validation leaves no span, and that is correct.** `finalise` imports `validate_report`
    and calls it as a plain function (`supervisor.py`), so it is not a registry dispatch and
    no `tool` row is written for it. The evidence that it ran is structural instead: a
    preparation is saved only from the value `finalise` returned, and `finalise` returns a
    report or raises `PreparationFailed` — so a successful `save_preparation` span *is* the
    proof the report passed the grounding check. Demanding a span here would fail a run that
    did everything right.
    """
    agent_spans = [s for s in spans if s.kind == "agent"]
    tool_spans = [s for s in spans if s.kind == "tool"]
    hops = sum(1 for s in agent_spans if s.name == "researcher.loop")
    tools_called = sorted({s.name for s in tool_spans})
    known_ids = {s.span_id for s in spans}

    researcher_ids = {s.span_id for s in agent_spans if s.name == "researcher.loop"}
    appraiser_ids = {s.span_id for s in agent_spans if s.name == "appraiser.judge"}
    misparented = [
        s.name
        for s in tool_spans
        if s.name in _RESEARCH_TOOLS and s.parent_span_id not in researcher_ids
    ]
    appraiser_children = [s.name for s in spans if (s.parent_span_id or "") in appraiser_ids]
    roots = [s.name for s in spans if s.parent_span_id is None]
    expected_root = ["supervisor.run" if args.mode == "multi" else "agent.run"]
    orphans = [s.name for s in spans if s.parent_span_id and s.parent_span_id not in known_ids]
    parenting_ok = (
        not misparented and not appraiser_children and not orphans and roots == expected_root
    )

    memory_spans = [s for s in tool_spans if s.name == "record_run_memory" and s.ok]
    saved = [s for s in tool_spans if s.name == "save_preparation" and s.ok]
    finalised = [s for s in agent_spans if s.name == "supervisor.finalise" and s.ok]
    hooks_ok = bool(tool_spans) and all(s.duration_ms is not None for s in tool_spans)

    verdict = "PASS" if (produced and parenting_ok and saved and finalised) else "FAIL"

    banner("DEMO SUMMARY")
    print(f"  Question                 : {task.task_title}")
    print(f"  Session duration         : {task.session_minutes} minutes")
    if args.summarise:
        # A trace records what a run *did*, never what it was configured with, so these
        # values are today's settings and not necessarily the finished run's. Saying so is
        # cheaper than printing a provider the run may never have used.
        print("  Configuration            : (not recorded in the trace; see the run's own output)")
    else:
        print(
            f"  Configuration            : mode={args.mode}  providers="
            f"{settings.provider_for('supervisor')}/{settings.provider_for('researcher')}/"
            f"{settings.provider_for('appraiser')}  model={settings.hosted_model}"
            f"  search={settings.search_backend}"
        )
    print(f"  run_id                   : {run_id}")
    print(f"  Total runtime            : {runtime:.1f}s")
    print(f"  Research hops            : {hops}")
    print(
        "  Agents involved          : "
        f"{', '.join(dict.fromkeys(s.name for s in agent_spans)) or '(none)'}"
    )
    print(f"  Tools called             : {', '.join(tools_called) or '(none)'}")
    print(
        f"  Session memory shown     : {'yes' if memory_spans else 'no'} "
        f"({len(memory_spans)} run_memory write(s), one per hop)"
    )
    print(
        f"  Timestamped hook logging : {'yes' if hooks_ok else 'no'} "
        f"({len(tool_spans)} tool calls, each a timed span plus one JSON hook line)"
    )
    print(f"  Parent-child tracing     : {'correct' if parenting_ok else 'INCORRECT'}")
    if misparented:
        print(f"      misparented tools    : {misparented}")
    if appraiser_children:
        print(f"      appraiser children   : {appraiser_children}")
    if orphans:
        print(f"      orphan spans         : {orphans}")
    if roots != expected_root:
        print(f"      roots                : {roots} (expected {expected_root})")
    print(
        "  Validation               : "
        + (
            "passed — validate_report ran inside supervisor.finalise (a direct call, not a\n"
            "                             registry dispatch, so it writes no span by design; a\n"
            "                             saved preparation is the proof it passed)"
            if (finalised and saved)
            else "not reached — finalise did not return a validated report"
        )
    )
    print(f"  Session saved            : {'yes' if saved else 'no'}")
    print(f"  Run status (trace)       : {record.status if record else '(no run header)'}")
    if failure:
        print(f"  Failure                  : {failure}")
    print()
    print(f"  VERDICT                  : {verdict}")
    print(rule("="))
    sys.stdout.flush()


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
