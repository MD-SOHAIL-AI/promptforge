"""Additive work-progress and model-test metadata shared with the renderer."""
from typing import Any, Literal
from pydantic import BaseModel, Field


class TaskExecution(BaseModel):
    version: Literal[1] = 1
    phase: Literal["configuration", "generation", "repair", "verification"]
    active_milestone: dict[str, Any] | None = None
    missing_artifacts: list[str] = Field(default_factory=list, max_length=64)
    blocking_symbols: list[str] = Field(default_factory=list, max_length=8)
    remaining_requirement_ids: list[str] = Field(default_factory=list, max_length=64)
    stalled_turns: int = Field(default=0, ge=0, le=5)
    next_action: str = Field(max_length=1800)
    instruction: str = ""


class LinkerDiagnostic(BaseModel):
    stage: Literal["link"] = "link"
    severity: Literal["error"] = "error"
    kind: str
    symbol: str = Field(max_length=400)
    path: str | None = None
    line: int | None = Field(default=None, ge=1)
    message: str


class ModelQualificationScope(BaseModel):
    target: str
    framework: str
    execution_profile: Literal["standard", "advanced"]
    checked_at: str
    source_fingerprint: str


class ModelQualification(BaseModel):
    status: Literal["untested", "tested"] = "untested"
    scopes: list[ModelQualificationScope] = Field(default_factory=list)
    message: str = "Task completion has not been qualified for this runtime."
