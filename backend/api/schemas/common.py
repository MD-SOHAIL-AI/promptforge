"""Shared API schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    """Strict base model used at every public API boundary."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ErrorResponse(APIModel):
    code: str = Field(description="Stable machine-readable error code")
    message: str = Field(description="Safe human-readable error message")
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "code": "PROJECT_NOT_FOUND",
                "message": "Project was not found",
                "details": {"project_id": "project-123"},
            }
        },
    )


class FailureDetail(APIModel):
    category: str
    message: str
    retryable: bool = False
    stage: str | None = None
    exception_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
