"""The local, mandatory, $0 path — Ollama over its HTTP API.

Structured output works by handing Ollama `format=<JSON Schema>`, which uses constrained
decoding: the model physically cannot emit JSON that violates the schema. That is the
single biggest reliability win available for a small local model (plan section 17).
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import BaseModel

from evergrove_agent.config import Settings, get_settings
from evergrove_agent.llm.base import (
    LLMError,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
)


class OllamaProvider:
    """Calls a local Ollama server. No API key, no network beyond localhost."""

    name = "ollama"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self.model = model or self._settings.local_model
        self._client = client
        self._owns_client = client is None

    async def generate(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.model_dump() for m in messages],
            "stream": False,
            # keep_alive here as well as in the environment, so a caller cannot
            # accidentally pay the 5-20 s reload on every call (plan 14.3).
            "keep_alive": self._settings.local_keep_alive,
            # Thinking models reason into `message.thinking`, which `format` does not
            # constrain and which this provider discards — only `message.content` is read
            # below. Left on, qwen3:4b spends minutes per call generating tokens that are
            # then dropped: measured during Day 3 S14, one trivial prompt took 173.9 s with
            # thinking and 5.1 s without, on the same model, schema and machine. Prefill
            # falls too (547 -> 16 tokens), because the thinking scaffolding leaves the
            # chat template with it. Servers and models that do not support the field
            # ignore it.
            "think": False,
            "options": {
                "temperature": temperature,
                "num_ctx": self._settings.num_ctx,
            },
        }
        if schema is not None:
            payload["format"] = schema.model_json_schema()
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]

        started = time.perf_counter()
        client = self._client or httpx.AsyncClient(
            base_url=self._settings.ollama_host,
            timeout=httpx.Timeout(self._settings.total_run_timeout_s),
        )
        try:
            response = await client.post("/api/chat", json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:  # a reachable server that refused
            raise LLMError(
                self.name,
                f"Ollama returned {exc.response.status_code}: {exc.response.text[:200]}. "
                f"Is the model '{self.model}' pulled? Try: ollama pull {self.model}",
            ) from exc
        except httpx.TimeoutException as exc:
            # Separated from the unreachable case because the two need opposite fixes and
            # a timeout stringifies to "", so folding them together reported a running
            # server as unreachable and gave nothing to act on (found during S14).
            raise LLMError(
                self.name,
                f"Ollama did not answer within TOTAL_RUN_TIMEOUT_S="
                f"{self._settings.total_run_timeout_s}s (model '{self.model}'). The server "
                f"is reachable; the request was too slow. Check `ollama ps` for the load, "
                f"or use a smaller model.",
            ) from exc
        except httpx.HTTPError as exc:  # unreachable, DNS, connection refused
            raise LLMError(
                self.name,
                f"could not reach Ollama at {self._settings.ollama_host}: {exc}",
            ) from exc
        finally:
            if self._owns_client:
                await client.aclose()

        duration_ms = int((time.perf_counter() - started) * 1000)
        message = body.get("message") or {}
        return LLMResponse(
            text=message.get("content", ""),
            model=body.get("model", self.model),
            provider=self.name,
            tool_calls=[
                ToolCall(
                    name=call["function"]["name"],
                    arguments=call["function"].get("arguments") or {},
                )
                for call in message.get("tool_calls") or []
            ],
            duration_ms=duration_ms,
            finish_reason=body.get("done_reason"),
        )
