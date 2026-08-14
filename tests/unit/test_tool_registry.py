"""The registry is the only path to a tool, so its guarantees get tested before any tool exists.

Two of them matter most and are asserted from every angle below: `call` never raises, and
every outcome — unknown name, bad arguments, a broken tool — comes back as a `ToolResult`
the agent can read. Everything here is offline: no model, no network, no database.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from evergrove_agent.schemas import ErrorCode, ToolError, ToolResult
from evergrove_agent.tools import RunContext, ToolInvocation, ToolRegistry


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    times: int = 1


class EchoOutput(BaseModel):
    echoed: str


class EchoTool:
    """A well-behaved tool: records what it was handed, then succeeds."""

    name = "echo"
    description = "Repeat the given text."
    input_model = EchoInput
    output_model = EchoOutput

    def __init__(self) -> None:
        self.calls: list[tuple[EchoInput, RunContext]] = []

    async def run(self, args: EchoInput, ctx: RunContext) -> ToolResult[EchoOutput]:
        self.calls.append((args, ctx))
        return ToolResult(
            ok=True, data=EchoOutput(echoed=args.text * args.times), duration_ms=0
        )


class PingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PingTool:
    """Takes no arguments — proves `args=None` is a valid way to call."""

    name = "ping"
    description = "Answer with pong."
    input_model = PingInput
    output_model = EchoOutput

    async def run(self, args: PingInput, ctx: RunContext) -> ToolResult[EchoOutput]:
        return ToolResult(ok=True, data=EchoOutput(echoed="pong"), duration_ms=0)


class FailingTool:
    """Fails the way the contract requires: a returned error, never an exception."""

    name = "failing"
    description = "Always reports a failure."
    input_model = PingInput
    output_model = EchoOutput

    async def run(self, args: PingInput, ctx: RunContext) -> ToolResult[Any]:
        return ToolResult(
            ok=False,
            error=ToolError(
                code=ErrorCode.SEARCH_UNAVAILABLE,
                message="the backend is unreachable",
                retryable=True,
            ),
            duration_ms=0,
        )


class RaisingTool:
    """Breaks the contract by raising — the registry has to absorb it."""

    name = "raising"
    description = "Raises instead of returning."
    input_model = PingInput
    output_model = EchoOutput

    async def run(self, args: PingInput, ctx: RunContext) -> ToolResult[Any]:
        raise RuntimeError("disk on fire")


class WrongReturnTool:
    """Breaks the contract by returning something that is not a ToolResult."""

    name = "wrong_return"
    description = "Returns the wrong type."
    input_model = PingInput
    output_model = EchoOutput

    async def run(self, args: PingInput, ctx: RunContext) -> Any:
        return {"ok": True}


class SlowClaimTool:
    """Reports an absurd duration, so the registry's own measurement is visible."""

    name = "slow_claim"
    description = "Claims to have taken a very long time."
    input_model = PingInput
    output_model = EchoOutput

    async def run(self, args: PingInput, ctx: RunContext) -> ToolResult[EchoOutput]:
        return ToolResult(ok=True, data=EchoOutput(echoed="x"), duration_ms=987_654)


@pytest.fixture
def ctx() -> RunContext:
    return RunContext(run_id="run_test01")


@pytest.fixture
def registry() -> ToolRegistry:
    """An empty registry — no tools and, per Day 2, no hooks yet."""
    return ToolRegistry()


def make_pre_hook(log: list[str], label: str, short_circuit: ToolResult[Any] | None = None):
    """A pre-hook that records that it ran, and optionally short-circuits the call."""

    async def hook(invocation: ToolInvocation) -> ToolResult[Any] | None:
        log.append(label)
        return short_circuit

    return hook


def make_post_hook(log: list[str], label: str, replacement: ToolResult[Any] | None = None):
    """A post-hook that records that it ran, and optionally replaces the result."""

    async def hook(
        invocation: ToolInvocation, result: ToolResult[Any]
    ) -> ToolResult[Any] | None:
        log.append(label)
        return replacement

    return hook


