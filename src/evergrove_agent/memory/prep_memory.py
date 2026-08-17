"""Persistent preparation memory: what survives between runs (plan section 12.2).

The `prep_memory` table and nothing else. A validated `FocusPreparationReport` goes in as a
compact summary; a later run asking about the same subject gets that summary back. This is
what makes "Continue PostgreSQL indexing" mean something other than "start again".

Storage only, on the same terms as `cache.py` and `tracing/store.py`: it opens no connection
of its own, it takes an injected `now`, and **it raises** — a `sqlite3.Error` propagates so a
caller (and a test) can see it. Keeping a memory failure from ending a fifteen-minute
research run is `tools/memory_tools.py`'s job, in one place, exactly as `Tracer` does it for
the trace.

Nothing here reads a model, a tool or the network, and nothing here decides anything. What a
recalled preparation *does* to the next run's prompts is the memory-aware prompting task's
business; this module answers one question, "what did we prepare for this before?", and one
question only.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta

from evergrove_agent.config import get_settings
from evergrove_agent.memory.db import transaction
from evergrove_agent.schemas import FocusPreparationReport, PreviousPreparation

_COLUMNS = (
    "task_key, original_task, interpreted_goal, session_objective,"
    " topics_covered, topics_deferred, source_urls, run_id, created_at"
)

_CANDIDATE_LIMIT = 5
"""How many rows a recall inspects before giving up.

Newest-first, and the age filter is applied in Python (see `recall_previous_preparation`), so
this bounds the work on a task someone has prepared for twenty times. Five is generous: the
first row survives the window unless the whole recent history is stale, in which case an
older one would be staler still.
"""

_STOPWORDS: frozenset[str] = frozenset(
    {
        # Grammar.
        "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with",
        "from", "into", "about", "my", "our", "its", "it", "this", "that", "these",
        "those", "is", "are", "be", "how", "what", "why", "when", "some", "any",
        # Intent verbs and framing nouns. These are what a user changes between one
        # session and the next while meaning the same subject — "Learn X" today,
        # "Continue X" tomorrow, "X revision" next week — so they cannot be part of the
        # key without making every continuation look like a new task.
        "learn", "learning", "learnt", "learned", "study", "studying", "read",
        "reading", "review", "reviewing", "revise", "revising", "revision",
        "understand", "understanding", "continue", "continuing", "continued",
        "finish", "finishing", "start", "starting", "begin", "beginning",
        "practice", "practise", "practising", "explore", "exploring", "master",
        "mastering", "intro", "introduction", "basics", "fundamentals", "overview",
        "more", "further", "again", "next", "session", "part", "deep", "dive",
    }
)
"""Words a task key drops.

