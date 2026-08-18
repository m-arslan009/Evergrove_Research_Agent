"""The Appraiser: read the evidence and say whether it is enough (Day 5 T1).

Moved verbatim from `single_agent.py`'s stage 3 (Day 3 S7). One `AppraisalRequest` in, one
`AppraisalVerdict` out.

**What this role may not do.** It performs no research — `_ROLE_TOOLS` gives `appraiser` an
empty tool menu, so this module needs neither the registry nor `dispatch`, and its absence
from the imports below is the structural form of that rule. It also never writes a report:
finalising is the Supervisor's stage, and a judge that could produce the deliverable would be
judging its own work.

**It never talks to the Researcher.** This module imports `runtime` and downward, never
`researcher` and never `supervisor`. The Supervisor hands it the evidence and reads the
verdict; the two workers have no channel between them.
"""

from __future__ import annotations

from evergrove_agent.agents.prompt_context import render_sources
from evergrove_agent.agents.runtime import _decode, _FINALISE_RESERVE
from evergrove_agent.config import Settings, get_settings
from evergrove_agent.llm import LLMProvider
from evergrove_agent.llm.prompts import render_prompt
from evergrove_agent.schemas import AppraisalRequest, AppraisalVerdict
from evergrove_agent.tools.base import RunContext


async def judge_sufficiency(
    request: AppraisalRequest,
    *,
    provider: LLMProvider,
    ctx: RunContext,
    settings: Settings | None = None,
) -> AppraisalVerdict | None:
    """Do these sources support a useful session? The Appraiser's whole job.

    The one stage that must actually read the evidence, so it is handed `render_sources` —
    full excerpts at `SOURCE_EXCERPT_CHARS`, not the compact block `finalise.md` gets.

    It is told nothing about the budget or the hop count, deliberately: a judge that knows a
    follow-up is impossible stops asking for one, which would hide the real verdict instead
    of changing it. Whether a follow-up can be afforded is the Supervisor's decision, taken
    after this returns.

    `None` when the budget refused or the model drifted twice; the caller finalises with the
    evidence it has rather than assuming a verdict nobody gave.
    """
    settings = settings or get_settings()
    prompt = render_prompt(
        "sufficiency",
        research_question=request.research_question,
        session_minutes=request.session_minutes,
        sources=render_sources(request.sources, settings=settings),
    )
    verdict = await _decode(
        provider,
        prompt,
        AppraisalVerdict,
        budget=ctx.budget,
        settings=settings,
        reserve=_FINALISE_RESERVE,
    )
    return verdict if isinstance(verdict, AppraisalVerdict) else None
