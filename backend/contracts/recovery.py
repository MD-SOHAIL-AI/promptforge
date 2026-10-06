"""Additive model-outage projections shared by desktop and CLI clients."""
from typing import Literal
from pydantic import BaseModel


class AgentRecoveryState(BaseModel):
    phase: Literal["retrying", "running", "cancelled", "retry_wait", "paused_model_unavailable", "probe", "blocked", "ready_to_resume"]
    attempt: int = 0
    max_attempts: int = 3
    provider_id: str | None = None
    model_id: str | None = None
    error_code: str | None = None
    origin: str | None = None
    http_status: int | None = None
    request_reached_provider: bool | None = None
    retry_due_at: str | None = None
    outage_deadline_at: str | None = None


class EndpointHealth(BaseModel):
    provider_id: str
    model_id: str
    state: Literal["closed", "open", "half_open"]
    failures: int
    retry_after_ms: int
    probe_active: bool


class RecoveryActionEligibility(BaseModel):
    allowed: bool
    blocking_reason: str | None = None


class RecoveryActions(BaseModel):
    inspect: RecoveryActionEligibility
    continue_generation: RecoveryActionEligibility
    verify_locally: RecoveryActionEligibility
