"""CLI entry point.

Day 1 supports exactly one path: `--no-research`, which produces a preparation from
model knowledge alone. That is the plan's Day 1 demo, and it is also the only fully
private mode, because it is the only one where the task text never reaches a search
provider (plan section 30).

Research mode — search, fetch, appraise, multi-hop — is the Day 3 loop and is refused
with a clear message rather than faked.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from evergrove_agent.config import Settings, get_settings
from evergrove_agent.llm import LLMError, LLMProvider, Message, build_provider
from evergrove_agent.llm.prompts import render_prompt
from evergrove_agent.schemas import FocusPreparationReport, TaskContext

NO_RESEARCH_CONTEXT = (
    "No research was performed. This preparation rests on model knowledge alone, so "
    "there are no sources to cite."
)
NO_RESEARCH_ASSUMPTION = "No sources were consulted; this plan rests on model knowledge alone."
NO_RESEARCH_UNKNOWN = "Whether current documentation agrees with this plan — nothing was read."


def max_topics_for(minutes: int) -> int:
    """The session-sizing rule from plan section 17: `max(3, minutes // 5)`, capped at 8."""
    return min(8, max(3, minutes // 5))


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
        "--attachment", default=None, help="Optional .txt/.md/.pdf to prepare from (Day 2)."
    )
    parser.add_argument(
        "--no-research",
        "--no-search",
        dest="no_research",
        action="store_true",
        help="Prepare from model knowledge alone. No search, no fetching, no sources.",
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
    parser.add_argument("--indent", type=int, default=2, help="JSON indent. 0 for one line.")
    return parser


async def prepare_without_research(
    task: TaskContext,
    provider: LLMProvider,
    settings: Settings,
) -> FocusPreparationReport:
    """One structured-output round trip: task in, validated report out."""
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


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()

    if args.provider is not None:
        for role in ("supervisor", "researcher", "appraiser"):
            setattr(settings, f"{role}_provider", args.provider)

    if args.fully_local:
        try:
            settings.force_fully_local()
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    if not args.no_research:
        print(
            "Research mode is not built yet — it is the Day 3 loop (search, fetch, "
            "appraise, multi-hop).\nRe-run with --no-research to prepare from model "
            "knowledge alone.",
            file=sys.stderr,
        )
        return 2

    if args.attachment is not None:
        print(
            "--attachment is not read yet; the read_document tool is Day 2. "
            "Re-run without it.",
            file=sys.stderr,
        )
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

    provider = build_provider("supervisor", settings, override=args.provider)

    try:
        report = await prepare_without_research(task, provider, settings)
    except LLMError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValidationError as exc:
        # The model answered, but not with our schema. Day 3 adds the retry ladder from
        # plan section 17; today it fails loudly rather than returning a partial report.
        print(f"The model's reply did not satisfy FocusPreparationReport:\n{exc}", file=sys.stderr)
        return 1

    print(report.model_dump_json(indent=args.indent or None))
    return 0


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
