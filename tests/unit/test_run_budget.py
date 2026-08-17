"""The run's spend ledger — the only thing that stops a run researching forever.

Every rule tested here fails as a real cost rather than a type error: one search call more
than the budget is live SerpAPI quota, a deadline that does not bite is a run that never
returns, and a ledger that misreports what it spent is a degraded report that reads like a
confident one.
"""

from __future__ import annotations

import pytest

from evergrove_agent.config import Settings
from evergrove_agent.tools import BudgetKind, RunBudget, RunContext

KINDS: tuple[BudgetKind, ...] = ("search", "fetch", "model_call")


class FakeClock:
    """A monotonic clock the test drives, so a 900-second timeout costs no wall time."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _budget(clock: FakeClock | None = None, **overrides: float) -> RunBudget:
    limits: dict[str, float] = {
        "max_searches": 2,
        "max_fetches": 2,
        "max_model_calls": 2,
        "max_hops": 3,
        "max_sources_kept": 3,
        "timeout_s": 100.0,
    }
    limits.update(overrides)
    if clock is None:
        return RunBudget(**limits)
    return RunBudget(clock=clock, **limits)


class TestClaim:
    @pytest.mark.parametrize("kind", KINDS)
    def test_grants_exactly_the_limit_then_refuses(self, kind: BudgetKind) -> None:
        """An off-by-one here is one live search, one extra page fetch or one extra model
        call per run — the first of those spends SerpAPI quota the budget exists to
        protect."""
        budget = _budget()

        assert budget.remaining(kind) == 2
        assert budget.claim(kind) is True
        assert budget.claim(kind) is True
        assert budget.remaining(kind) == 0
        assert budget.claim(kind) is False

    def test_a_refused_claim_increments_nothing(self) -> None:
        """A ledger that keeps counting past its ceiling reports a spend that never
        happened, and `remaining` then goes negative — which reaches
        `ResearchAssignment.max_searches` (`ge=0`) as a validation error mid-run."""
        budget = _budget(max_searches=1)
        budget.claim("search")

        for _ in range(3):
            assert budget.claim("search") is False

        assert budget.searches_used == 1
        assert budget.remaining("search") == 0

    def test_kinds_are_spent_independently(self) -> None:
        """One shared counter would let a run's fetches eat its search allowance, so a
        hop that read four pages could no longer search at all."""
        budget = _budget()
        budget.claim("search")

        assert budget.remaining("search") == 1
        assert budget.remaining("fetch") == 2
        assert budget.remaining("model_call") == 2


class TestDeadline:
    def test_claim_refuses_once_the_run_is_out_of_time(self) -> None:
        """The failure `TOTAL_RUN_TIMEOUT_S` exists to prevent: a loop that keeps deciding
        on one more hop and never returns. Checking the clock inside `claim` is what makes
        the run stop at its next step rather than notice afterwards."""
        clock = FakeClock()
        budget = _budget(clock, timeout_s=10.0)

        assert budget.claim("search") is True

        clock.advance(10.0)

        assert budget.expired is True
        assert budget.remaining_seconds == 0.0
        assert budget.claim("search") is False
        assert budget.claim("model_call") is False
        assert budget.searches_used == 1

    def test_time_left_is_reported_from_the_start_of_the_run(self) -> None:
        """`remaining_seconds` sizes the loop's decision about whether another hop fits;
        measuring from anything but run start would hand a late hop a full budget."""
        clock = FakeClock()
        budget = _budget(clock, timeout_s=100.0)

        clock.advance(30.0)

        assert budget.elapsed_seconds == 30.0
        assert budget.remaining_seconds == 70.0
        assert budget.expired is False


class TestLimitsFromSettings:
    def test_limits_are_snapshotted_not_re_read(self) -> None:
        """A budget that re-read `Settings` mid-run could be moved under a running loop,
        and a trace could no longer be read back against fixed numbers."""
        settings = Settings(_env_file=None)
        budget = RunBudget.from_settings(settings)

        assert budget.max_searches == settings.max_search_calls
        assert budget.max_fetches == settings.max_fetch_calls
        assert budget.max_model_calls == settings.max_model_calls
        assert budget.max_hops == settings.max_hops
        assert budget.max_sources_kept == settings.max_sources_kept
        assert budget.timeout_s == float(settings.total_run_timeout_s)

        raised = Settings(_env_file=None, max_search_calls=99)
        assert budget.max_searches != raised.max_search_calls

    def test_a_run_context_carries_a_budget_by_default(self) -> None:
        """Every tool and, on Day 4, every hook reaches the ledger through `ctx`. A context
        built without one would silently disable enforcement for that run."""
        ctx = RunContext()

        assert isinstance(ctx.budget, RunBudget)
        assert ctx.budget.remaining("search") == ctx.budget.max_searches


class TestLimitsCountedElsewhere:
    @pytest.mark.parametrize("used, expected", [(0, 3), (3, 0), (5, 0)])
    def test_hops_and_sources_remaining_clamp_at_zero(
        self, used: int, expected: int
    ) -> None:
        """Both answers feed `ResearchAssignment`, whose allowances are `ge=0`: a negative
        one would fail validation in the middle of a run rather than stop it cleanly.
        The counts come from `RunState`, so nothing here keeps a second copy of them."""
        budget = _budget(max_hops=3, max_sources_kept=3)

        assert budget.hops_remaining(used) == expected
        assert budget.sources_remaining(used) == expected


class TestExhaustedLimits:
    def test_names_every_spent_limit_including_time(self) -> None:
        """S8 and S10 read this to finalise honestly. Under-reporting it produces a report
        that stayed silent about why it is thin — the one failure mode the whole
        degradation path exists to prevent."""
        clock = FakeClock()
        budget = _budget(clock, max_searches=1, timeout_s=10.0)

        assert budget.exhausted_limits == ()

        budget.claim("search")
        assert budget.exhausted_limits == ("search",)

        clock.advance(10.0)
        assert budget.exhausted_limits == ("search", "time")
