"""What the user asked for — the single input to a run.

The field set is deliberately identical to the MCP tool's signature (plan section 16),
so the CLI and the MCP server describe the same thing.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class TaskContext(BaseModel):
    """A task the user is about to spend one focus session on."""

    model_config = ConfigDict(extra="forbid")

    task_title: str = Field(
        min_length=1,
        max_length=500,
        description='What the user wrote, e.g. "Learn PostgreSQL indexing".',
    )
    session_minutes: int = Field(
        default=25,
        ge=5,
        le=180,
        description="Length of the focus session this preparation must fit.",
    )
    task_description: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional extra context the user typed.",
    )
    attachment_path: Path | None = Field(
        default=None,
        description=(
            "Optional .txt / .md / .pdf to prepare from. Must resolve inside "
            "Settings.allowed_attachment_dir; enforced by the read_document tool (Day 2)."
        ),
    )
