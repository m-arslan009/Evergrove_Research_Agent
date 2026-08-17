"""Turning a run's own state into the text its prompts carry (Day 3 S3).

`llm/prompts/` holds the wording and knows only about files and `str.format`. This module
holds everything that wording leaves as a placeholder, and it is the only place that
decides how the run's vocabulary — `RunState`, `GatheredSource`, `ResearchAssignment`, a
`ToolResult` — is spelled out for a model to read.

That split is why it lives in `agents/` beside `tool_calling.py` rather than beside the
loader: these functions import `schemas/` and `config`, and `llm/` imports neither today.
Keeping the loader free of both is what stops a prompt file dragging the model layer into
the agent's domain.

**One function per placeholder**, so no caller ever improvises a block of prompt text.
Nothing here calls a model, a tool, the network or the database, and nothing here decides
anything — what a stage *does* with the text is the loop's business (S5-S10).

Two rules the whole file obeys.

**Every block is bounded.** `NUM_CTX` is 4096 tokens, and the largest thing this project
ever sends a small model is a finalise prompt carrying evidence, a JSON schema and a
report at once. `SOURCE_EXCERPT_CHARS` is the ceiling on how much of a source a model
ever sees, and it is applied here as the last step before the text leaves — even though
S6 already bounds `GatheredSource.excerpt` when it builds one, because the ceiling should
hold whatever built the source.

**Read and unread sources never look alike.** `retrieved_at is None` means discovered but
never opened. The authority rule in `finalise.md` and the grounding check in S9 both rest
on that distinction surviving the trip into a prompt, so it is spelled out in words on
every source rather than left to be inferred from a missing field.
"""

from __future__ import annotations

from collections.abc import Sequence

from evergrove_agent.agents.tool_calling import ToolCallOutcome
from evergrove_agent.config import Settings, get_settings
from evergrove_agent.llm.base import ToolSpec
from evergrove_agent.schemas import (
    GatheredSource,
    ResearchAssignment,
    RunState,
    ToolFailure,
)

_TRUNCATION_MARK = " […truncated]"


# --- plan.md -------------------------------------------------------------------------


def render_progress(state: RunState, *, hops_remaining: int) -> str:
    """`plan.md`'s `progress` — what this run has spent, found and been told.

    The planner has no tools and no memory of its previous turn, so this block is the
    whole of what stops hop 2 repeating hop 1: the queries already spent, the sources
    already gathered, and the judgement that sent us round again. `used_queries` and
    `all_sources` come from `RunState`'s derived properties rather than being recomputed,
    because "what has this run seen" has exactly one definition (S1).
    """
    if not state.findings:
        return (
            "No research has been done yet — this is the first decision of the run.\n"
            f"Research hops still available: {hops_remaining}."
        )

    lines = [
        f"Research hops completed: {state.hop}. Still available: {hops_remaining}.",
        "",
        "Questions and queries already spent — never repeat one:",
    ]
    lines += _bullets(
        [finding.research_question for finding in state.findings]
        + list(state.used_queries)
    )

    lines += ["", "Sources gathered so far:"]
    lines += _bullets(
        [
            f"{_status(source)} — {source.title or source.url} ({source.domain_class})"
            for source in state.all_sources
        ]
        or ["none"]
    )

    verdict = state.verdict
    if verdict is not None:
        lines += [
            "",
            "The last judgement: "
            + ("enough to plan a session." if verdict.sufficient else "not enough yet."),
        ]
        if verdict.missing_information:
            lines += ["Reported missing:"] + _bullets(verdict.missing_information)
        if verdict.requested_followup:
            lines += [f"Suggested follow-up: {verdict.requested_followup}"]

    return "\n".join(lines)


# --- research_step.md ------------------------------------------------------------------


def render_available_tools(specs: Sequence[ToolSpec]) -> str:
    """`research_step.md`'s `available_tools` — the menu, by name, in menu order.

    Takes the specs `advertise()` produced rather than a role, so the list the prompt
    names and the list the provider was handed cannot describe different menus. Names
    only: the descriptions and the argument schemas already travel with the specs, and a
    second copy in prose is a second tool-selection mechanism that would drift from the
    allow-list `dispatch` enforces.
    """
    if not specs:
        return "No tools are available at this step."
    return "\n".join(spec.name for spec in specs)


def render_allowance(assignment: ResearchAssignment) -> str:
    """`research_step.md`'s `allowance` — what this hop may still spend.

    The *allowance*, not the ledger: the live counters are `RunContext`'s (S4). A model
    told what it has left stops asking for a fifth search it cannot have, which saves the
    turn a refusal would cost.
    """
    return (
        "At most "
        f"{_count(assignment.max_searches, 'search', 'searches')} and "
        f"{_count(assignment.max_fetches, 'page read', 'page reads')} in this step."
    )


