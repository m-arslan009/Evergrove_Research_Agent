"""The one entry point a run is started from (Day 3 S11).

The CLI calls this, and on Day 6 the MCP server calls the same function — which is the
whole reason it exists. Two surfaces that each assembled their own registry, providers and
budget would be two places for a run's composition to drift, and the second one always
drifts silently.

**Composition only.** Nothing here decides anything: no prompt, no budget arithmetic, no
retry, no error translation. `run_agent` already owns every one of those, and its
`registry` / `providers` / `ctx` parameters are optional *for this module's benefit* — the
loop takes them so composition can live outside it. If a change to this file would alter
what a run does rather than what it is built from, it belongs in `agents/single_agent.py`.

**Failures travel unchanged.** `PreparationFailed` and `LLMError` pass straight through.
A surface has to show a user why a run failed, and a layer that wrapped those in a third
exception type would leave `PreparationFailed.issues` behind at exactly the moment someone
needs it.

`RunState.validation_errors` is deliberately **not** surfaced here. The retry ladder writes
it on every corrected attempt, but `run_agent` returns a report rather than the state, and
widening that return type to carry a diagnostic would change the contract Day 6's MCP tool
is specified against. It is handed to Day 4 tracing, which is the layer that will see the
whole run rather than only its result.
"""

from __future__ import annotations

from evergrove_agent.agents import AgentProviders, run_agent
from evergrove_agent.config import Settings, get_settings
from evergrove_agent.schemas import FocusPreparationReport, TaskContext
from evergrove_agent.tools.base import RunBudget, RunContext
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tools.wiring import build_tool_registry


async def prepare_focus_session(
    task: TaskContext,
    *,
    settings: Settings | None = None,
    registry: ToolRegistry | None = None,
    providers: AgentProviders | None = None,
    ctx: RunContext | None = None,
) -> FocusPreparationReport:
    """Prepare one focus session with research: task in, validated report out.

    Every collaborator is optional and defaulted from `settings`, so a caller supplies only
    what it has a reason to control. The CLI passes a `ctx` because it displays the run's
    live ledger while the run is in flight; a test passes `providers` built on
    `FakeProvider` and a fixture-backed `registry`, which is what lets the whole path run
    offline with no patching.

    `settings` is resolved once and threaded into all three defaults. Letting each of them
    reach for `get_settings()` on its own would ignore a caller's override in two places out
    of three — and `--fully-local` is exactly such an override.

    Raises `PreparationFailed` when the run could not produce a valid report, and `LLMError`
    when the configured model could not be reached. Both are the caller's to render.
    """
    settings = settings or get_settings()
    return await run_agent(
        task,
        registry=registry if registry is not None else build_tool_registry(settings),
        providers=providers or AgentProviders.from_settings(settings),
        ctx=ctx or RunContext(budget=RunBudget.from_settings(settings)),
        settings=settings,
    )
