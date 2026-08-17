"""The third validation of a report: the business rules Pydantic cannot express (S9).

`FocusPreparationReport` is validated twice before it reaches here — once by constrained
decoding against the JSON Schema, once by `model_validate_json`. Both prove *shape*:
types, lengths, enum membership. Neither can know which URLs this run actually saw, and
that is the one thing worth proving. A model can return a perfectly shaped report citing
a page that sounds exactly right and appears nowhere in the run.

So the strongest guard in the project (plan section 17) is **set membership**, not a
prompt instruction: every cited URL must belong to the set this run discovered, and a URL
nobody opened may only be cited as `authority="unknown"`. A model cannot talk its way past
a set.

Everything here is pure. No model, no network, no database, no clock — the same report and
the same evidence always produce the same issues, which is what makes it safe for S10 to
run it between attempts and quote the result straight back at the model.

Registered like `normalize_sources` and, for the same reason, never advertised: whether a
report is acceptable is not a judgement the model under test gets to make. Internal callers
compose with the pure `validate_report` function rather than calling back through the
registry.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evergrove_agent.schemas import FocusPreparationReport, ToolResult
from evergrove_agent.search.normalize import canonicalize_url
from evergrove_agent.tools.base import RunContext

ValidationCode = Literal[
    "ungrounded_url",
    "authority_overclaim",
    "resources_without_research",
    "unknowns_required",
    "topic_overlap",
    "too_many_topics",
    "goal_not_narrowed",
]
"""Why a report was rejected, as a stable identifier.

Codes rather than message matching: S10 quotes `message` at a model, and a reworded
sentence must not break a test or a later caller that routes on the reason.
"""


class ReportIssue(BaseModel):
    """One rule the report broke, at one place in it."""

    model_config = ConfigDict(extra="forbid")

    code: ValidationCode
    field: str = Field(min_length=1, max_length=120)
    """Where, in the report's own vocabulary — `resources[2].url`, `topics_to_cover`."""
    message: str = Field(min_length=1, max_length=400)
    """One line, written to be shown to a model unedited: what is wrong and what would
    be acceptable instead. Never a stack trace, never a rule number."""