def render_already_covered(assignment: ResearchAssignment) -> str:
    """`research_step.md`'s `already_covered` — what earlier hops already spent.

    This is what makes a second hop a genuinely new query rather than a repeat, and it is
    also quota: an identical live query re-run is a SerpAPI call spent on a result the
    cache already held.
    """
    if not assignment.avoid_queries and not assignment.avoid_urls:
        return "Nothing has been searched or read yet in this run."

    lines: list[str] = []
    if assignment.avoid_queries:
        lines += ["Queries already used — do not repeat them:"]
        lines += _bullets(assignment.avoid_queries)
    if assignment.avoid_urls:
        if lines:
            lines += [""]
        lines += ["Pages already read — do not open them again:"]
        lines += _bullets(assignment.avoid_urls)
    return "\n".join(lines)


def render_attachment(attachment_path: str | None) -> str:
    """`research_step.md`'s `attachment` — the user's own document, when there is one.

    Empty when there is none, so the prompt carries no sentence about a file that does
    not exist. `read_document` is advertised on exactly the same condition (S2), which is
    why this is a separate placeholder rather than a line of the task block: the two must
    agree, and the loop sets both from one fact.
    """
    if not attachment_path:
        return ""
    return (
        f"The user attached a document: {attachment_path}\n"
        "Read it before searching the web — it is what they actually want to work from."
    )


def render_tool_outcome(
    outcome: ToolCallOutcome, *, settings: Settings | None = None
) -> str:
    """One tool call and what came back, as the researcher's next turn should read it.

    A failure is rendered as a fact with its code, its message and whether retrying could
    help — everything `research_step.md` tells the model to act on. That is the whole
    point of the tool envelope: a model that can read the error recovers, and a model
    that is only told "that did not work" invents a replacement.

    A success is the output model's own JSON, clipped to `SOURCE_EXCERPT_CHARS`, because
    a single `fetch_url` reply can legitimately be 20,000 characters and the context
    window is 4096 tokens. The clip is safe: this text is read, never parsed — S6 builds
    its `GatheredSource` from the typed result, not from this string.

    Whether an outcome is shown to the model at all is S6's decision, not this function's.
    """
    limit = (settings or get_settings()).source_excerpt_chars
    name = outcome.call.name
    result = outcome.result

    error = result.error
    if error is not None:
        retry = "retrying may help" if error.retryable else "retrying will not help"
        return f"{name} failed: {error.code.value} — {error.message} ({retry})."

    if result.data is None:
        return f"{name} returned nothing."
    return f"{name} returned:\n{_clip(result.data.model_dump_json(), limit)}"


# --- sufficiency.md --------------------------------------------------------------------


def render_sources(
    sources: Sequence[GatheredSource], *, settings: Settings | None = None
) -> str:
    """`sufficiency.md`'s `sources` — the evidence, in full, for the one stage that judges it.

    The appraiser is the only stage that must actually read what was gathered, so this is
    the block that carries excerpts at their full `SOURCE_EXCERPT_CHARS` ceiling.
    `finalise.md` gets `render_research_context` instead, which is deliberately compact:
    both of them plus a JSON schema do not fit in one 4096-token window.
    """
    if not sources:
        return "No sources were gathered."

    limit = (settings or get_settings()).source_excerpt_chars
    blocks = [
        _source_block(source, body=_evidence(source, limit))
        for source in sources
    ]
    return "\n\n".join(blocks)


# --- finalise.md -----------------------------------------------------------------------


