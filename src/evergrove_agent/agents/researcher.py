"""The Researcher: gather evidence for one question, and nothing else (Day 5 T1).

Moved verbatim from `single_agent.py`'s stage 2 (Day 3 S6). The only worker that acts —
`_ROLE_TOOLS` in `tool_calling.py` gives `researcher` the only non-empty tool menu, and every
call it makes goes through `dispatch` into `ToolRegistry.call`, which is what keeps the Day 4
hooks, spans, budget claims and JSON log line intact. **No tool is re-implemented here**;
searching, fetching and reading are Day 2's and stay Day 2's.

**What this role may not do.** It does not decide whether the run is finished, it does not
judge whether the evidence is enough, and it never writes a report. It answers one
`ResearchAssignment` with one `ResearchFindings` and stops. Those two Pydantic models are the
entire interface, which is what lets the Supervisor coordinate it without either side knowing
how the other works.

**It never talks to the Appraiser.** This module imports `runtime` and downward, never
`appraiser` and never `supervisor`. Coordination belongs to the Supervisor, and an import
here would be the first step to a worker that routes around it.
"""

from __future__ import annotations

from pydantic import ValidationError

from evergrove_agent.agents.prompt_context import (
    render_allowance,
    render_already_covered,
    render_attachment,
    render_available_tools,
    render_tool_outcome,
    render_turn_state,
)
from evergrove_agent.agents.runtime import _claim_reasoning_call, _RESEARCH_RESERVE
from evergrove_agent.agents.tool_calling import (
    ToolCallOutcome,
    advertise,
    advertised_tool_names,
    dispatch,
)
from evergrove_agent.config import Settings, get_settings
from evergrove_agent.llm import LLMError, LLMProvider, Message
from evergrove_agent.llm.base import ToolCall
from evergrove_agent.llm.prompts import render_prompt
from evergrove_agent.schemas import (
    GatheredSource,
    ResearchAction,
    ResearchAssignment,
    ResearchFindings,
    ToolFailure,
)
from evergrove_agent.search.normalize import canonicalize_url
from evergrove_agent.tools.base import RunContext
from evergrove_agent.tools.fetch_url import FetchUrlOutput
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tools.web_search import WebSearchOutput

_MAX_RESEARCH_TURNS = 3
"""Model turns one hop may take. Turn 1 finds candidates, turn 2 opens the ones search
actually returned, turn 3 recovers from a failure. A single turn is not enough — the model
would have to guess a URL before seeing any search result, which is the one thing
`research_step.md` forbids it to do."""


async def run_research_step(
    assignment: ResearchAssignment,
    *,
    provider: LLMProvider,
    registry: ToolRegistry,
    ctx: RunContext,
    settings: Settings | None = None,
    attachment_path: str | None = None,
) -> ResearchFindings:
    """Search, read and collect for one question. The Researcher's whole job.

    Up to `_MAX_RESEARCH_TURNS` model turns, because one is not enough: the model has to see
    what search returned before it can open anything. Each turn's results go back as an
    observation built from `render_tool_outcome`, which carries the error code, the message
    and whether a retry could help — everything `research_step.md`'s recovery instruction
    tells the model to act on.

    **A turn is one constrained `ResearchAction`, not free-form tool calling** — Day 3's
    contingency option (2), spent during S14 on measurement rather than on a reliability
    failure: free-form calls were *correct* on `qwen3:4b` but cost 361 s a turn against 46 s
    for a constrained call, because nothing stopped the model prefixing ~4 000 characters of
    reasoning. `dispatch` still receives a `ToolCall` and arguments still travel as a raw
    mapping, so this is the same tool path with a cheaper decision in front of it.

    The hop ends early on any of: an empty `tool` (the model saying it has enough), a reply
    that does not fit the schema, a refused budget claim, or an unreachable model.

    **Always returns findings, never raises.** A hop that gathered nothing is still a fact
    about the run, and the failures it collected are what stop the report reading like a
    confident one. Calls are dispatched one at a time, never batched: a budget claim has to
    sit between one call and the next, and they stay strictly in the order the model asked
    for them.
    """
    settings = settings or get_settings()
    budget = ctx.budget
    has_attachment = attachment_path is not None
    specs = advertise(registry, "researcher", has_attachment=has_attachment)
    allowed = advertised_tool_names("researcher", has_attachment=has_attachment)

    messages = [
        Message(
            role="user",
            content=render_prompt(
                "research_step",
                research_question=assignment.research_question,
                session_minutes=assignment.session_minutes,
                source_preference=assignment.source_preference,
                available_tools=render_available_tools(specs),
                allowance=render_allowance(assignment),
                already_covered=render_already_covered(assignment),
                attachment=render_attachment(attachment_path),
            ),
        )
    ]

    sources: dict[str, GatheredSource] = {}
    queries: list[str] = []
    failures: list[ToolFailure] = []
    notes = ""

    for _ in range(_MAX_RESEARCH_TURNS):
        if not _claim_reasoning_call(budget, reserve=_RESEARCH_RESERVE):
            break
        try:
            response = await provider.generate(
                messages, schema=ResearchAction, temperature=settings.temperature
            )
        except LLMError as exc:
            # Not fatal here, unlike every other stage: this hop already holds evidence
            # worth keeping, and throwing it away would make the report thinner than the
            # run actually was. If the provider is genuinely down, `finalise` says so.
            notes = notes or f"The research step stopped early: {exc}"
            break

        try:
            action = ResearchAction.model_validate_json(response.text)
        except ValidationError:
            # Drift ends the hop, exactly as prose instead of a tool call used to. There is
            # no re-ask here on purpose: `_decode`'s retry exists for stages whose answer
            # steers control flow, while a hop that stops early still returns its findings.
            break

        if action.reasoning.strip():
            notes = action.reasoning.strip()
        if not action.tool.strip():
            break

        # One call per turn. `dispatch` still receives a `ToolCall` and the arguments still
        # travel as the raw mapping they arrived in, so the registry remains the only
        # argument validator and this is not a second tool path.
        calls = [ToolCall(name=action.tool.strip(), arguments=dict(action.arguments))]

        outcomes: list[ToolCallOutcome] = []
        for call in calls:
            # The registry's pre-hook claims the counter and refuses the call when the run
            # cannot afford it (Day 4 T3), so the loop no longer pays for tools itself.
            result = await dispatch(call, registry=registry, ctx=ctx, allowed=allowed)
            outcome = ToolCallOutcome(call=call, result=result)
            outcomes.append(outcome)
            _absorb(outcome, assignment, sources, queries, failures, settings)

        # The observation carries the hop's *current* state, not the opening state the
        # prompt was rendered with. Without this the model re-asks the question it just
        # asked: S14 measured three identical `web_search` calls in one hop, which the
        # cache answered for free while still spending all three search claims and leaving
        # `fetch_url` untouched.
        observation = "\n\n".join(
            render_tool_outcome(outcome, settings=settings) for outcome in outcomes
        )
        messages = [
            *messages,
            Message(role="assistant", content=response.text or _ASKED_FOR_TOOLS),
            Message(
                role="user",
                content=observation
                + "\n\n"
                + render_turn_state(
                    searches_left=budget.remaining("search"),
                    fetches_left=budget.remaining("fetch"),
                    queries=queries,
                ),
            ),
        ]

    return ResearchFindings(
        research_question=assignment.research_question,
        hop=assignment.hop,
        queries_used=queries[:32],
        sources=list(sources.values())[:64],
        failures=failures[:16],
        notes=notes[:2000],
    )


