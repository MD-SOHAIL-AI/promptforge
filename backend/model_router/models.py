"""Shared model-router contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


AuthType = Literal["api_key", "none"]
TaskType = Literal[
    "code_generation",
    "planning",
    "debugging",
    "documentation",
    "serial_analysis",
    "hardware_analysis",
    "testing",
    "review",
    "general_chat",
]


@dataclass(frozen=True, slots=True)
class ModelProvider:
    provider_id: str
    display_name: str
    auth_type: AuthType
    local: bool
    default_model: str
    base_url: str | None = None
    enabled: bool = False
    configured: bool = False
    api_key_masked: str | None = None
    provider_type: Literal["api_provider"] | None = None
    health_status: str = "unknown"
    last_checked_at: str | None = None
    last_error: str | None = None
    masked_api_key: str | None = None
    models_cached: int = 0

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "configured": self.configured,
            "auth_type": self.auth_type,
            "local": self.local,
            "default_model": self.default_model,
            "health_status": self.health_status,
            "last_checked_at": self.last_checked_at,
            "last_error": self.last_error,
            "models_cached": self.models_cached,
            "credential_configured": self.configured and self.auth_type == "api_key",
        }
        if self.base_url:
            data["base_url"] = self.base_url
        if self.provider_type:
            data["provider_type"] = self.provider_type
        return data


@dataclass(frozen=True, slots=True)
class ModelInfo:
    provider_id: str
    model_id: str
    display_name: str
    context_window: int | None = None
    free: bool | None = None
    local: bool = False
    supported_parameters: tuple[str, ...] = ()

    @property
    def agent_compatible(self) -> bool | None:
        if not self.supported_parameters:
            return None
        supported = set(self.supported_parameters)
        return bool(
            {"response_format", "structured_outputs"} & supported
            or {"tools", "tool_choice"}.issubset(supported)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "context_window": self.context_window,
            "free": self.free,
            "local": self.local,
            "supported_parameters": list(self.supported_parameters),
            "agent_compatible": self.agent_compatible,
        }


@dataclass(frozen=True, slots=True)
class ModelRequest:
    prompt: str
    system_prompt: str | None = None
    task_type: TaskType = "general_chat"
    model_id: str | None = None
    provider_id: str | None = None
    temperature: float = 0.7
    max_tokens: int = 1024
    allow_fallback: bool = True
    local_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str
    provider_id: str
    model_id: str
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    raw_usage: dict[str, Any] = field(default_factory=dict)

    def token_usage(self) -> dict[str, int]:
        usage: dict[str, int] = {}
        if self.input_tokens is not None:
            usage["input_tokens"] = self.input_tokens
        if self.output_tokens is not None:
            usage["output_tokens"] = self.output_tokens
        if self.total_tokens is not None:
            usage["total_tokens"] = self.total_tokens
        return usage


@dataclass(frozen=True, slots=True)
class ModelRoute:
    task_type: TaskType
    provider_id: str
    model_id: str
    fallback_enabled: bool = True
    fallback_provider_id: str | None = None
    local_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "fallback_enabled": self.fallback_enabled,
            "fallback_provider_id": self.fallback_provider_id,
            "local_only": self.local_only,
        }


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider_id: str
    ok: bool
    status: str
    latency_ms: int | None = None
    error_code: str | None = None
    message: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "ok": self.ok,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "error_code": self.error_code,
            "message": self.message,
            "checked_at": self.checked_at.isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True, slots=True)
class UsageRecord:
    provider_id: str
    model_id: str
    task_type: str
    latency_ms: int
    success: bool
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error_code: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "task_type": self.task_type,
            "latency_ms": self.latency_ms,
            "success": self.success,
            "error_code": self.error_code,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost": self.estimated_cost,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
        }
