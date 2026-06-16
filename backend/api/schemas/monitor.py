"""Serial monitor endpoint schemas."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, field_validator

from .common import APIModel


class MonitorStartRequest(APIModel):
    port: str | None = Field(default=None, min_length=1, max_length=256)
    baudrate: int = Field(default=115_200, gt=0)
    timeout_s: float = Field(default=1.0, gt=0, le=60)

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("port must be non-empty and NUL-free")
        return value

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"port": "COM7", "baudrate": 115200}},
    )


class MonitorStatusResponse(APIModel):
    state: str
    connected: bool
    port: str | None
    baudrate: int
    metrics: dict[str, Any] = Field(default_factory=dict)