Deliberately small and explicit rather than an imported stopword corpus: a key that changes
because a dependency updated its word list would silently stop matching every preparation
already on disk. Adding a word here is a behaviour change and needs a test alongside it.
"""

_TOKEN = re.compile(r"[a-z0-9+#.]+")
"""What counts as a token after lowercasing. Keeps `c++`, `c#`, `node.js` and `3.11`
intact — dropping their punctuation would fold `c#` into `c`.
"""


def normalize_task_key(task_title: str) -> str:
    """The lookup key for a task title: lowercased, stripped, deduped and sorted.

    Two titles get the same key when they name the same subject:
    `"Learn PostgreSQL indexing"`, `"postgresql indexing"`, `"Continue PostgreSQL
    Indexing!"` and `"Indexing in PostgreSQL"` all key to `"indexing postgresql"`.

    **Sorted, which is more than the plan's "stopwords removed".** Word order carries no
    subject information here — "indexing in PostgreSQL" and "PostgreSQL indexing" are one
    task — and sorting is what lets the second one match the first. It costs nothing, and
    the readable form is never lost: `PreviousPreparation.original_task` keeps the title as
    the user typed it.

    **Deduped**, so "Indexing and index types" does not key differently from a title that
    happens to repeat a word.

    A title made *entirely* of stopwords (`"Learn more"`) falls back to its full token set
    rather than returning an empty string. An empty key would match every other
    all-stopword title, which is the one way this function could recall a preparation about
    a genuinely different subject.
    """
    tokens = _TOKEN.findall(task_title.lower())
    kept = [token for token in tokens if token not in _STOPWORDS]
    return " ".join(sorted(set(kept or tokens)))


def save_preparation(
    connection: sqlite3.Connection,
    *,
    report: FocusPreparationReport,
    now: datetime | None = None,
) -> PreviousPreparation:
    """Store `report` as a summary a later run can continue from.

    **Only ever called with a report that already passed `validate_report`.** That is
    guaranteed at the call site rather than re-checked here: `finalise()` either returns a
    validated report or raises `PreparationFailed`, so the loop's success path is the only
    place a report exists to save. A second grounding check here would be a second
    definition of the project's strongest anti-hallucination rule.

    Appends rather than replacing. A task prepared for three times keeps three rows, because
    the history is worth having and `recall_previous_preparation` reads the newest — the same
    reason the trace keeps every run instead of the last one.
    """
    entry = PreviousPreparation(
        task_key=normalize_task_key(report.original_task),
        original_task=report.original_task,
        interpreted_goal=report.interpreted_goal,
        session_objective=report.session_objective,
        topics_covered=list(report.topics_to_cover),
        topics_deferred=list(report.topics_to_skip),
        source_urls=[str(resource.url) for resource in report.resources],
        run_id=report.run_id,
        created_at=now or datetime.now(UTC),
    )

    with transaction(connection) as cursor:
        cursor.execute(
            f"INSERT INTO prep_memory ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.task_key,
                entry.original_task,
                entry.interpreted_goal,
                entry.session_objective,
                json.dumps(entry.topics_covered),
                json.dumps(entry.topics_deferred),
                json.dumps(entry.source_urls),
                entry.run_id,
                entry.created_at.isoformat(),
            ),
        )
    return entry


def recall_previous_preparation(
    connection: sqlite3.Connection,
    *,
    task_title: str,
    max_age_days: int | None = None,
    now: datetime | None = None,
) -> PreviousPreparation | None:
    """The most recent preparation for this task within the recall window, or `None`.

    `max_age_days` defaults to `MEMORY_RECALL_MAX_AGE_DAYS` (30). Rows are never deleted
    when they age out — a preparation stays on disk for audit and simply stops being
    *recalled*, the same stance `cache.py` takes towards an expired page.

    The age comparison happens in Python against `now`, not in SQL. The stored timestamp
    format is this module's business, and a row written by hand or by an older build could be
    naive — parsing it through `_parse_timestamp` means mixed awareness can never raise
    inside a comparison. SQL does the part it is good at: an indexed equality on `task_key`.

    A row whose JSON columns cannot be read degrades to a **miss** rather than an exception,
    the same way `search_cache` treats a payload from an older build: memory is an
    enhancement, and one unreadable row must not stop a newer one being found.
    """
    days = max_age_days if max_age_days is not None else (
        get_settings().memory_recall_max_age_days
    )
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)

    rows = connection.execute(
        f"SELECT {_COLUMNS} FROM prep_memory WHERE task_key = ?"
        " ORDER BY created_at DESC, id DESC LIMIT ?",
        (normalize_task_key(task_title), _CANDIDATE_LIMIT),
    ).fetchall()

    for row in rows:
        entry = _to_previous_preparation(row)
        if entry is None:
            continue
        if entry.created_at >= cutoff:
            return entry
    return None


def _to_previous_preparation(row: sqlite3.Row) -> PreviousPreparation | None:
    """Rebuild a `prep_memory` row, or `None` if this one is not readable.

    The three list columns are JSON, and the row may predate a change to what this module
    writes. Returning `None` for a corrupt row keeps one bad entry from taking the recall
    path down — the same degradation `memory/search_cache.py` applies to a stale payload.
    """
    try:
        return PreviousPreparation(
            task_key=row["task_key"],
            original_task=row["original_task"],
            interpreted_goal=row["interpreted_goal"],
            session_objective=row["session_objective"],
            topics_covered=json.loads(row["topics_covered"]),
            topics_deferred=json.loads(row["topics_deferred"]),
            source_urls=json.loads(row["source_urls"]),
            run_id=row["run_id"],
            created_at=_parse_timestamp(row["created_at"]),
        )
    except (ValueError, TypeError):
        # `ValueError` covers both `json.JSONDecodeError` and Pydantic's
        # `ValidationError`; `TypeError` covers a JSON payload of the wrong shape.
        return None


def _parse_timestamp(value: str) -> datetime:
    """Read a stored ISO-8601 timestamp, treating a naive one as UTC.

    Everything is written aware and in UTC; the fallback only guards a row written by hand
    or by an older build. The fourth local copy of this — `cache.py`, `search_cache.py` and
    `tracing/store.py` each keep their own — and promoting a shared one would be an
    unrelated refactor of three passing modules.
    """
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
