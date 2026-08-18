"""The Supervisor's stop/continue rule, read straight off a verdict (Day 5 T5).

Every other suite exercises this rule through a whole run, which is the right way to prove
that a hop happened — and the wrong way to prove *why*. A loop reaches these two functions
only along the paths one script happens to take, so the shapes that matter most here (a
whitespace-only follow-up, a `sufficient` verdict with exactly one accepted source) are
awkward to reach and easy to leave untested.

So this file calls them directly, over the whole space of verdicts, and asserts the one
property the loop depends on that neither function can state alone:

    `_stop_after_hop(v) is None`  ⟺  `_outstanding_followup(v) is not None`

They are two readings of one verdict. The first decides whether the loop breaks; the second
decides what the next hop asks. If they ever disagree, the run either stops with a follow-up
nobody spent or continues with no question to spend — and both failures look like ordinary
control flow from the outside, which is exactly why the equivalence is pinned here rather
than left as a comment.

Pure: no model, no registry, no network, no database, no budget.
"""

from __future__ import annotations

import pytest

from evergrove_agent.agents.runtime import StopReason
from evergrove_agent.agents.supervisor import (
    _followup_decision,
    _MIN_ACCEPTED,
    _outstanding_followup,
    _stop_after_hop,
)
from evergrove_agent.schemas import AppraisalVerdict

FOLLOWUP = "how do I read the plan EXPLAIN ANALYZE prints"


def verdict(
    sufficient: bool, *, followup: str | None = None, accepted: int = 0
) -> AppraisalVerdict:
    """A verdict with only the three fields the rule actually reads.

    `accepted` is a count rather than a list of objects because the rule counts entries and
    reads nothing inside them — building realistic `AcceptedSource`s here would dress the
    test up as being about the judgement when it is about the arithmetic. The bare strings
    still exercise `AcceptedSource`'s coercion, which is what a small model really sends.
    """
    return AppraisalVerdict(
        sufficient=sufficient,
        requested_followup=followup,
        reasoning="judged",
        accepted=[f"source {n}" for n in range(accepted)],  # type: ignore[arg-type]
    )


# --- the rule, verdict by verdict -----------------------------------------------------------

CASES: list[tuple[str, AppraisalVerdict | None, StopReason | None]] = [
    # No verdict at all is not a verdict of "insufficient": the stage could not answer, and
    # spending a hop on a question nobody asked for would be inventing one.
    ("no verdict", None, "appraiser_unavailable"),
    # The plan's success condition, and the two ways of just missing it. Two accepted sources
    # is the bar; one is the shape of the failure the Appraiser exists to catch — the system
    # believing its own first search result — so it finalises honestly instead.
    ("sufficient, two accepted", verdict(True, accepted=2), "sufficient"),
    ("sufficient, three accepted", verdict(True, accepted=3), "sufficient"),
    ("sufficient, one accepted", verdict(True, accepted=1), "thin_evidence"),
    ("sufficient, none accepted", verdict(True, accepted=0), "thin_evidence"),
    # A thin "yes" must not buy a hop even when the model also volunteered a follow-up: the
    # plan continues only on `sufficient == false`.
    (
        "sufficient but thin, with a follow-up",
        verdict(True, followup=FOLLOWUP, accepted=1),
        "thin_evidence",
    ),
    # The only verdict that continues, and the two honest ways of not continuing.
    ("insufficient, with a follow-up", verdict(False, followup=FOLLOWUP), None),
    ("insufficient, no follow-up", verdict(False), "no_followup"),
    ("insufficient, null follow-up", verdict(False, followup=None), "no_followup"),
    # A model that answers `"   "` has not asked a question. Stripping is what stops a blank
    # string reaching `ResearchAssignment`, whose `research_question` is `min_length=1`.
    ("insufficient, blank follow-up", verdict(False, followup="   "), "no_followup"),
]


@pytest.mark.parametrize(
    ("verdict_under_test", "expected"),
    [pytest.param(case, expected, id=name) for name, case, expected in CASES],
)
def test_the_verdict_alone_decides_whether_the_run_continues(
    verdict_under_test: AppraisalVerdict | None, expected: StopReason | None
) -> None:
    """The plan's stop condition, case by case (section 8.3).

    One parameterized test rather than nine, because every case is the same question asked of
    a different verdict. What it catches is a rule quietly widening: `sufficient` alone
    stopping the run again, a blank follow-up buying a hop, or a missing verdict being read as
    an insufficient one — each of which produces a report that looks entirely normal and rests
    on evidence the judge never endorsed.
    """
    assert _stop_after_hop(verdict_under_test) == expected


@pytest.mark.parametrize(
    "verdict_under_test",
    [pytest.param(case, id=name) for name, case, _ in CASES],
)
def test_continuing_and_having_a_follow_up_are_the_same_condition(
    verdict_under_test: AppraisalVerdict | None,
) -> None:
    """The invariant the loop rests on, over every verdict shape above.

    The loop asks `_stop_after_hop` whether to break and then, at the top of the next pass,
    asks `_outstanding_followup` what the next hop should ask. Nothing structurally ties those
    two answers together, so a future edit to either one can separate them — and the result is
    not a crash but a wrong run: a verdict that says continue while no follow-up is outstanding
    sends the run back to the planner, which is precisely the second opinion T5 removed.
    """
    continues = _stop_after_hop(verdict_under_test) is None
    has_followup = _outstanding_followup(verdict_under_test) is not None
    assert continues == has_followup


# --- what the follow-up becomes -------------------------------------------------------------


def test_the_next_hop_asks_the_appraisers_question_and_not_one_of_ours() -> None:
    """The follow-up reaches the assignment as the Appraiser's own words.

    This is what makes a second hop agentic rather than a scripted retry, so the assertion is
    on identity, not on similarity: the moment our code paraphrases, summarises or prefixes
    the question, hop 2 is asking something the evidence did not ask for. `RESEARCH` is
    asserted alongside it because `_assign` is only defined for that action and
    `SupervisorDecision`'s own validator rejects the pair coming apart.
    """
    decision = _followup_decision(FOLLOWUP)

    assert decision.action == "RESEARCH"
    assert decision.research_question == FOLLOWUP


def test_a_followup_at_its_maximum_length_still_fits_a_research_question() -> None:
    """Two schema bounds that must not cross, checked rather than trusted.

    `requested_followup` allows 200 characters and `research_question` allows 300, so the
    handover is safe today — but they live in different models, and narrowing the second (or
    widening the first) would turn the longest real follow-up into a `ValidationError` raised
    from inside the loop, on a run that had already spent a hop earning it.
    """
    longest = "x" * 200

    assert _followup_decision(longest).research_question == longest


def test_the_accepted_source_bar_is_the_plans_number() -> None:
    """`_MIN_ACCEPTED` is the plan's "at least 2 accepted sources", not a tuning knob.

    Pinned because it is the one number in this rule with an external authority behind it, and
    because lowering it to 1 would make every case above pass while quietly restoring the
    behaviour T5 exists to remove.
    """
    assert _MIN_ACCEPTED == 2
