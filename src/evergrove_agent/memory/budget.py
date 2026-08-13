"""The monthly live-search guard: our own ceiling, kept in our own file.

Plan section 10. SerpAPI's free tier is 250 searches a calendar month and nothing external
holds us to `MONTHLY_SEARCH_BUDGET` — the provider simply keeps answering until the tier is
gone and then stops mid-run. So the count lives here, in the same SQLite file, keyed by
calendar month so a new month is a new row and a fresh allowance with nothing to reset.

Storage and arithmetic only: no HTTP, no backend, no provider name beyond the one set below
that says which backends cost anything. `web_search` (S7) calls `reserve_search_call` after
the cache missed, and turns a refusal into `ErrorCode.MONTHLY_BUDGET_EXCEEDED` — a refusal
is a value the model can act on, and building it belongs above this line, exactly as
`CachedSource` never becomes a `ToolResult` either.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from evergrove_agent.config import get_settings
from evergrove_agent.memory.db import transaction

QUOTA_CONSUMING_BACKENDS: frozenset[str] = frozenset({"serpapi"})
"""Backends whose calls come out of the monthly budget.

Only SerpAPI has a paid-tier ceiling. `fixture` replays recorded JSON, `academic` is
OpenAlex/Crossref/arXiv (free, unmetered) and `ddgs` is unmetered too — none of them may
ever move this counter, or an offline test run would spend the live tier. The rule lives
here rather than in `web_search` so it is one list, and provable before a backend exists.
"""


@dataclass(frozen=True)
class BudgetReservation:
    """The answer to "may I make one live search right now?", with the numbers behind it.

    A storage-level value, not a `ToolResult`: `used` and `limit` are what `web_search`
    needs to write a refusal message the model can understand.
    """

    granted: bool
    month: str
    """The calendar month the decision was made against, `YYYY-MM` in UTC."""
    used: int
    """Live searches spent this month — including this one when `granted`."""
    limit: int


def consumes_quota(backend: str) -> bool:
    """Whether a call to `backend` should be charged against the monthly budget."""
    return backend in QUOTA_CONSUMING_BACKENDS


def current_month(now: datetime | None = None) -> str:
    """The calendar month `now` falls in, as `YYYY-MM` in UTC.

    UTC rather than local time so the month a call is charged to does not depend on where
    the machine thinks it is, and the counter cannot be nudged by a timezone change.
    """
    return (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m")


def get_search_usage(
    connection: sqlite3.Connection, *, now: datetime | None = None
) -> int:
    """Live searches already spent in `now`'s month — zero when the month has no row."""
    row = connection.execute(
        "SELECT used FROM search_budget WHERE month = ?", (current_month(now),)
    ).fetchone()
    return int(row["used"]) if row is not None else 0


def reserve_search_call(
    connection: sqlite3.Connection,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> BudgetReservation:
    """Claim one live search against this month's budget, or refuse.

    Claims *before* the call rather than recording after it: a search that times out may
    still have counted at the provider, so over-counting is the safe direction to be wrong
    in. There is deliberately no refund path — one uncharged failure is cheaper than a
    bookkeeping bug that hands back calls the provider already took.

    The check and the increment are a single statement, so two callers cannot both read
    `used == limit - 1` and both spend it: `ON CONFLICT ... WHERE used < limit` makes the
    row itself the arbiter, and a returned row *is* the grant.
    """
    month = current_month(now)
    cap = limit if limit is not None else get_settings().monthly_search_budget

    # The WHERE clause only guards the update branch, so a zero budget would still let the
    # first call of a new month through the plain INSERT. Refuse it before we get there.
    if cap <= 0:
        return BudgetReservation(
            granted=False,
            month=month,
            used=get_search_usage(connection, now=now),
            limit=cap,
        )

    with transaction(connection) as cursor:
        row = cursor.execute(
            "INSERT INTO search_budget (month, used) VALUES (?, 1)"
            " ON CONFLICT(month) DO UPDATE SET used = used + 1 WHERE used < ?"
            " RETURNING used",
            (month, cap),
        ).fetchone()

    if row is None:
        # No row came back: the update was filtered out, so the month is at or over its
        # ceiling — which is also what a limit lowered below an existing count looks like.
        return BudgetReservation(
            granted=False,
            month=month,
            used=get_search_usage(connection, now=now),
            limit=cap,
        )
    return BudgetReservation(granted=True, month=month, used=int(row["used"]), limit=cap)
