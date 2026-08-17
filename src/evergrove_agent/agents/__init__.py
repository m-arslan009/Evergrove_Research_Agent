"""The reasoning layer: what decides, and how it reaches the tools.

Three files. `tool_calling.py` is the model-facing tool bridge (S2), `prompt_context.py`
fills the prompt files' placeholders (S3), and `single_agent.py` is the loop that drives
both (S5-S8). Day 5 splits `single_agent.py`'s four stage functions into `supervisor.py`,
`researcher.py` and `appraiser.py` beside them — a file move, because each already takes and
returns a model from `schemas/agents.py`.
"""

from __future__ import annotations

from evergrove_agent.agents.prompt_context import (
    max_topics_for,
    render_allowance,
    render_already_covered,
    render_attachment,
    render_available_tools,
    render_continuation_note,
    render_previous_preparation,
    render_progress,
    render_research_context,
    render_sources,
    render_stop_reason,
    render_tool_outcome,
    render_turn_state,
)
from evergrove_agent.agents.single_agent import (
    AgentProviders,
    PreparationFailed,
    StopReason,
    decide_next_step,
    finalise,
    judge_sufficiency,
    run_agent,
    run_research_step,
)
from evergrove_agent.agents.tool_calling import (
    ToolCallOutcome,
    advertise,
    advertised_tool_names,
    dispatch,
    dispatch_all,
    to_tool_spec,
)

__all__ = [
    "AgentProviders",
    "PreparationFailed",
    "StopReason",
    "ToolCallOutcome",
    "advertise",
    "advertised_tool_names",
    "decide_next_step",
    "dispatch",
    "dispatch_all",
    "finalise",
    "judge_sufficiency",
    "max_topics_for",
    "render_allowance",
    "render_already_covered",
    "render_attachment",
    "render_available_tools",
    "render_continuation_note",
    "render_previous_preparation",
    "render_progress",
    "render_research_context",
    "render_sources",
    "render_stop_reason",
    "render_tool_outcome",
    "render_turn_state",
    "run_agent",
    "run_research_step",
    "to_tool_spec",
]
