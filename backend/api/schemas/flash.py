"""Flash endpoint schemas."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, field_validator

from ...tools.board_detector import BoardType
from .build import BuildResultResponse
from .common import APIModel, FailureDetail


class FlashRequest(APIModel):
    project_id: str = Field(min_length=1, max_length=128)
    board_type: BoardType
    port: str = Field(min_length=1, max_length=256)
    environment: str | None = Field(default=None, min_length=1, max_length=128)
    baudrate: int = Field(default=115_200, gt=0)
    verify: bool = True
    timeout_s: float = Field(default=60.0, gt=0, le=600)

    @field_validator("project_id", "port", "environment")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("value must be non-empty and NUL-free")
        return value

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": "project-123",
                "board_type": "ESP32",
                "port": "COM7",
                "verify": True,
            }
        },
    )


class FlashResultResponse(APIModel):
    success: bool
    status: str
    timestamp: float
    duration_ms: int = Field(ge=0)
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    port: str
    board: str
    flash_duration_ms: int = Field(ge=0)
    verification_status: str
    bytes_written: int = Field(ge=0)
    tool: str
    tool_version: str
    process_exit_code: int | None = None
    process_timed_out: bool | None = None
    failure: FailureDetail | None = None


class FlashResponse(APIModel):
    project_id: str
    build: BuildResultResponse
    flash: FlashResultResponse | None = None
