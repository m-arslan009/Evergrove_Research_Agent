"""A provider that replays a script — the reason the test suite needs no model.

The whole suite must run offline, in under 60 seconds, at zero cost (plan section 29).
That is only possible because every agent talks to `LLMProvider` and never to Ollama.
"""

from __future__ import annotations

from pydantic import BaseModel

from evergrove_agent.llm.base import (
    LLMError,
    LLMResponse,
    Message,
    ToolSpec,
)


class ExhaustedScript(LLMError):
    """The agent asked for one more response than the test scripted."""


class RecordedCall(BaseModel):
    """What a caller asked for — so a test can assert on the prompt, not just the reply."""

    messages: list[Message]
    schema_name: str | None = None
    tool_names: list[str] = []
    temperature: float = 0.0


class FakeProvider:
    """Returns scripted responses in order, and records what it was asked."""

    name = "fake"

    def __init__(self, responses: list[str | LLMResponse], *, model: str = "fake-model") -> None:
        self.model = model
        self._responses = list(responses)
        self._index = 0
        self.calls: list[RecordedCall] = []

    @property
    def remaining(self) -> int:
        return len(self._responses) - self._index

    async def generate(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append(
            RecordedCall(
                messages=list(messages),
                schema_name=schema.__name__ if schema is not None else None,
                tool_names=[tool.name for tool in tools or []],
                temperature=temperature,
            )
        )

        if self._index >= len(self._responses):
            raise ExhaustedScript(
                self.name,
                f"asked for response {self._index + 1} but only "
                f"{len(self._responses)} were scripted",
            )

        scripted = self._responses[self._index]
        self._index += 1
        if isinstance(scripted, LLMResponse):
            return scripted
        return LLMResponse(text=scripted, model=self.model, provider=self.name)
