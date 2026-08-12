"""The shared tool contract (plan section 9.3).

Tools never raise. A failure is a value, so the agent can reason about it and the trace
can record it — that single decision removes most of the error handling from the agent
loop. The per-tool input/output models arrive with their tools on Day 2; the envelope
below is written on Day 1 because everything else is shaped around it.
"""

from __future__ import annotations

from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class ErrorCode(str, Enum):
    """Every way a tool is allowed to fail (plan sections 11, 13 and 10).

    The string value is what appears in a trace row and in the message the model reads,
    so these names are part of the contract.
    """

    # arguments and budgets — raised by the pre-tool hook chain, before the tool runs
    BAD_ARGUMENTS = "BAD_ARGUMENTS"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    MONTHLY_BUDGET_EXCEEDED = "MONTHLY_BUDGET_EXCEEDED"

    # network
    TIMEOUT = "TIMEOUT"
    FETCH_FAILED = "FETCH_FAILED"
    NOT_FOUND = "NOT_FOUND"
    SEARCH_UNAVAILABLE = "SEARCH_UNAVAILABLE"

    # documents
    CORRUPT_PDF = "CORRUPT_PDF"
    CORRUPT_DOCX = "CORRUPT_DOCX"
    ENCRYPTED_PDF = "ENCRYPTED_PDF"
    NO_TEXT_LAYER = "NO_TEXT_LAYER"
    EMPTY_FILE = "EMPTY_FILE"
    UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
    PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"

    # last resort
    UNKNOWN = "UNKNOWN"


class ToolError(BaseModel):
    """A failure the model is allowed to see and reason about."""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str = Field(
        min_length=1,
        max_length=500,
        description="Human readable, safe to show the model, and actionable.",
    )
    retryable: bool


class ToolResult(BaseModel, Generic[T]):
    """The envelope every tool returns — success and failure alike."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    data: T | None = None
    error: ToolError | None = None
    duration_ms: int = Field(ge=0)
    from_cache: bool = False

    @model_validator(mode="after")
    def _exactly_one_outcome(self) -> ToolResult[T]:
        if self.ok and self.error is not None:
            raise ValueError("a successful ToolResult must not carry an error")
        if not self.ok and self.error is None:
            raise ValueError("a failed ToolResult must carry an error")
        return self
