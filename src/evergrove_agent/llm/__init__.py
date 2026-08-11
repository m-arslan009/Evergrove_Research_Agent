"""Model access. Nothing outside this package talks to a model directly."""

from __future__ import annotations

from evergrove_agent.config import AgentRole, ProviderName, Settings, get_settings
from evergrove_agent.llm.base import (
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
)
from evergrove_agent.llm.fake_provider import FakeProvider
from evergrove_agent.llm.hosted_provider import HostedProvider
from evergrove_agent.llm.ollama_provider import OllamaProvider

__all__ = [
    "FakeProvider",
    "HostedProvider",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "OllamaProvider",
    "ToolCall",
    "ToolSpec",
    "build_provider",
]


def build_provider(
    role: AgentRole,
    settings: Settings | None = None,
    *,
    override: ProviderName | None = None,
) -> LLMProvider:
    """The provider serving `role`, per `.env` (plan section 14.2).

    `override` exists for the CLI's `--provider` flag; it does not change the file.
    """
    settings = settings or get_settings()
    provider = override or settings.provider_for(role)
    if provider == "hosted":
        return HostedProvider(settings)
    return OllamaProvider(settings)
