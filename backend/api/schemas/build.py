"""Build endpoint schemas."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, field_validator

from .common import APIModel, FailureDetail


class BuildRequest(APIModel):
    project_id: str = Field(min_length=1, max_length=128)
    environment: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("project_id", "environment")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("value must be non-empty and NUL-free")
        if "/" in value or "\\" in value:
            raise ValueError("value cannot contain path separators")
        return value

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": "project-123"}},
    )


class BuildResultResponse(APIModel):
    success: bool
    status: str
    timestamp: float
    duration_ms: int = Field(ge=0)
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    firmware_path: str | None = None
    build_size_bytes: int = Field(ge=0)
    platform: str
    board: str
    toolchain_version: str
    warnings_count: int = Field(ge=0)
    process_exit_code: int | None = None
    process_timed_out: bool | None = None
    failure: FailureDetail | None = None


class BuildResponse(APIModel):
    project_id: str
    result: BuildResultResponse
