"""The opt-in second provider — Google AI Studio (Gemini) on its free tier.

Why it exists at all: an all-local run on the target hardware takes 4-8 minutes, and the
same run hosted takes 30-60 seconds (plan section 14.5). Iterating against the hosted
provider is the difference between finishing the week and running out of it. `local`
stays the committed default so the $0-local claim stays true.

Privacy, stated plainly (plan section 30): enabling this sends the task text — and later
the fetched source excerpts — to Google. On the free tier that data may be used to
improve Google's products.

No SDK: one `fetch`-equivalent HTTP call over `httpx` keeps the dependency list at three.
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

# Gemini's responseSchema is an OpenAPI 3.0 subset, not full JSON Schema. These are the
# keywords it accepts; everything else Pydantic emits has to be translated or dropped.
_SUPPORTED_KEYS = frozenset(
    {"type", "description", "enum", "items", "properties", "required", "minItems", "maxItems"}
)
_SUPPORTED_FORMATS = frozenset({"date-time", "int32", "int64", "float", "double"})


def to_gemini_schema(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Translate a Pydantic JSON Schema into Gemini's `responseSchema` dialect.

    Inlines `$ref`/`$defs` (unsupported), collapses `T | None` into `nullable`, and drops
    keywords Gemini rejects — `format: uri`, `additionalProperties`, `minLength`, titles
    and defaults. Nothing is lost that matters: Pydantic re-validates the reply anyway,
    so this only has to be good enough to steer decoding.
    """
    defs = defs if defs is not None else schema.get("$defs", {})

    if "$ref" in schema:
        ref_name = schema["$ref"].rsplit("/", 1)[-1]
        return to_gemini_schema(defs.get(ref_name, {}), defs)

    # `X | None` arrives as anyOf[X, null]; Gemini spells that `nullable`.
    if "anyOf" in schema:
        branches = [b for b in schema["anyOf"] if b.get("type") != "null"]
        nullable = len(branches) != len(schema["anyOf"])
        if not branches:
            return {"type": "string", "nullable": True}
        translated = to_gemini_schema(branches[0], defs)
        if nullable:
            translated["nullable"] = True
        return translated

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SUPPORTED_KEYS:
            continue
        if key == "properties":
            out[key] = {name: to_gemini_schema(sub, defs) for name, sub in value.items()}
        elif key == "items":
            out[key] = to_gemini_schema(value, defs)
        else:
            out[key] = value

    fmt = schema.get("format")
    if fmt in _SUPPORTED_FORMATS:
        out["format"] = fmt
    out.setdefault("type", "object" if "properties" in out else "string")
    return out


class HostedProvider:
    """Calls Google AI Studio's `generateContent` endpoint."""

    name = "hosted"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self.model = model or self._settings.hosted_model
        self._client = client
        self._owns_client = client is None

    def _api_key(self) -> str:
        key = self._settings.google_api_key
        if key is None or not key.get_secret_value():
            raise LLMError(
                self.name,
                "GOOGLE_API_KEY is not set. Create a free key at aistudio.google.com, "
                "put it in .env, or run with every *_PROVIDER=local.",
            )
        return key.get_secret_value()

    async def generate(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        api_key = self._api_key()

        system_parts = [m.content for m in messages if m.role == "system"]
        contents = [
            {
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}],
            }
            for m in messages
            if m.role != "system"
        ]

        generation_config: dict[str, Any] = {"temperature": temperature}
        if schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = to_gemini_schema(schema.model_json_schema())

        payload: dict[str, Any] = {"contents": contents, "generationConfig": generation_config}
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": to_gemini_schema(tool.parameters),
                        }
                        for tool in tools
                    ]
                }
            ]

        started = time.perf_counter()
        client = self._client or httpx.AsyncClient(
            base_url=self._settings.hosted_api_base,
            timeout=httpx.Timeout(120.0),
        )
        try:
            response = await client.post(
                f"/models/{self.model}:generateContent",
                json=payload,
                headers={"x-goog-api-key": api_key},
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                self.name,
                f"Google AI Studio returned {exc.response.status_code}: "
                f"{exc.response.text[:200]}",
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(self.name, f"could not reach Google AI Studio: {exc}") from exc
        finally:
            if self._owns_client:
                await client.aclose()

        duration_ms = int((time.perf_counter() - started) * 1000)
        candidates = body.get("candidates") or []
        if not candidates:
            raise LLMError(self.name, f"no candidates in response: {str(body)[:200]}")

        parts = (candidates[0].get("content") or {}).get("parts") or []
        return LLMResponse(
            text="".join(part["text"] for part in parts if "text" in part),
            model=body.get("modelVersion", self.model),
            provider=self.name,
            tool_calls=[
                ToolCall(
                    name=part["functionCall"]["name"],
                    arguments=part["functionCall"].get("args") or {},
                )
                for part in parts
                if "functionCall" in part
            ],
            duration_ms=duration_ms,
            finish_reason=candidates[0].get("finishReason"),
        )
