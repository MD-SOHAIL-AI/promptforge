"""Execution WebSocket schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from .common import APIModel


class ExecutionEventType(str, Enum):
    TASK_CREATED = "TASK_CREATED"
    PLAN_GENERATED = "PLAN_GENERATED"
    CODE_GENERATION_STARTED = "CODE_GENERATION_STARTED"
    CODE_GENERATION_COMPLETED = "CODE_GENERATION_COMPLETED"
    BUILD_STARTED = "BUILD_STARTED"
    BUILD_COMPLETED = "BUILD_COMPLETED"
    FLASH_STARTED = "FLASH_STARTED"
    FLASH_COMPLETED = "FLASH_COMPLETED"
    MONITOR_STARTED = "MONITOR_STARTED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"


class ExecutionEvent(APIModel):
    sequence: int = Field(ge=1)
    event: ExecutionEventType
    timestamp: datetime
    task_id: str
    execution_id: str
    workflow_correlation_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
