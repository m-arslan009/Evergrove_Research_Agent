"""The pipeline tool wrapping source normalisation (plan section 10).

Registered like any other tool, so a run's trace shows what normalisation discarded —
but never advertised to the model: which URLs are the same page is not a judgement call
a model should be making. `web_search` and `fetch_url` compose with the pure
`normalize_sources` function instead of calling back through the registry.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from evergrove_agent.schemas import ToolResult
from evergrove_agent.search.normalize import (
    NormalizeSourcesOutput,
    RawSource,
    normalize_sources,
)
from evergrove_agent.tools.base import RunContext


class NormalizeSourcesInput(BaseModel):
    """Candidate sources, in whatever state the backend that found them left them."""

    model_config = ConfigDict(extra="forbid")

    sources: list[RawSource] = Field(default_factory=list)


class NormalizeSourcesTool:
    """Canonicalise, deduplicate, classify and rank a list of candidate sources."""

    name = "normalize_sources"
    description = (
        "Canonicalise, deduplicate, classify and rank candidate sources so "
        "authoritative ones come first. Pipeline step; not offered to the model."
    )
    input_model = NormalizeSourcesInput
    output_model = NormalizeSourcesOutput

    async def run(
        self, args: NormalizeSourcesInput, ctx: RunContext
    ) -> ToolResult[NormalizeSourcesOutput]:
        """Always succeeds: an unusable URL is counted in `dropped`, never raised.

        `duration_ms` is left at 0 — the registry times the call and stamps it centrally.
        """
        return ToolResult(ok=True, data=normalize_sources(args.sources), duration_ms=0)
