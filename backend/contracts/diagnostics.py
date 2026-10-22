"""One redacted provider-attempt contract from transport to durable clients."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class BudgetFailure(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, allow_inf_nan=False)
    dimension: Literal["requests", "output_tokens", "input_bytes", "elapsed_seconds", "tool_calls_used"]
    consumed: int | float = Field(ge=0)
    limit: int | float = Field(ge=0)
    attempted_charge: int | float = Field(ge=0)
    origin: str = Field(default="job_budget", max_length=128)
    phase: str | None = Field(default=None, max_length=128)
    remaining_job_seconds: float | None = Field(default=None, ge=0)


class AttemptDiagnostics(BaseModel):
    """Counts and provider identifiers only; never prompt or reasoning content."""
    model_config = ConfigDict(extra="ignore", strict=True, allow_inf_nan=False)
    requested_output_tokens: int | None = Field(default=None, ge=0)
    effective_output_tokens: int | None = Field(default=None, ge=0)
    finish_reason: str | None = Field(default=None, max_length=64)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    text_event_count: int = Field(default=0, ge=0)
    reasoning_event_count: int = Field(default=0, ge=0)
    tool_event_count: int = Field(default=0, ge=0)
    worker_exit_code: int | None = None
    worker_cleanup_incomplete: bool = False
    worker_phase: Literal["spawn", "input", "response"] | None = None
    worker_failure_kind: Literal["memory_limit", "invalid_runtime_options", "abnormal_exit"] | None = None
    physical_attempt_id: str | None = Field(default=None, max_length=256)
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_request_id: str | None = Field(default=None, max_length=256)
    completion_received: bool = False
    stream_event_count: int = Field(default=0, ge=0)
    first_event_ms: int | None = Field(default=None, ge=0)
    last_event_ms: int | None = Field(default=None, ge=0)
    timeout_stage: str | None = Field(default=None, max_length=128)
    failure_shape: Literal["invalid_stream_event"] | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class ToolArgumentIssue(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    field: str = Field(max_length=256, pattern=r"^checks(?:\[\d+\](?:\.(?:id|status|evidence)(?:\[\d+\](?:\.(?:path|quote|start_line|end_line))?)?)?)?$")
    constraint: Literal["type", "count", "fields", "string", "enum", "line_order", "line_span"]
    expected: str = Field(min_length=1, max_length=256)


class ProviderDiagnostics(AttemptDiagnostics):
    model_config = ConfigDict(extra="ignore", strict=True, allow_inf_nan=False)
    outbound_request_count: int = Field(default=0, ge=0)
    physical_request_count: int = Field(default=0, ge=0)
    request_reached_provider: bool = False
    request_dispatched: bool = False
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_request_id: str | None = Field(default=None, max_length=256)
    retry_after_ms: int | None = Field(default=None, ge=0)
    last_attempt_succeeded: bool | None = None
    safe_error_code: str | None = Field(default=None, max_length=128)
    toolplan_normalized: bool | None = None
    toolplan_repair_attempted: bool | None = None
    last_turn: int | None = Field(default=None, ge=0)
    response_mode: str | None = Field(default=None, max_length=128)
    last_failure_code: str | None = Field(default=None, max_length=128)
    last_repair_outcome: str | None = Field(default=None, max_length=128)
    last_parse_reason: str | None = Field(default=None, max_length=512)
    last_argument_issue: ToolArgumentIssue | None = None
    last_failure_origin: str | None = Field(default=None, max_length=128)
    budget_persistence_cause: str | None = Field(default=None, max_length=128)
    last_response_shape: str | None = Field(default=None, max_length=128)
    last_response_tool_count: int | None = Field(default=None, ge=0)
    stream_fallback_used: bool = False
    initial_failure_code: str | None = Field(default=None, max_length=128)
    recovery_failure_code: str | None = Field(default=None, max_length=128)
    physical_attempt_id: str | None = Field(default=None, max_length=256)
    response_headers_received: bool = False
    first_event_ms: int | None = Field(default=None, ge=0)
    last_event_ms: int | None = Field(default=None, ge=0)
    stream_event_count: int = Field(default=0, ge=0)
    completion_received: bool = False
    timeout_stage: str | None = Field(default=None, max_length=128)
    duration_ms: int | None = Field(default=None, ge=0)
    budget_failure: BudgetFailure | None = None
    repair_skip_reason: Literal["physical_attempt_budget", "request_budget", "output_budget", "time_budget"] | None = None
    initial_attempt_diagnostics: AttemptDiagnostics | None = None
    repair_attempt_diagnostics: AttemptDiagnostics | None = None


def safe_provider_diagnostics(value: object) -> dict[str, object]:
    """Ignore unknown/private keys and discard malformed fields individually."""
    if not isinstance(value, Mapping):
        return {}
    candidate = {key: value[key] for key in ProviderDiagnostics.model_fields if key in value}
    while candidate:
        try:
            return ProviderDiagnostics.model_validate(candidate).model_dump(exclude_unset=True)
        except ValidationError as error:
            bad = {item["loc"][0] for item in error.errors() if item["loc"]}
            if not bad:
                return {}
            candidate = {key: item for key, item in candidate.items() if key not in bad}
    return {}


def apply_provider_diagnostics(run: object, value: object) -> None:
    for name, item in safe_provider_diagnostics(value).items():
        if hasattr(run, name):
            setattr(run, name, item)


def run_provider_diagnostics(run: object) -> dict[str, object]:
    return safe_provider_diagnostics({name: getattr(run, name) for name in ProviderDiagnostics.model_fields if hasattr(run, name)})
