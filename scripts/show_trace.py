"""Print one recorded run as a tree (Day 4 T6).

    uv run python scripts/show_trace.py a71c3f
    uv run python scripts/show_trace.py a71c3f --summaries
    uv run python scripts/show_trace.py a71c3f --db data/agent.sqlite3

Deliberately thin, the same way `tools/cli.py` is: argument parsing, two read-only queries,
one formatter, an exit code. Every decision about what a trace *looks* like belongs to
`tracing/render.py`, which is pure and therefore testable without a database; every
decision about what a trace *contains* was made when the rows were written. Nothing here
inserts, updates, or opens a run.

Exit codes follow `main.py`: 0 the trace was rendered, 1 nothing is recorded under that run
id (or the database could not be read — a message, never a traceback), 2 the arguments were
unusable, which is argparse's own.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from evergrove_agent.memory import db
from evergrove_agent.tracing import get_run, get_spans, render_trace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="show_trace",
        description="Show what one run did, as a tree. Reads the trace; changes nothing.",
    )
    parser.add_argument(
        "run_id",
        help="The run to show, as it appears in a report's run_id or a JSON trace log line.",
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        default=None,
        help="The database to read. Defaults to DB_PATH from the environment.",
    )
    parser.add_argument(
        "--summaries",
        action="store_true",
        help="Also show each span's stored input and output summaries.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """Read the run and its spans, print the tree, and answer with the exit code.

    The run header and the spans are fetched **separately and both are optional**, because
    `spans.run_id` carries no foreign key (`memory/db.py` explains why): a run whose header
    write failed still has spans worth reading, and a run that was killed before its first
    tool call has a header and none. Only when both come back empty is there genuinely
    nothing recorded under this id.
    """
    path = Path(args.db_path) if args.db_path else None

    try:
        with db.open_database(path) as connection:
            record = get_run(connection, args.run_id)
            spans = get_spans(connection, args.run_id)
    except (sqlite3.Error, OSError) as exc:
        print(f"could not read the trace database: {exc}", file=sys.stderr)
        return 1

    if record is None and not spans:
        print(
            f"no trace recorded for run {args.run_id!r}. "
            "Check the run id, or point --db at the database the run wrote to.",
            file=sys.stderr,
        )
        return 1

    for line in render_trace(record, spans, summaries=args.summaries):
        print(line)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