# --- registration ------------------------------------------------------------------------


def test_registers_and_looks_up_a_tool_by_name(registry: ToolRegistry) -> None:
    tool = EchoTool()

    registry.register(tool)

    assert registry.get("echo") is tool
    assert registry.names == ("echo",)
    assert "echo" in registry
    assert len(registry) == 1


def test_lookup_of_an_unregistered_name_returns_none(registry: ToolRegistry) -> None:
    assert registry.get("echo") is None
    assert registry.names == ()


def test_a_duplicate_name_is_refused_and_the_first_tool_survives(
    registry: ToolRegistry,
) -> None:
    first = EchoTool()
    registry.register(first)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())

    assert registry.get("echo") is first


def test_registering_something_without_a_name_is_refused(registry: ToolRegistry) -> None:
    class Nameless:
        name = "   "
        description = "no usable name"
        input_model = PingInput
        output_model = EchoOutput

        async def run(self, args: PingInput, ctx: RunContext) -> ToolResult[Any]:
            raise AssertionError("never reached")

    with pytest.raises(ValueError):
        registry.register(Nameless())


def test_a_new_registry_starts_with_no_hooks(registry: ToolRegistry) -> None:
    """Day 4 fills these lists; Day 2 only guarantees they exist and are empty."""
    assert registry._pre_hooks == []
    assert registry._post_hooks == []


# --- successful dispatch -------------------------------------------------------------------


