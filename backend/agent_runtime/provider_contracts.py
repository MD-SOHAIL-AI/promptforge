"""Provider-neutral contracts and safe API-provider classifications."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol, runtime_checkable

from .tool_contracts import ToolPlan


class ApiProviderClassification(str, Enum):
    PASS = "API_PROVIDER_PASS"
    KEY_MISSING = "API_PROVIDER_KEY_MISSING"
    DISABLED = "API_PROVIDER_DISABLED"
    MODEL_ERROR = "API_PROVIDER_MODEL_ERROR"
    INVALID_JSON = "API_PROVIDER_INVALID_JSON"
    SCHEMA_INVALID = "API_PROVIDER_SCHEMA_INVALID"
    POLICY_DENIED = "API_PROVIDER_POLICY_DENIED"
    TOOL_VALIDATION_FAILED = "API_PROVIDER_TOOL_VALIDATION_FAILED"
    PATH_UNSAFE = "API_PROVIDER_PATH_UNSAFE"
    CONTENT_INVALID = "API_PROVIDER_CONTENT_INVALID"
    EXTRA_CHANGES = "API_PROVIDER_EXTRA_CHANGES"
    NO_CHANGES = "API_PROVIDER_NO_CHANGES"
    REVIEW_CREATED = "API_PROVIDER_REVIEW_CREATED"
    TIMEOUT = "API_PROVIDER_TIMEOUT"
    RATE_LIMITED = "API_PROVIDER_RATE_LIMITED"
    UNSAFE_ABORTED = "API_PROVIDER_UNSAFE_ABORTED"
    UNKNOWN_SAFE_FAILURE = "API_PROVIDER_UNKNOWN_SAFE_FAILURE"


class ApiProviderError(ValueError):
    def __init__(self, classification: ApiProviderClassification, safe_code: str) -> None:
        self.classification = classification
        self.safe_code = safe_code
        super().__init__(safe_code)


@runtime_checkable
class ApiBackedProvider(Protocol):
    provider_id: str
    model_id: str
    supports_structured_tool_calls: bool
    supports_streaming: bool
    supports_json_schema: bool
    max_input_tokens: int
    max_output_tokens: int

    def request_plan(
        self,
        *,
        task: str,
        sandbox_manifest: Mapping[str, object],
        allowed_tools: list[str],
        policy: Mapping[str, object],
        run_id: str,
    ) -> ToolPlan: ...


@dataclass(frozen=True, slots=True)
class DisabledApiProvider:
    provider_id: str
    model_id: str = "disabled"
    supports_structured_tool_calls: bool = False
    supports_streaming: bool = False
    supports_json_schema: bool = False
    max_input_tokens: int = 0
    max_output_tokens: int = 0
    enabled: bool = False
    production_eligible: bool = False

    def request_plan(
        self,
        *,
        task: str,
        sandbox_manifest: Mapping[str, object],
        allowed_tools: list[str],
        policy: Mapping[str, object],
        run_id: str,
    ) -> ToolPlan:
        del task, sandbox_manifest, allowed_tools, policy, run_id
        raise ApiProviderError(ApiProviderClassification.DISABLED, "provider_api_design_only")


def disabled_api_providers() -> dict[str, DisabledApiProvider]:
    return {
        provider_id: DisabledApiProvider(provider_id=provider_id)
        for provider_id in ("anthropic_api", "google_api", "local_model_api")
    }