_ASKED_FOR_TOOLS = "(requested tools)"
"""Stands in for an empty assistant turn. A model that answers with tool calls and no prose
still has to appear in the transcript, or its own request goes missing from the context it
is given next."""


def _absorb(
    outcome: ToolCallOutcome,
    assignment: ResearchAssignment,
    sources: dict[str, GatheredSource],
    queries: list[str],
    failures: list[ToolFailure],
    settings: Settings,
) -> None:
    """Fold one tool outcome into the hop's evidence.

    Owns the `NormalizedSource` (+ `FetchUrlOutput`) → `GatheredSource` conversion that
    `schemas/` cannot do for itself — `search/normalize.py` imports `schemas`, so the reverse
    would be a cycle.

    Keyed on the canonical URL throughout, because that is the string
    `RunState.evidence_urls` is built from and the string S9's grounding check compares
    against. A search hit and a later fetch of the same page must land on one entry, or the
    run reports two sources where it read one.
    """
    result = outcome.result
    name = outcome.call.name

    if result.error is not None:
        failures.append(ToolFailure(tool_name=name, error=result.error))
        return

    if name == "web_search" and isinstance(result.data, WebSearchOutput):
        query = str(outcome.call.arguments.get("query", "")).strip()
        if query and query not in queries:
            queries.append(query)
        for hit in result.data.results:
            url = canonicalize_url(hit.url)
            # A lead an earlier hop already gathered is not new evidence; it is already in
            # `RunState`, and re-listing it would inflate `sources_examined`.
            if url is None or url in sources or url in assignment.avoid_urls:
                continue
            sources[url] = GatheredSource(
                url=url,
                title=hit.title[:_TITLE_CHARS],
                domain_class=hit.domain_class,
                snippet=hit.snippet[:_SNIPPET_CHARS],
            )
        return

    # `read_document` is absent on purpose. It reads the user's own attachment, which is not
    # a source anyone can cite — it has no URL and nothing to ground against. Its content
    # still reaches the model, through the observation `render_tool_outcome` builds.
    if name == "fetch_url" and isinstance(result.data, FetchUrlOutput):
        url = canonicalize_url(result.data.url)
        if url is None:
            return
        lead = sources.get(url)
        # A page the model opened without search having offered it is still evidence — it
        # was genuinely read. It keeps `domain_class="unknown"`, because no classified
        # search hit vouched for it and we do not upgrade authority on our own.
        sources[url] = GatheredSource(
            url=url,
            title=(result.data.title or (lead.title if lead else ""))[:_TITLE_CHARS],
            domain_class=lead.domain_class if lead else "unknown",
            snippet=(lead.snippet if lead else "")[:_SNIPPET_CHARS],
            excerpt=result.data.text[: settings.source_excerpt_chars],
            retrieved_at=result.data.retrieved_at,
        )


_TITLE_CHARS = 500
_SNIPPET_CHARS = 2000
"""`GatheredSource`'s own ceilings, applied where a source is built.

`NormalizedSource` and `FetchUrlOutput` bound neither field, so a long page title would
otherwise raise inside a step this module promises never to raise from — and losing a whole
hop's evidence to an over-long title is the worst possible trade.
"""
