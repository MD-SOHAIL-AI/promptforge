"""Execution API schemas."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, field_validator

from .common import APIModel


class ExecuteRequest(APIModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    task_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must be non-empty")
        if "\x00" in value:
            raise ValueError("prompt cannot contain NUL characters")
        return value

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith("task-") or not _safe_identifier(value):
            raise ValueError("task_id must be a safe identifier beginning with 'task-'")
        return value

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "prompt": "Blink LED on ESP32",
                "task_id": "task-client-001",
            }
        },
    )


class ExecutionFailureResponse(APIModel):
    step: str
    message: str
    category: str
    recoverable: bool
    fatal: bool
    tool_name: str | None = None
    exception_type: str | None = None


class ExecutionStepResponse(APIModel):
    step: str
    success: bool
    tool_name: str | None = None
    execution_time_ms: int = Field(ge=0)
    result: dict[str, Any] | list[Any] | None = None
    failure: ExecutionFailureResponse | None = None


class ExecuteResponse(APIModel):
    status: str
    task_id: str
    execution_time_ms: int = Field(ge=0)
    steps: list[ExecutionStepResponse]
    failures: list[ExecutionFailureResponse]


def _safe_identifier(value: str) -> bool:
    return all(character.isalnum() or character in "._-" for character in value)
