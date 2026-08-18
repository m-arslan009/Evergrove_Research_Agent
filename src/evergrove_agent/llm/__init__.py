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

    **The only place a provider is constructed.** Each role resolves independently, so
    `SUPERVISOR_PROVIDER=local RESEARCHER_PROVIDER=local APPRAISER_PROVIDER=hosted` is a
    configuration change and nothing else — no agent reads a setting or builds a client of
    its own, and `AgentProviders.from_settings` is three calls to this function.

    `override` exists for the CLI's `--provider` flag; it does not change the file.

    **An unrecognised name raises rather than defaulting to local.** `ProviderName` already
    makes a bad `*_PROVIDER` a `ValidationError` when `Settings` is built, which is where a
    typo should be caught — but `Settings.model_copy(update=…)` and `setattr` both bypass
    that, and those are exactly how `tools/cli.py` and `main.py` apply their overrides. A
    silent fall-through would hand such a run the local model while the operator believed a
    role was hosted, which is the one failure that looks like success.
    """
    settings = settings or get_settings()
    provider = override or settings.provider_for(role)
    if provider == "local":
        return OllamaProvider(settings)
    if provider == "hosted":
        return HostedProvider(settings)
    raise ValueError(
        f"unknown provider {provider!r} for role {role!r}; expected 'local' or 'hosted'"
    )
