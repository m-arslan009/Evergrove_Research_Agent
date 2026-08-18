"""Every tunable value in the project.

Plan section 21, feature 1: budgets scattered through the code cannot be changed for a
demo. One file, one place. The defaults below are the revised, hardware-aware budgets
from plan section 14.4 — they are sized for a CPU-bound 16 GB machine, not for a GPU.

Nothing here reaches out to the network, the database, or a model. It is values only,
so every other module can import it without a cycle.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["local", "hosted"]
"""Which LLM provider serves a given agent role. `local` keeps the $0 guarantee."""

SearchBackendName = Literal["fixture", "serpapi", "academic", "ddgs"]
"""Which search backend `web_search` uses. `fixture` is the default so tests and
development cost no SerpAPI quota (plan section 10)."""

AgentRole = Literal["supervisor", "researcher", "appraiser"]

AgentMode = Literal["single", "multi"]
"""Which reasoning topology a run uses (Day 5 T1). `multi` is the Supervisor coordinating a
Researcher and an Appraiser and is the default; `single` is the Day 3 loop running the same
four stages as one agent, kept because it is a deliverable in its own right.

A type alias only, with **no `Settings` field behind it**: the mode is a per-run choice the
CLI makes and `service.prepare_focus_session` takes as a parameter, not a deployment tunable.
Giving it an `.env` value would create a second place the default lives and let a stale
environment silently decide which topology a demo runs."""

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Configuration, read from the environment and `.env`.

    See `.env.example` for the documented set. Secrets are `SecretStr` so they cannot
    be printed by accident in a trace or a log line.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # `model_used` etc. are ours; stop pydantic reserving the `model_` prefix.
        protected_namespaces=(),
    )

    # --- Local model runtime (Ollama) -------------------------------------------------
    ollama_host: str = Field(
        default="http://localhost:11434",
        description="Base URL of the local Ollama server.",
    )
    local_model: str = Field(
        default="qwen3:4b",
        description="Primary local model. Sized for a CPU-bound machine (plan 14.2).",
    )
    local_keep_alive: str = Field(
        default="60m",
        description=(
            "Sent as Ollama's keep_alive. Without it the 2.6 GB model unloads between "
            "calls and reloads from disk every time (plan 14.3)."
        ),
    )
    num_ctx: int = Field(
        default=4096,
        ge=512,
        le=32768,
        description="Ollama context window. Larger is not free: KV cache and prefill grow.",
    )

    # --- Hosted model runtime (Google AI Studio, free tier, opt-in) --------------------
    hosted_model: str = Field(
        default="gemini-3.6-flash",
        description=(
            "Google AI Studio model id. Confirm the exact id against your own AI Studio "
            "console before relying on it — free-tier model ids change. The previous "
            "default, gemini-2.5-flash, now 404s for newly issued keys: ListModels still "
            "advertises it, but generateContent answers 'no longer available to new users'."
        ),
    )
    hosted_api_base: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        description="Google AI Studio (Gemini) REST base URL.",
    )
    google_api_key: SecretStr | None = Field(
        default=None,
        description="Google AI Studio key. Free tier, no card. Required only for hosted.",
    )

    # --- Which provider serves which role (plan 14.2) ----------------------------------
    supervisor_provider: ProviderName = "local"
    researcher_provider: ProviderName = "local"
    appraiser_provider: ProviderName = "local"

    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="0 for every structured-output call; schema adherence collapses above it.",
    )

    # --- Search (consumed from Day 2; configured here so budgets live in one file) -----
    search_backend: SearchBackendName = "fixture"
    serpapi_api_key: SecretStr | None = None
    monthly_search_budget: int = Field(
        default=200,
        ge=0,
        description="Live searches allowed per calendar month. 200 of SerpAPI's 250, "
        "leaving 50 in reserve (plan 10).",
    )
    search_timeout_s: int = Field(
        default=15,
        ge=1,
        description="Per-request timeout for a live search backend. Shared by serpapi "
        "and academic so the two cannot drift apart.",
    )

    # --- Per-run budgets (plan 14.4, revised for this hardware) ------------------------
    max_hops: int = Field(
        default=3,
        ge=1,
        le=3,
        description="Research hops allowed per run. Raised from the plan's 2 on request. "
        "3 is the ceiling, not a coincidence: FocusPreparationReport.hops_used is le=3, "
        "so going higher means changing the most expensive schema in the project.",
    )
    max_search_calls: int = Field(default=3, ge=0)
    max_fetch_calls: int = Field(default=4, ge=0)
    max_sources_kept: int = Field(default=3, ge=0)
    max_model_calls: int = Field(default=10, ge=1)
    source_excerpt_chars: int = Field(
        default=3000,
        ge=200,
        description="How much of a source the model ever sees. Separate from how much "
        "we extract and cache (plan 11).",
    )
    max_source_text_chars: int = Field(
        default=200_000,
        ge=1000,
        description="Ceiling on the extracted text kept and cached per source. A safety "
        "bound, not a reading budget: what the cache holds must stay large enough for a "
        "later, differently-worded question to find different passages in it, so this is "
        "deliberately far above SOURCE_EXCERPT_CHARS.",
    )
    max_document_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        description="Largest attachment `read_document` will open. A refusal is cheaper "
        "than parsing a 500 MB file on a 16 GB machine.",
    )
    max_fetch_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=1024,
        description="Largest response body `fetch_url` will download. Deliberately not "
        "MAX_DOCUMENT_BYTES: a page a stranger's server hands us is not an attachment "
        "the user chose, and 5 MB is already far larger than any article.",
    )
    fetch_timeout_s: int = Field(
        default=15,
        ge=1,
        description="Per-request timeout for `fetch_url`. Separate from SEARCH_TIMEOUT_S "
        "so a slow page and a slow search backend can be tuned apart.",
    )
    total_run_timeout_s: int = Field(
        default=900,
        ge=10,
        description="900 for an all-local run; drop to 180 when every role is hosted.",
    )
    max_output_retries: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Attempts at a schema-valid report before failing loudly (plan 17).",
    )

    # --- Tracing (plan 13) ---------------------------------------------------------------
    trace_summary_chars: int = Field(
        default=200,
        ge=40,
        description="Ceiling on a span's input_summary / output_summary. A trace is a "
        "diagnostic, not a second copy of the evidence: the page text a fetch returned "
        "already lives in source_cache, so a span records enough to recognise the call "
        "and nothing more.",
    )

    # --- Expiry (plan 12) ---------------------------------------------------------------
    cache_ttl_days: int = Field(default=7, ge=1)
    search_cache_ttl_days: int = Field(
        default=7,
        ge=1,
        description="How long a cached result list keeps answering a repeated query. "
        "Separate from CACHE_TTL_DAYS so search freshness can be tuned without "
        "expiring fetched page text — this is the cache that protects the quota.",
    )
    memory_recall_max_age_days: int = Field(default=30, ge=1)

    # --- Paths ---------------------------------------------------------------------------
    db_path: Path = Field(
        default=PROJECT_ROOT / "data" / "evergrove_agent.sqlite3",
        description="The single SQLite file: memory, cache, budget, traces.",
    )
    allowed_attachment_dir: Path = Field(
        default=PROJECT_ROOT / "fixtures",
        description=(
            "attachment_path must resolve inside this directory. Security guard, and it "
            "matters the moment the tool is exposed over MCP (plan 30)."
        ),
    )
    search_fixture_dir: Path = Field(
        default=PROJECT_ROOT / "fixtures" / "search",
        description="Recorded search responses the `fixture` backend replays (plan 10).",
    )

    @field_validator("db_path", "allowed_attachment_dir", "search_fixture_dir")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    # --- Derived helpers -----------------------------------------------------------------
    def provider_for(self, role: AgentRole) -> ProviderName:
        """Which provider serves `role`."""
        return getattr(self, f"{role}_provider")

    @property
    def roles_using_hosted(self) -> tuple[AgentRole, ...]:
        """Roles currently resolving to the hosted provider — empty means a $0-local run."""
        roles: tuple[AgentRole, ...] = ("supervisor", "researcher", "appraiser")
        return tuple(role for role in roles if self.provider_for(role) == "hosted")

    def force_fully_local(self) -> None:
        """Back the `--fully-local` flag (plan 30, control 3).

        Refuses rather than silently rewriting, so the $0-local claim stays checkable
        instead of asserted.
        """
        if self.roles_using_hosted:
            raise ValueError(
                "--fully-local was requested but these roles resolve to the hosted "
                f"provider: {', '.join(self.roles_using_hosted)}. "
                "Set them to 'local' in .env, or drop the flag."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings object. Cached so `.env` is read once."""
    return Settings()
