"""The reasoning layer: what decides, and how it reaches the tools.

Thin on purpose. Today it holds the model-facing tool bridge (S2); `single_agent.py` and
its four separately-prompted functions arrive in S5, and Day 5 splits those into
`supervisor.py`, `researcher.py` and `appraiser.py` beside it.
"""

from __future__ import annotations

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
    "to_tool_spec",
]