async def test_dispatches_to_the_registered_tool(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    tool = EchoTool()
    registry.register(tool)

    result = await registry.call("echo", EchoInput(text="ab", times=2), ctx)

    assert result.ok
    assert result.data.echoed == "abab"
    assert result.error is None
    assert tool.calls == [(EchoInput(text="ab", times=2), ctx)]


async def test_mapping_arguments_are_validated_into_the_tools_input_model(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    """A model's tool call arrives as a raw dict, never as the tool's own type."""
    tool = EchoTool()
    registry.register(tool)

    result = await registry.call("echo", {"text": "hi"}, ctx)

    assert result.ok
    args, passed_ctx = tool.calls[0]
    assert isinstance(args, EchoInput)
    assert args.times == 1
    assert passed_ctx.run_id == "run_test01"


async def test_none_arguments_work_for_a_tool_that_takes_none(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    registry.register(PingTool())

    result = await registry.call("ping", None, ctx)

    assert result.ok
    assert result.data.echoed == "pong"


async def test_the_registry_times_the_call_itself(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    registry.register(SlowClaimTool())

    result = await registry.call("slow_claim", None, ctx)

    assert result.duration_ms != 987_654
    assert 0 <= result.duration_ms < 5_000


# --- failures come back as results ----------------------------------------------------------


async def test_an_unknown_tool_returns_a_result_naming_the_alternatives(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    registry.register(EchoTool())

    result = await registry.call("web_search", {"query": "x"}, ctx)

    assert not result.ok
    assert result.error.code is ErrorCode.UNKNOWN
    assert "web_search" in result.error.message
    assert "echo" in result.error.message


async def test_an_unknown_tool_on_an_empty_registry_says_so(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    result = await registry.call("echo", None, ctx)

    assert not result.ok
    assert "No tools are registered" in result.error.message


@pytest.mark.parametrize(
    "args",
    [
        {},  # 'text' is required
        {"text": "hi", "times": "many"},  # wrong type
        {"text": "hi", "surprise": 1},  # extra='forbid'
        "not a mapping at all",
    ],
)
async def test_invalid_arguments_short_circuit_before_the_tool_runs(
    registry: ToolRegistry, ctx: RunContext, args: Any
) -> None:
    tool = EchoTool()
    registry.register(tool)

    result = await registry.call("echo", args, ctx)

    assert not result.ok
    assert result.error.code is ErrorCode.BAD_ARGUMENTS
    assert tool.calls == []


async def test_a_tools_own_failure_is_passed_through_unchanged(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    registry.register(FailingTool())

    result = await registry.call("failing", None, ctx)

    assert not result.ok
    assert result.error.code is ErrorCode.SEARCH_UNAVAILABLE
    assert result.error.retryable is True


async def test_a_raising_tool_becomes_a_structured_failure(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    registry.register(RaisingTool())

    result = await registry.call("raising", None, ctx)

    assert not result.ok
    assert result.error.code is ErrorCode.UNKNOWN
    assert "disk on fire" in result.error.message


async def test_a_tool_returning_the_wrong_type_becomes_a_structured_failure(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    registry.register(WrongReturnTool())

    result = await registry.call("wrong_return", None, ctx)

    assert not result.ok
    assert result.error.code is ErrorCode.UNKNOWN
    assert "not a ToolResult" in result.error.message


# --- hooks -------------------------------------------------------------------------------------


async def test_hooks_run_around_the_tool_in_registration_order(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    log: list[str] = []

    class LoggingEcho(EchoTool):
        async def run(self, args: EchoInput, ctx: RunContext) -> ToolResult[EchoOutput]:
            log.append("tool")
            return await super().run(args, ctx)

    registry.register(LoggingEcho())
    registry.add_pre_hook(make_pre_hook(log, "pre_1"))
    registry.add_pre_hook(make_pre_hook(log, "pre_2"))
    registry.add_post_hook(make_post_hook(log, "post_1"))
    registry.add_post_hook(make_post_hook(log, "post_2"))

    result = await registry.call("echo", {"text": "hi"}, ctx)

    assert result.ok
    assert log == ["pre_1", "pre_2", "tool", "post_1", "post_2"]


async def test_hooks_receive_the_validated_invocation(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    seen: list[ToolInvocation] = []

    async def capture(invocation: ToolInvocation) -> None:
        seen.append(invocation)
        return None

    registry.register(EchoTool())
    registry.add_pre_hook(capture)

    await registry.call("echo", {"text": "hi"}, ctx)

    assert isinstance(seen[0].args, EchoInput)
    assert seen[0].tool.name == "echo"
    assert seen[0].ctx is ctx


async def test_a_pre_hook_can_short_circuit_the_tool_and_post_hooks_still_run(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    """The shape Day 4's cache hit and budget refusal take."""
    log: list[str] = []
    cached = ToolResult(
        ok=True, data=EchoOutput(echoed="from cache"), duration_ms=0, from_cache=True
    )
    tool = EchoTool()

    registry.register(tool)
    registry.add_pre_hook(make_pre_hook(log, "pre_1", short_circuit=cached))
    registry.add_pre_hook(make_pre_hook(log, "pre_2"))
    registry.add_post_hook(make_post_hook(log, "post_1"))

    result = await registry.call("echo", {"text": "hi"}, ctx)

    assert result.from_cache is True
    assert result.data.echoed == "from cache"
    assert tool.calls == []
    assert log == ["pre_1", "post_1"]


async def test_a_post_hook_can_replace_the_result(
    registry: ToolRegistry, ctx: RunContext
) -> None:
    replacement = ToolResult(
        ok=False,
        error=ToolError(
            code=ErrorCode.BUDGET_EXCEEDED, message="over budget", retryable=False
        ),
        duration_ms=1,
    )
    registry.register(EchoTool())
    registry.add_post_hook(make_post_hook([], "post_1", replacement=replacement))

    result = await registry.call("echo", {"text": "hi"}, ctx)

    assert result is replacement


@pytest.mark.parametrize("stage", ["pre", "post"])
async def test_a_raising_hook_cannot_break_the_run(
    registry: ToolRegistry, ctx: RunContext, stage: str
) -> None:
    async def exploding(*args: Any) -> None:
        raise RuntimeError("hook exploded")

    registry.register(EchoTool())
    if stage == "pre":
        registry.add_pre_hook(exploding)
    else:
        registry.add_post_hook(exploding)

    result = await registry.call("echo", {"text": "hi"}, ctx)

    assert not result.ok
    assert result.error.code is ErrorCode.UNKNOWN
    assert "hook exploded" in result.error.message