class ReportValidation(BaseModel):
    """The verdict on one report — the structured result S10 retries against."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    issues: list[ReportIssue] = Field(default_factory=list, max_length=20)

    def as_lines(self) -> list[str]:
        """`- field: message` per issue, in the order they were found.

        The exact strings S10 stores in `RunState.validation_errors` and quotes into the
        retry message, so what a failed run reports and what the model was told to fix are
        one list rather than two renderings of it.
        """
        return [f"- {issue.field}: {issue.message}" for issue in self.issues]


_PUNCTUATION = re.compile(r"[^\w\s]+")
_WHITESPACE = re.compile(r"\s+")


def _comparable(text: str) -> str:
    """Case, punctuation and spacing removed — enough to catch a restatement, no more.

    Deliberately crude. The rule being enforced is "the goal must not be the task typed
    back"; anything fuzzier would start rejecting goals for being insufficiently different,
    which is a judgement no deterministic check should be making.
    """
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", text.casefold())).strip()


def _canonical_set(urls: frozenset[str]) -> set[str]:
    """The evidence, reduced to the one form membership is tested against.

    Already canonical in practice — `GatheredSource.url` is built by this same function —
    but a caller assembling a set by hand (a test, the tool wrapper, a later CLI) should
    not be able to make a valid report look ungrounded by writing a trailing slash.
    """
    return {canonical for url in urls if (canonical := canonicalize_url(url))}


def validate_report(
    report: FocusPreparationReport,
    *,
    evidence_urls: frozenset[str],
    fetched_urls: frozenset[str],
    max_topics: int,
    research_performed: bool,
) -> ReportValidation:
    """Check a shaped report against what the run actually gathered.

    `evidence_urls` and `fetched_urls` are `RunState`'s own projections — discovered, and
    actually opened. Both sides of every URL comparison go through `canonicalize_url`, the
    same function `GatheredSource.url` was built with: a report citing the same page with a
    trailing slash is the same page, and treating it as ungrounded would burn every retry
    attempt correcting nothing.

    `max_topics` is passed in rather than computed here so the session-sizing rule keeps
    exactly one definition (`agents.prompt_context.max_topics_for`, which fills the same
    placeholder in `finalise.md`) — and so a tool never imports the agent layer.

    Returns every issue found, not the first: a retry that fixes one problem and is then
    told about the next has spent an attempt to learn something we already knew.
    """
    issues: list[ReportIssue] = []
    grounded = _canonical_set(evidence_urls)
    opened = _canonical_set(fetched_urls)

    for index, resource in enumerate(report.resources):
        where = f"resources[{index}]"
        cited = canonicalize_url(str(resource.url))
        if cited is None or cited not in grounded:
            issues.append(
                ReportIssue(
                    code="ungrounded_url",
                    field=f"{where}.url",
                    message=(
                        f"{resource.url} is not one of the sources this run gathered. "
                        "Cite only URLs listed in the research section, or drop it."
                    ),
                )
            )
        elif cited not in opened and resource.authority != "unknown":
            issues.append(
                ReportIssue(
                    code="authority_overclaim",
                    field=f"{where}.authority",
                    message=(
                        f"{resource.url} was found but never opened, so it cannot be "
                        f'called "{resource.authority}". Use authority "unknown".'
                    ),
                )
            )

    if not research_performed:
        if report.resources:
            issues.append(
                ReportIssue(
                    code="resources_without_research",
                    field="resources",
                    message=(
                        "No research was performed, so there are no sources to cite. "
                        "Leave resources empty."
                    ),
                )
            )
        if not report.unknowns:
            issues.append(
                ReportIssue(
                    code="unknowns_required",
                    field="unknowns",
                    message=(
                        "No research was performed, so unknowns cannot be empty. Say "
                        "plainly that this plan rests on model knowledge alone."
                    ),
                )
            )

    covered = {_comparable(topic) for topic in report.topics_to_cover}
    repeated = sorted(
        topic for topic in report.topics_to_skip if _comparable(topic) in covered
    )
    if repeated:
        issues.append(
            ReportIssue(
                code="topic_overlap",
                field="topics_to_skip",
                message=(
                    f"{', '.join(repeated)} appears in both topics_to_cover and "
                    "topics_to_skip. A topic is either covered or deliberately left out."
                ),
            )
        )

    if len(report.topics_to_cover) > max_topics:
        issues.append(
            ReportIssue(
                code="too_many_topics",
                field="topics_to_cover",
                message=(
                    f"{len(report.topics_to_cover)} topics do not fit a "
                    f"{report.session_duration_minutes}-minute session. Keep at most "
                    f"{max_topics}, chosen for depth."
                ),
            )
        )

    if _comparable(report.interpreted_goal) == _comparable(report.original_task):
        issues.append(
            ReportIssue(
                code="goal_not_narrowed",
                field="interpreted_goal",
                message=(
                    "interpreted_goal restates the task instead of narrowing it to one "
                    "session-sized slice."
                ),
            )
        )

    return ReportValidation(ok=not issues, issues=issues)


class ValidateReportInput(BaseModel):
    """A finished report, plus the evidence it has to be true against."""

    model_config = ConfigDict(extra="forbid")

    report: FocusPreparationReport
    evidence_urls: list[str] = Field(default_factory=list, max_length=64)
    fetched_urls: list[str] = Field(default_factory=list, max_length=64)
    max_topics: int = Field(default=8, ge=2, le=8)
    research_performed: bool = True


class ValidateReportTool:
    """Check a finished report against the run's own evidence."""

    name = "validate_report"
    description = (
        "Check a finished focus preparation report against the sources the run "
        "actually gathered. Pipeline step; not offered to the model."
    )
    input_model = ValidateReportInput
    output_model = ReportValidation

    async def run(
        self, args: ValidateReportInput, ctx: RunContext
    ) -> ToolResult[ReportValidation]:
        """Always succeeds: a rejected report is `ok=True` carrying `ReportValidation.ok
        = False`. A failing `ToolResult` would mean the validator itself broke, and this
        one cannot — it reads two sets and a report and touches nothing else.

        `duration_ms` is left at 0; the registry times the call and stamps it centrally.
        """
        return ToolResult(
            ok=True,
            data=validate_report(
                args.report,
                evidence_urls=frozenset(args.evidence_urls),
                fetched_urls=frozenset(args.fetched_urls),
                max_topics=args.max_topics,
                research_performed=args.research_performed,
            ),
            duration_ms=0,
        )