def max_topics_for(minutes: int) -> int:
    """`finalise.md`'s `max_topics` — the session-sizing rule from plan section 17.

    `max(3, minutes // 5)`, capped at 8. An int rather than a block of text, but it is a
    placeholder on the same prompt and it fills the same role as everything else here:
    the one place a fact about the run becomes something the prompt carries. It lives
    here rather than beside a caller because both `main.py`'s `--no-research` path and
    the loop's `finalise()` render that prompt, and a second copy of a sizing rule drifts
    into two different sessions from the same number of minutes.
    """
    return min(8, max(3, minutes // 5))


def render_research_context(
    state: RunState, *, settings: Settings | None = None
) -> str:
    """`finalise.md`'s `research_context` — everything the report may rest on, compactly.

    Compact on purpose. A researched finalise call sends this block, the prompt, the
    `FocusPreparationReport` JSON schema and the generated report through one 4096-token
    window, so each source contributes its identity — title, URL, authority, read or not
    — and its search snippet rather than its full excerpt. The reading was already done:
    each hop's `notes` is the researcher's own summary of it, which is what
    `schemas/agents.py` describes that field as being for.

    What is *missing* travels too, and that is the load-bearing half. A run whose search
    was unavailable, or whose evidence the appraiser judged thin, has to reach `unknowns`
    as a stated gap — otherwise a degraded run produces a report that reads exactly like
    a confident one.
    """
    limit = (settings or get_settings()).source_excerpt_chars
    sources = state.all_sources

    lines: list[str] = []
    if sources:
        lines += ["Sources found in this run:", ""]
        lines += [
            "\n\n".join(
                _source_block(source, body=_snippet(source, limit))
                for source in sources
            )
        ]
    else:
        lines += ["This run gathered no sources."]

    notes = [
        f"hop {finding.hop}: {_clip(finding.notes, limit)}"
        for finding in state.findings
        if finding.notes.strip()
    ]
    if notes:
        lines += ["", "What the researcher noted:"] + _bullets(notes)

    verdict = state.verdict
    if verdict is not None and verdict.missing_information:
        lines += ["", "Still missing after the research:"]
        lines += _bullets(verdict.missing_information)

    failures = [
        failure for finding in state.findings for failure in finding.failures
    ]
    if failures:
        lines += ["", "Tools that failed during this run:"]
        lines += _bullets([_failure_line(failure) for failure in failures])

    return "\n".join(lines)


_STOP_CAUSES: dict[str, str] = {
    "hop_cap": "the research hop limit was reached",
    "budget_spent": "no search or page-read budget was left",
    "no_followup": (
        "the evidence was judged incomplete and no specific follow-up question "
        "would have helped"
    ),
    "planner_unavailable": "the planning step could not produce a usable decision",
    "appraiser_unavailable": "the sufficiency judgement could not be completed",
}
"""Why a run stopped, for the stops that mean the research was cut short.

`sufficient` and `planner_finalised` are absent on purpose: those runs stopped because
they were done, and announcing a reason for them would make a healthy run read like a
degraded one.
"""

_LIMIT_NAMES: dict[str, str] = {
    "search": "search calls",
    "fetch": "page reads",
    "model_call": "model calls",
    "time": "time",
}
"""`RunBudget.exhausted_limits`' identifiers as a reader's words.

The ledger returns identifiers precisely so that this translation lives here — S4's
contract keeps prompt wording out of the budget, and this is the other half of it.
"""


def render_stop_reason(reason: str, *, exhausted: Sequence[str] = ()) -> str:
    """Why the research ended, in one sentence — or nothing, when there is nothing to say.

    Two callers, one wording. `finalise()` sends this as an extra `Message`, because
    `finalise.md`'s five placeholders are frozen and a sixth would break the
    `--no-research` path; it also appends the same sentence to the report's `unknowns`,
    because a model may ignore the message and a degraded run must never read like a
    confident one.

    An empty string is the signal that neither is needed: the run stopped because it was
    finished and spent no limit doing it. That is the same "nothing to say means say
    nothing" shape `render_attachment` uses.
    """
    cause = _STOP_CAUSES.get(reason)
    limits = [_LIMIT_NAMES.get(name, name) for name in exhausted]
    if cause is None and not limits:
        return ""

    sentence = (
        f"The research stopped before it was judged complete because {cause}"
        if cause
        else "The research finished"
    )
    if limits:
        sentence += f", and this run ran out of {_join(limits)}"
    return sentence + "."


# --- shared shaping ---------------------------------------------------------------------


def _source_block(source: GatheredSource, *, body: str) -> str:
    """One source, identically shaped for every prompt that shows one.

    Same field order and same words in both blocks, so a model that learned to read the
    evidence in `sufficiency.md` reads the citations in `finalise.md` the same way.
    """
    lines = [
        source.title or "(untitled)",
        f"url: {source.url}",
        f"authority: {source.domain_class}",
        f"status: {_status(source)}",
    ]
    if body:
        lines.append(body)
    return "\n".join(lines)


def _status(source: GatheredSource) -> str:
    """The read/unread distinction, in the words both prompts use for it."""
    return "read" if source.was_read else "found but not opened"


def _evidence(source: GatheredSource, limit: int) -> str:
    """What a source actually says — its excerpt, or the snippet if it was never opened."""
    if source.excerpt.strip():
        return f"text: {_clip(source.excerpt, limit)}"
    return _snippet(source, limit)


def _snippet(source: GatheredSource, limit: int) -> str:
    """The search result's own claim about a page. Not evidence — it was not read."""
    if not source.snippet.strip():
        return ""
    return f"snippet: {_clip(source.snippet, limit)}"


def _failure_line(failure: ToolFailure) -> str:
    return f"{failure.tool_name}: {failure.error.code.value} — {failure.error.message}"


def _bullets(items: Sequence[str]) -> list[str]:
    return [f"- {item}" for item in items]


def _count(value: int, singular: str, plural: str) -> str:
    return f"{value} {singular if value == 1 else plural}"


def _join(items: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c` — a list read aloud rather than punctuated."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _clip(text: str, limit: int) -> str:
    """Bound one field at the last step before it reaches a model.

    Marked rather than silent: a model that can see it was given a fragment asks for the
    rest or says so in `unknowns`, where one that cannot treats a sentence cut mid-word
    as the whole story.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + _TRUNCATION_MARK
