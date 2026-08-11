"""The one interface every model sits behind (plan section 21, feature 3).

The point of this file is that the model is a config value, not a code change — and
that tests can run with no model at all. Every provider in this package implements
`LLMProvider` and nothing else in the codebase talks to a model directly.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

MessageRole = Literal["system", "user", "assistant", "tool"]


class Message(BaseModel):
    """One turn of the conversation handed to a provider."""

    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str


class ToolSpec(BaseModel):
    """A tool as the *model* sees it.

    Distinct from the runtime `Tool` protocol (Day 2): this is the advertisement, not
    the implementation. `parameters` is a JSON Schema, normally produced by calling
    `SomeInputModel.model_json_schema()`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """A tool the model asked for, before anything has run it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    """What a provider returns. `text` is the raw body; parsing is the caller's job."""

    model_config = ConfigDict(extra="forbid")

    text: str
    model: str
    provider: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)
    finish_reason: str | None = None

    def parse_as(self, schema: type[BaseModel]) -> BaseModel:
        """Validate `text` as `schema`. Raises `pydantic.ValidationError` if it is not."""
        return schema.model_validate_json(self.text)


class LLMError(RuntimeError):
    """A provider could not be reached, or answered with something unusable.

    Unlike a `ToolError`, this is an exception: an unreachable model is not a result the
    agent can reason about, it is a broken run.
    """

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider


@runtime_checkable
class LLMProvider(Protocol):
    """The whole model surface. Three implementations: ollama, hosted, fake."""

    name: str
    model: str

    async def generate(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Answer `messages`.

        When `schema` is given the provider must constrain decoding to it, so the reply
        is JSON matching that schema. When `tools` are given the provider may answer
        with `tool_calls` instead of text.
        """
        ...
