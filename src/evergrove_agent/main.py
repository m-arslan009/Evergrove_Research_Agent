"""CLI entry point.

Two paths, and the difference between them is whether the task text ever leaves the
machine. `--no-research` is a single structured round trip against model knowledge alone —
the only fully private mode, because it is the only one where nothing reaches a search
provider (plan section 30). Everything else is the Day 3 research loop: plan, search, read,
judge, and a report whose every citation the run actually saw.

The flags stay flat — `evergrove-agent --task "…"`, no subcommand. That resolves the open
question S12 inherited from the plan's `prepare` subcommand: a second word buys nothing
while there is one thing to do, and `tools/cli.py` already carries subcommands because it
genuinely has four tools to choose between.

**This module composes nothing.** `service.prepare_focus_session` assembles the run and
`agents.run_agent` decides what it does; what is left here is argument parsing, the
`--no-research` round trip, a status line, and turning an outcome into an exit code.
Exit codes: 0 success, 1 the run failed, 2 the arguments were unusable.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from pydantic import ValidationError

from evergrove_agent.agents.prompt_context import max_topics_for
from evergrove_agent.agents.single_agent import PreparationFailed
from evergrove_agent.config import Settings, get_settings
from evergrove_agent.documents.base import DocumentReadError
from evergrove_agent.documents.reader import resolve_attachment
from evergrove_agent.llm import LLMError, LLMProvider, Message, build_provider
from evergrove_agent.llm.prompts import render_prompt
from evergrove_agent.schemas import FocusPreparationReport, TaskContext
from evergrove_agent.service import prepare_focus_session
from evergrove_agent.tools.base import RunBudget, RunContext

NO_RESEARCH_CONTEXT = (
    "No research was performed. This preparation rests on model knowledge alone, so "
    "there are no sources to cite."
)
NO_RESEARCH_ASSUMPTION = "No sources were consulted; this plan rests on model knowledge alone."
NO_RESEARCH_UNKNOWN = "Whether current documentation agrees with this plan — nothing was read."

ATTACHMENT_NEEDS_RESEARCH = (
    "--attachment cannot be combined with --no-research: reading it is a tool call, and "
    "the no-research path makes none. Drop one of the two flags."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evergrove-agent",
        description="Prepare one Evergrove focus session.",
    )
    parser.add_argument("--task", required=True, help="The task title, as the user wrote it.")
    parser.add_argument(
        "--minutes", type=int, default=25, help="Session length in minutes (5-180). Default 25."
    )
    parser.add_argument("--description", default=None, help="Optional extra context.")
    parser.add_argument(
        "--attachment",
        default=None,
        help="Optional .txt/.md/.pdf/.docx to prepare from. A relative path resolves "
        "inside ALLOWED_ATTACHMENT_DIR. Not available with --no-research.",
    )
    parser.add_argument(
        "--no-research",
        "--no-search",
        dest="no_research",
        action="store_true",
        help="Prepare from model knowledge alone. No search, no fetching, no sources.",
    )
    parser.add_argument(
        "--mode",
        choices=("single", "multi"),
        default="multi",
        help="Reasoning topology: 'multi' is the Supervisor coordinating a Researcher and "
        "an Appraiser (default); 'single' runs the same four stages as one agent. Both use "
        "the same tools, budgets, memory and validation. Ignored with --no-research, which "
        "reasons in one round trip and has no topology.",
    )
    parser.add_argument(
        "--provider",
        choices=("local", "hosted"),
        default=None,
        help="Override the configured provider for this run.",
    )
    parser.add_argument(
        "--fully-local",
        action="store_true",
        help="Refuse to start if any role resolves to the hosted provider (plan 30).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the progress line. The report itself is unaffected.",
    )
    parser.add_argument(
        "--trace-log",
        dest="trace_log",
        action="store_true",
        help="Print one JSON line per tool call to stderr as the run makes it. "
        "The same facts the trace records; see scripts/show_trace.py for the tree.",
    )
    parser.add_argument("--indent", type=int, default=2, help="JSON indent. 0 for one line.")
    return parser


async def prepare_without_research(
    task: TaskContext,
    provider: LLMProvider,
    settings: Settings,
) -> FocusPreparationReport:
    """One structured-output round trip: task in, validated report out.

    Deliberately **not** put through S9's `validate_report`. Two of its rules it satisfies
    by construction below, but `too_many_topics`, `topic_overlap` and `goal_not_narrowed`
    genuinely go unchecked here. Extending the grounding ladder to this path is a change to
    a shipped mode's behaviour and is left as its own decision rather than folded into the
    wiring work; it is recorded in the context document as outstanding.
    """
    prompt = render_prompt(
        "finalise",
        task_title=task.task_title,
        task_description=task.task_description or "(none given)",
        session_minutes=task.session_minutes,
        max_topics=max_topics_for(task.session_minutes),
        research_context=NO_RESEARCH_CONTEXT,
    )
    response = await provider.generate(
        [Message(role="user", content=prompt)],
        schema=FocusPreparationReport,
        temperature=settings.temperature,
    )
    report = FocusPreparationReport.model_validate_json(response.text)
    return _apply_bookkeeping(report, task, response.model)


def _apply_bookkeeping(
    report: FocusPreparationReport,
    task: TaskContext,
    model_used: str,
) -> FocusPreparationReport:
    """Overwrite everything the model does not get to decide.

    Provenance, identity and the no-research facts are bookkeeping, and bookkeeping is
    normal code (plan section 4). Doing it here also means a model that ignores the
    "no sources" instruction cannot smuggle an invented URL into the output.
    """
    assumptions = list(report.assumptions)
    if NO_RESEARCH_ASSUMPTION not in assumptions:
        assumptions = ([NO_RESEARCH_ASSUMPTION] + assumptions)[:6]

    unknowns = list(report.unknowns) or [NO_RESEARCH_UNKNOWN]

    return report.model_copy(
        update={
            "run_id": f"run_{uuid4().hex[:8]}",
            "generated_at": datetime.now(timezone.utc),
            "model_used": model_used,
            "original_task": task.task_title,
            "session_duration_minutes": task.session_minutes,
            "resources": [],
            "sources_examined": 0,
            "hops_used": 0,
            "assumptions": assumptions,
            "unknowns": unknowns,
        }
    )


# --- the status line ---------------------------------------------------------------------

_TICK_SECONDS = 0.4
"""How often the status line redraws. Fast enough to look alive, slow enough that a run
piped through `tee` into a file is not competing with the terminal for the stream."""

_SPINNER = "|/-\\"
"""ASCII on purpose: this is the one thing printed before a report exists, and a console
that cannot encode a fancier glyph would fail at exactly the moment nothing else has been
shown yet."""


def _elapsed(seconds: float) -> str:
    """`0:42`, `3:05`. Minutes and seconds, because a local run is measured in minutes."""
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def _status_line(ctx: RunContext, frame: str) -> str:
    """What the run has spent so far, read live off the ledger it is spending from.

    `RunBudget`'s counters are the run's own, not a second estimate kept here — which is why
    this needs no callback from the loop and no change to `run_agent`'s signature. The CLI
    builds the `RunContext`, hands the same object to `service.py`, and reads it while the
    run holds it.
    """
    budget = ctx.budget
    return (
        f"{frame} preparing  {_elapsed(budget.elapsed_seconds)}"
        f"   model calls {budget.model_calls_used}/{budget.max_model_calls}"
        f"   searches {budget.searches_used}/{budget.max_searches}"
        f"   page reads {budget.fetches_used}/{budget.max_fetches}"
    )


async def _tick(ctx: RunContext, stream: TextIO) -> None:
    """Redraw the status line until cancelled. Never raises into the run it is watching."""
    for step in range(sys.maxsize):
        line = _status_line(ctx, _SPINNER[step % len(_SPINNER)])
        with contextlib.suppress(OSError, ValueError):
            stream.write(f"\r{line:<78}")
            stream.flush()
        await asyncio.sleep(_TICK_SECONDS)


@contextlib.asynccontextmanager
async def progress(ctx: RunContext, *, enabled: bool, stream: TextIO | None = None):
    """Show what the run is spending while it spends it.

    On **stderr**, so a piped or redirected report is byte-identical to a silent run, and
    only when that stream is a terminal — a status line redrawn 150 times into a log file is
    noise, not progress. `--quiet` turns it off regardless.

    The ticker is a sibling task rather than anything the loop calls: `run_agent` awaits
    long provider calls, and a coroutine on the same event loop keeps the terminal answering
    throughout them. It is cancelled and the line erased in `finally`, so a failed run leaves
    a clean terminal for the error that follows it.
    """
    target = stream if stream is not None else sys.stderr
    if not (enabled and getattr(target, "isatty", lambda: False)()):
        yield
        return

    task = asyncio.create_task(_tick(ctx, target))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        with contextlib.suppress(OSError, ValueError):
            target.write(f"\r{'':<78}\r")
            target.flush()


# --- running one preparation --------------------------------------------------------------


def enable_trace_log(stream: TextIO | None = None) -> None:
    """Send the hooks' JSON trace lines to stderr, one per tool call.

    **stderr, not stdout.** The report is the only thing this CLI writes to stdout, and the
    progress line already lives on stderr for the documented reason that a redirected report
    must be byte-identical to a silent run. Plan section 13 says "to stdout"; that predates
    the shipped CLI and would corrupt the report, so the plan's own choice of mechanism —
    "stdlib JSON logging" — is honoured instead and the destination is set here.

    A bare `%(message)s` formatter, because the message already *is* the record: prefixing a
    JSON line with a level and a logger name would make it unparseable by the tools that
    read JSON lines. `propagate = False` keeps these off the root logger, so enabling this
    cannot start duplicating them through a handler an embedder installed.
    """
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("evergrove_agent.trace")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()

    if args.trace_log:
        enable_trace_log()

    if args.provider is not None:
        for role in ("supervisor", "researcher", "appraiser"):
            setattr(settings, f"{role}_provider", args.provider)

    if args.fully_local:
        try:
            settings.force_fully_local()
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    if args.no_research and args.attachment is not None:
        print(ATTACHMENT_NEEDS_RESEARCH, file=sys.stderr)
        return 2

    try:
        task = TaskContext(
            task_title=args.task,
            session_minutes=args.minutes,
            task_description=args.description,
            attachment_path=Path(args.attachment) if args.attachment else None,
        )
    except ValidationError as exc:
        print(f"Invalid task:\n{exc}", file=sys.stderr)
        return 2

    if task.attachment_path is not None:
        # Asked before the run starts rather than discovered as a NOT_FOUND tool failure
        # several model calls in. The guard itself stays `read_document`'s.
        try:
            resolve_attachment(task.attachment_path, settings=settings)
        except DocumentReadError as exc:
            print(f"--attachment cannot be read: {exc.message}", file=sys.stderr)
            return 2

    if args.no_research:
        return await _run_without_research(task, args, settings)
    return await _run_with_research(task, args, settings)


async def _run_without_research(
    task: TaskContext, args: argparse.Namespace, settings: Settings
) -> int:
    """The private path: one round trip, no tools, no network beyond the model itself."""
    provider = build_provider("supervisor", settings, override=args.provider)

    try:
        report = await prepare_without_research(task, provider, settings)
    except LLMError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValidationError as exc:
        # No retry ladder on this path — see `prepare_without_research`. It fails loudly
        # rather than returning a report that does not satisfy the schema.
        print(f"The model's reply did not satisfy FocusPreparationReport:\n{exc}", file=sys.stderr)
        return 1

    return _emit(report, args)


async def _run_with_research(
    task: TaskContext, args: argparse.Namespace, settings: Settings
) -> int:
    """A researched run, through the one entry point.

    The `RunContext` is built here so the status line can read the same ledger the run
    spends from. Nothing else about the run is assembled at this level — `--mode` is passed
    straight through, because which topology runs is `service.py`'s to resolve and a surface
    that reached for a loop itself would be the second composition root S11 exists to
    prevent.

    **A failed research run is not quietly downgraded to a no-research one.** The two modes
    make different promises about what a report rests on, and substituting one for the other
    after the fact would hand back a sourceless plan under the flags that asked for sources.
    """
    ctx = RunContext(budget=RunBudget.from_settings(settings))

    try:
        async with progress(ctx, enabled=not args.quiet):
            report = await prepare_focus_session(
                task, mode=args.mode, settings=settings, ctx=ctx
            )
    except PreparationFailed as exc:
        # The message already carries the run id, the attempts made and the last validation
        # errors; `exc.issues` carries the same facts structurally for any other caller.
        print(f"No valid preparation was produced: {exc}", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return _emit(report, args)


def _emit(report: FocusPreparationReport, args: argparse.Namespace) -> int:
    """The report on stdout, and the success code. The only thing that writes stdout."""
    print(report.model_dump_json(indent=args.indent or None))
    return 0


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
