"""The reasoning layer: what decides, and how it reaches the tools.

`tool_calling.py` is the model-facing tool bridge (S2) and `prompt_context.py` fills the
prompt files' placeholders (S3). Above them sit the three roles Day 5 T1 split out of
`single_agent.py` — `supervisor.py` decides and writes the report, `researcher.py` gathers,
`appraiser.py` judges — with `runtime.py` holding what more than one of them needs. It was a
file move rather than a rewrite because each stage already took and returned a model from
`schemas/agents.py`.

**Two topologies over one set of parts.** `supervisor.run_supervised` is the multi-agent
path and the default; `single_agent.run_agent` runs the same four stages as one agent and
remains a required demonstration. `service.prepare_focus_session(mode=...)` chooses.

Modules inside this package import each other **by module path, never through this file** —
`runtime` → the three roles → `single_agent` → here. Importing `evergrove_agent.agents` from
within `agents/` would make the package re-enter itself.
"""

from __future__ import annotations

from evergrove_agent.agents.appraiser import judge_sufficiency
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
from evergrove_agent.agents.researcher import run_research_step
from evergrove_agent.agents.runtime import (
    AgentProviders,
    PreparationFailed,
    StopReason,
)
from evergrove_agent.agents.single_agent import run_agent
from evergrove_agent.agents.supervisor import decide_next_step, finalise, run_supervised
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
    "run_supervised",
    "to_tool_spec",
]
