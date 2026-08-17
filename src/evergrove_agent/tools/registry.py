"""The one path to a tool (plan sections 7.2, 9.3 and 13).

Nothing calls a tool directly. Every call goes through `ToolRegistry.call`, which resolves
the name, validates the arguments, runs the pre-hooks, runs the tool, then runs the
post-hooks. Because that choke point exists from the start, Day 4 adds timestamps,
tracing, budget checks and caching by filling two lists — not by editing seven call sites.

The registry knows nothing about models, HTTP, search backends, SQLite, or any particular
tool. It depends on `schemas/` and `tools/base.py` and nothing else.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from evergrove_agent.schemas import ErrorCode, ToolError, ToolResult
from evergrove_agent.tools.base import (
    PostToolHook,
    PreToolHook,
    RunContext,
    Tool,
    ToolInvocation,
)

_MAX_MESSAGE_CHARS = 500
"""`ToolError.message`'s own ceiling. Long pydantic errors are truncated to it rather than
allowed to fail validation — the registry's promise is that `call` never raises."""


class ToolRegistry:
    """Holds the tools, holds the hook chains, and is the only thing that runs a tool.

    Tool names are unique: registering a name twice is a wiring bug and raises. Everything
    that can go wrong at *call* time is a `ToolResult` instead, because by then a model is
    driving and a failure has to be something it can read and react to.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # Empty today by design (plan section 22, feature 1) — Day 4 fills them.
        self._pre_hooks: list[PreToolHook] = []
        self._post_hooks: list[PostToolHook] = []

    # --- registration ------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Add `tool` under its own name. Raises `ValueError` on a duplicate or a bad name."""
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{type(tool).__name__} has no usable 'name'")
        if not callable(getattr(tool, "run", None)):
            raise ValueError(f"tool {name!r} has no callable 'run'")
        if name in self._tools:
            raise ValueError(
                f"a tool named {name!r} is already registered; tool names must be unique"
            )
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        """The registered tool, or `None`. Lookup only — it does not run anything."""
        return self._tools.get(name)

    @property
    def names(self) -> tuple[str, ...]:
        """Every registered name, sorted — the menu Day 3 advertises to the model."""
        return tuple(sorted(self._tools))

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    # --- hooks -------------------------------------------------------------------------

    def add_pre_hook(self, hook: PreToolHook) -> None:
        """Append a pre-hook. They run in the order added, before the tool."""
        self._pre_hooks.append(hook)

    def add_post_hook(self, hook: PostToolHook) -> None:
        """Append a post-hook. They run in the order added, after the tool."""
        self._post_hooks.append(hook)

    # --- the single call path ------------------------------------------------------------

    async def call(
        self,
        name: str,
        args: BaseModel | Mapping[str, Any] | None,
        ctx: RunContext,
    ) -> ToolResult[Any]:
        """Run `name` with `args` and return its `ToolResult`. Never raises.

        `args` may be the tool's own input model, a raw mapping (what a model's tool call
        arrives as), or `None` for a tool that takes no arguments.

        Order: resolve → validate arguments → pre-hooks → tool → post-hooks. A pre-hook may
        return a result to skip the tool; the post-hooks still run over it, so a
        short-circuited call is traced like any other. An unknown name or invalid arguments
        return before the hook chain, since there is no validated invocation to hand it.

        The guard below is the last one: a *post*-hook that raises is caught here, after
        the result it was given already exists. Everything earlier is caught inside
        `_run_chain`, so the post-hooks see it too.
        """
        started = time.perf_counter()

        tool = self._tools.get(name)
        if tool is None:
            return self._failure(
                ErrorCode.UNKNOWN,
                f"there is no tool named {name!r}. {self._menu()}",
                started,
            )

        try:
            parsed = self._parse_args(tool, args)
        except ValidationError as exc:
            return self._failure(
                ErrorCode.BAD_ARGUMENTS,
                f"{name} rejected the arguments: {self._describe(exc)}",
                started,
            )

        invocation = ToolInvocation(tool=tool, args=parsed, ctx=ctx)
        try:
            result = await self._run_chain(invocation)
        except Exception as exc:  # a raising post-hook must not break the run either
            return self._failure(
                ErrorCode.UNKNOWN,
                f"{name} failed unexpectedly: {type(exc).__name__}: {exc}",
                started,
            )
        return result

    async def _run_chain(self, invocation: ToolInvocation) -> ToolResult[Any]:
        """Pre-hooks, then the tool unless one of them short-circuited, then post-hooks.

        **Every outcome reaches the post-hooks, including a raising tool or pre-hook.** A
        tool that raises is the one case that breaks the contract, so it is also the one
        case a trace is most wanted for; leaving it as the single path that skips the
        post-hooks would leave its span open forever and make a crash the hardest failure
        to read back. The exception still becomes an ordinary `UNKNOWN` failure — the
        post-hooks observe it, they do not learn it was an exception.
        """
        started = time.perf_counter()
        name = invocation.tool.name

        try:
            result = await self._invoke(invocation, started)
        except Exception as exc:  # a raising tool or pre-hook must not break the run
            result = self._failure(
                ErrorCode.UNKNOWN,
                f"{name} failed unexpectedly: {type(exc).__name__}: {exc}",
                started,
            )

        for hook in self._post_hooks:
            replacement = await hook(invocation, result)
            if replacement is not None:
                result = replacement
        return result

    async def _invoke(self, invocation: ToolInvocation, started: float) -> ToolResult[Any]:
        """The pre-hooks and the tool: everything the post-hooks report *on*.

        Split out so one `try` in `_run_chain` covers all of it and none of the post-hooks.
        Raises whatever a pre-hook or a tool raises.
        """
        result: ToolResult[Any] | None = None
        for hook in self._pre_hooks:
            result = await hook(invocation)
            if result is not None:
                break

        if result is None:
            result = await invocation.tool.run(invocation.args, invocation.ctx)

        if not isinstance(result, ToolResult):
            return self._failure(
                ErrorCode.UNKNOWN,
                f"{invocation.tool.name} returned {type(result).__name__}, "
                "not a ToolResult",
                started,
            )

        # The registry times the call, so no tool has to and every trace row agrees.
        return result.model_copy(update={"duration_ms": self._elapsed_ms(started)})

    # --- helpers --------------------------------------------------------------------------

    @staticmethod
    def _parse_args(tool: Tool, args: BaseModel | Mapping[str, Any] | None) -> BaseModel:
        """Coerce whatever the caller passed into the tool's `input_model`.

        Raises `ValidationError`, which the caller turns into `BAD_ARGUMENTS`.
        """
        if isinstance(args, tool.input_model):
            return args
        payload: Any = args.model_dump() if isinstance(args, BaseModel) else args
        return tool.input_model.model_validate({} if payload is None else payload)

    def _menu(self) -> str:
        """The available-tools sentence appended to an unknown-tool error."""
        return (
            f"Available tools: {', '.join(self.names)}."
            if self._tools
            else "No tools are registered."
        )

    @staticmethod
    def _describe(exc: ValidationError) -> str:
        """A pydantic failure as one line the model can act on, not a stack trace."""
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'input'}: {error['msg']}"
            for error in exc.errors()
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    @classmethod
    def _failure(cls, code: ErrorCode, message: str, started: float) -> ToolResult[Any]:
        """A structured failure the model can read — the registry's only return shape.

        Always `retryable=False`: none of the registry's own failures — a missing tool,
        rejected arguments, a broken tool — improve on an identical second attempt.
        """
        return ToolResult(
            ok=False,
            error=ToolError(
                code=code,
                message=message[:_MAX_MESSAGE_CHARS],
                retryable=False,
            ),
            duration_ms=cls._elapsed_ms(started),
        )
