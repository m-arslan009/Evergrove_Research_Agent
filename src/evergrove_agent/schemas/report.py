"""The deliverable: a session-sized, source-grounded focus preparation.

This is the most expensive schema in the project to change (plan section 28), which is
why it is written on Day 1 and reviewed before Day 2 begins. It is handed to the model
as a JSON Schema for constrained decoding, then validated again by Pydantic, then
validated a third time against business rules by `validate_report` (Day 2, plan 17).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

SourceAuthority = Literal["official", "standards", "primary", "secondary", "unknown"]
"""Where a source sits on the priority ladder (plan section 10).

`unknown` is required for a URL that a search discovered but the agent never read —
we do not get to call a page authoritative when nobody opened it.
"""


class Resource(BaseModel):
    """One source the user is being pointed at."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=200)
    url: HttpUrl
    why_this_source: str = Field(min_length=10, max_length=300)
    authority: SourceAuthority


class PracticeExercise(BaseModel):
    """One thing to actually do, so the session ends in work rather than reading."""

    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=20, max_length=500)
    expected_outcome: str = Field(min_length=10, max_length=300)


class FocusPreparationReport(BaseModel):
    """Everything the user needs before pressing Start."""

    model_config = ConfigDict(extra="forbid")

    # --- provenance: written by our code, never by the model -------------------------
    run_id: str = Field(min_length=1, max_length=64)
    generated_at: datetime
    model_used: str = Field(min_length=1, max_length=120)

    # --- what the user asked ----------------------------------------------------------
    original_task: str = Field(min_length=1, max_length=500)
    session_duration_minutes: int = Field(ge=5, le=180)

    # --- what the agent decided --------------------------------------------------------
    interpreted_goal: str = Field(min_length=10, max_length=400)
    session_objective: str = Field(min_length=10, max_length=300)
    topics_to_cover: list[str] = Field(min_length=2, max_length=8)
    topics_to_skip: list[str] = Field(default_factory=list, max_length=10)
    resources: list[Resource] = Field(default_factory=list, min_length=0, max_length=5)
    practice: PracticeExercise | None = None
    success_criteria: str = Field(min_length=10, max_length=400)

    # --- honesty fields — these are what stop the agent inventing ----------------------
    assumptions: list[str] = Field(default_factory=list, max_length=6)
    unknowns: list[str] = Field(default_factory=list, max_length=6)

    # --- research provenance -------------------------------------------------------------
    hops_used: int = Field(ge=0, le=3)
    sources_examined: int = Field(ge=0)

    @field_validator("topics_to_cover")
    @classmethod
    def no_blank_topics(cls, v: list[str]) -> list[str]:
        if any(not t.strip() for t in v):
            raise ValueError("topics_to_cover contains an empty entry")
        return v
