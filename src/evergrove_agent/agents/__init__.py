"""The reasoning layer: what decides, and how it reaches the tools.

Thin on purpose. Today it holds the model-facing tool bridge (S2) and the renderers that
fill the prompt files' placeholders (S3); `single_agent.py` and its four
separately-prompted functions arrive in S5, and Day 5 splits those into `supervisor.py`,
`researcher.py` and `appraiser.py` beside them.
"""

from __future__ import annotations

from evergrove_agent.agents.prompt_context import (
    render_allowance,
    render_already_covered,
    render_attachment,
    render_available_tools,
    render_progress,
    render_research_context,
    render_sources,
    render_tool_outcome,
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
    "ToolCallOutcome",
    "advertise",
    "advertised_tool_names",
    "dispatch",
    "dispatch_all",
    "render_allowance",
    "render_already_covered",
    "render_attachment",
    "render_available_tools",
    "render_progress",
    "render_research_context",
    "render_sources",
    "render_tool_outcome",
    "to_tool_spec",
]
