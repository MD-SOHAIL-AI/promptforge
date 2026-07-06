"""Strict public schemas for provider-neutral local agent runs."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .common import APIModel


GenericRunStatus = Literal[
    "queued",
    "validating",
    "preparing_sandbox",
    "running",
    "collecting_artifacts",
    "cancelling",
    "cancelled",
    "completed",
    "blocked",
    "failed",
    "timed_out",
    "interrupted",
]


class GenericRunStartRequest(APIModel):
    provider_id: Literal["agy"]
    project_id: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
    instruction: str = Field(min_length=1, max_length=16_384, repr=False)
    timeout_seconds: int = Field(default=300, ge=10, le=900)
    idempotency_key: str = Field(min_length=8, max_length=127, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,126}$")

    @field_validator("instruction")
    @classmethod
    def instruction_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Instruction must not be blank.")
        return value


class GenericRunStartResponse(APIModel):
    run_id: str
    provider_id: Literal["agy"]
    status: GenericRunStatus
    created_at: str
    idempotent_reuse: bool
    event_stream_available: bool = True


class GenericRunDetailResponse(APIModel):
    run_id: str
    provider_id: Literal["agy"]
    status: GenericRunStatus
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    failure_code: str | None = None
    safe_failure_message: str | None = None
    progress: int = Field(ge=0, le=100)
    artifact_count: int = Field(ge=0)
    review_id: str | None = None
    changed_file_count: int = Field(default=0, ge=0)
    cancellation_requested: bool = False
    cancellation_requested_at: str | None = None
    cancellable: bool = False


class GenericRunListResponse(APIModel):
    runs: list[GenericRunDetailResponse]
    count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class GenericRunCancelResponse(APIModel):
    run_id: str
    disposition: Literal["accepted", "already_requested", "already_terminal", "not_found", "rejected"]
    status: GenericRunStatus | None = None
    cancellation_requested: bool = False


class GenericProviderCapabilities(APIModel):
    edit_files: bool
    streaming_events: bool
    cancellation: bool
    timeout: bool
    artifacts: bool
    sandbox_required: Literal[True] = True


class GenericProviderResponse(APIModel):
    provider_id: Literal["agy"]
    display_name: str
    installed: bool
    available: bool
    authentication_status: Literal["authenticated", "unauthenticated", "unknown", "not_installed", "error"]
    capabilities: GenericProviderCapabilities
    execution_enabled: bool
    disabled_reason: str | None = None


class GenericProviderListResponse(APIModel):
    providers: list[GenericProviderResponse]


class GenericAgentQaFixtureResponse(APIModel):
    qa_mode: Literal[True] = True
    fixture_state: str
    providers: list[GenericProviderResponse]
    run: GenericRunDetailResponse | None = None
    events: list[dict[str, object]] = Field(default_factory=list)
    connection_state: Literal["idle", "connecting", "connected", "reconnecting", "polling", "resync_required"] = "idle"
    submitting: bool = False
    error: str | None = None
