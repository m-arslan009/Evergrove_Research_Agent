"""The one entry point a run is started from (Day 3 S11).

The CLI calls this, and on Day 6 the MCP server calls the same function — which is the
whole reason it exists. Two surfaces that each assembled their own registry, providers and
budget would be two places for a run's composition to drift, and the second one always
drifts silently.

**Composition only.** Nothing here decides anything: no prompt, no budget arithmetic, no
retry, no error translation. Both loops already own every one of those, and their
`registry` / `providers` / `ctx` parameters are optional *for this module's benefit* — a
loop takes them so composition can live outside it. If a change to this file would alter
what a run does rather than what it is built from, it belongs in `agents/supervisor.py` or
`agents/single_agent.py`.

**`mode` is the one thing this module chooses** (Day 5 T1), and it is still composition:
`run_supervised` and `run_agent` are two finished loops with the same signature, and picking
between them assembles nothing and decides nothing inside either. Everything else on the way
in — the registry, the tracer, the providers, the budget — is built once and handed to
whichever one runs, so the two modes cannot drift in what they are allowed to do.

**This module owns the run's database handle** (Day 4 T2). It is the only layer that knows
when a run begins and ends, and both a trace and a shared cache connection need exactly
that lifetime — so the connection is opened here, handed to the `Tracer` and to the tools,
and closed here. Writing the run's header and its outcome is bookkeeping about the run, not
a decision inside it, which is why it does not contradict the paragraph above.

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

import logging
import sqlite3

from evergrove_agent.agents import AgentProviders, run_agent, run_supervised
from evergrove_agent.config import AgentMode, Settings, get_settings
from evergrove_agent.memory import db
from evergrove_agent.schemas import FocusPreparationReport, TaskContext
from evergrove_agent.tools.base import RunBudget, RunContext
from evergrove_agent.tools.registry import ToolRegistry
from evergrove_agent.tools.wiring import build_tool_registry
from evergrove_agent.tracing import RunStatus, Tracer

logger = logging.getLogger(__name__)


async def prepare_focus_session(
    task: TaskContext,
    *,
    mode: AgentMode = "multi",
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

    The run is traced: a `runs` row is written before the loop starts and closed out after
    it, and a registry built here is given both the connection and the `Tracer`, so its
    tool calls become spans under this run. A registry the **caller** supplied is used
    exactly as given and carries no tracer — a caller that assembled its own call path has
    said what it wants that path to be, and quietly attaching hooks to it would make the
    object behave differently from the one they built.

    The same `Tracer` is handed to the loop (Day 5 T6), which is what puts the agent
    boundaries in the trace and makes every tool call nest under the role that made it.
    It travels as an argument because `RunContext` carries identifiers and never I/O, and
    because this module is the only layer that knows when a run begins and ends — the same
    reason it already owns the connection. A loop given no tracer runs exactly as it did
    before, tools included.

    `mode` picks the reasoning topology and nothing else (Day 5 T1). `"multi"` is the
    Supervisor coordinating a Researcher and an Appraiser; `"single"` is the Day 3 loop
    running the same four stages as one agent. **Both are composed identically** — the same
    registry, tools, hooks, budget, memory, tracing, providers and validation are assembled
    above this line and handed to whichever loop runs, so a difference between the two modes
    can only ever be a difference in topology. Choosing between two already-built loops is
    composition, which is why it belongs here rather than inside either of them.

    Raises `PreparationFailed` when the run could not produce a valid report, and `LLMError`
    when the configured model could not be reached. Both are the caller's to render.
    """
    settings = settings or get_settings()
    ctx = ctx or RunContext(budget=RunBudget.from_settings(settings))

    connection = _open_trace_connection(settings)
    tracer = Tracer(connection, settings=settings) if connection is not None else None
    status: RunStatus = "failed"
    hops_used = 0
    try:
        if tracer is not None:
            tracer.start_run(ctx, task)
        run = run_supervised if mode == "multi" else run_agent
        report = await run(
            task,
            registry=(
                registry
                if registry is not None
                else build_tool_registry(settings, connection=connection, tracer=tracer)
            ),
            providers=providers or AgentProviders.from_settings(settings),
            ctx=ctx,
            settings=settings,
            tracer=tracer,
        )
    except Exception:
        # A run that stopped because it ran out of allowance is a different fact from one
        # that stopped because something broke, and the trace is the only place that
        # distinction survives — `PreparationFailed` carries the attempts, not the reason.
        status = "budget_exhausted" if ctx.budget.exhausted_limits else "failed"
        raise
    else:
        status, hops_used = "ok", report.hops_used
        return report
    finally:
        if tracer is not None:
            tracer.finish_run(ctx, status=status, hops_used=hops_used)
        if connection is not None:
            connection.close()


def _open_trace_connection(settings: Settings) -> sqlite3.Connection | None:
    """The run's own handle on the database, or `None` if it could not be opened.

    `connect` + `initialize_schema` rather than the `open_database` context manager,
    because the handle has to live for the whole run and be closed in the same `finally`
    that closes the run out.

    A failure here is logged and swallowed, and the run proceeds untraced with the tools
    opening their own connections per call, exactly as they did before this existed. The
    same stance `Tracer` takes over every individual write, for the same reason: a
    diagnostic that can end a fifteen-minute research run is worse than no diagnostic.
    `OSError` is caught beside `sqlite3.Error` because `connect` creates `DB_PATH`'s parent
    directory, and an unwritable one fails before SQLite is ever reached.
    """
    try:
        connection = db.connect(settings.db_path)
        db.initialize_schema(connection)
    except (sqlite3.Error, OSError) as exc:
        logger.warning(
            "could not open %s (%s: %s); this run will not be traced",
            settings.db_path,
            type(exc).__name__,
            exc,
        )
        return None
    return connection
