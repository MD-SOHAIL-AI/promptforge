"""Shared model-router contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping
from backend.contracts.transport import TransportPolicy


AuthType = Literal["api_key", "none", "oauth_external"]
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
    provider_type: Literal["api_provider", "local_bridge_provider"] | None = None
    health_status: str = "unknown"
    last_checked_at: str | None = None
    last_error: str | None = None
    masked_api_key: str | None = None
    models_cached: int = 0
    custom: bool = False
    protocol: str = "openai-completions"
    configuration_fields: tuple[str, ...] = ()
    options_configured: bool = False

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
            "custom": self.custom,
            "protocol": self.protocol,
            "configuration_fields": list(self.configuration_fields),
            "options_configured": self.options_configured,
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
    capabilities: "ModelCapabilities | None" = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "context_window": self.context_window,
            "free": self.free,
            "local": self.local,
            "capabilities": self.capabilities.to_dict() if self.capabilities else None,
        }


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Features supported by one concrete provider/model route.

    The original six fields intentionally remain first to preserve positional
    construction used by existing provider metadata/tests.
    """

    streaming: bool = True
    json_object: bool = False
    json_schema: bool = False
    native_tools: bool = False
    reasoning: bool = False
    system_prompt: bool = True
    text_generation: bool = True
    vision: bool = False
    embeddings: bool = False
    reranking: bool = False
    context_window: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "streaming": self.streaming,
            "json_object": self.json_object,
            "json_schema": self.json_schema,
            "native_tools": self.native_tools,
            "reasoning": self.reasoning,
            "system_prompt": self.system_prompt,
            "text_generation": self.text_generation,
            "vision": self.vision,
            "embeddings": self.embeddings,
            "reranking": self.reranking,
            "context_window": self.context_window,
        }

    def satisfies(self, request: "RouteRequest") -> bool:
        if request.require_text_generation and not self.text_generation:
            return False
        if request.require_streaming and not self.streaming:
            return False
        if request.require_json_object and not self.json_object:
            return False
        if request.require_json_schema and not self.json_schema:
            return False
        if request.require_native_tools and not self.native_tools:
            return False
        if request.require_reasoning and not self.reasoning:
            return False
        if request.require_vision and not self.vision:
            return False
        if request.require_embeddings and not self.embeddings:
            return False
        if request.require_reranking and not self.reranking:
            return False
        if request.min_context_window is not None:
            if self.context_window is None or self.context_window < request.min_context_window:
                return False
        return True


RoutingPreference = Literal["balanced", "latency", "quality", "cost"]


@dataclass(frozen=True, slots=True)
class RouteRequest:
    """Per-invocation deterministic model requirements.

    ``allow_fallback`` is retained as a compatibility input only; Model
    Runtime V2 never switches providers/models automatically.
    """

    task_type: TaskType = "general_chat"
    provider_id: str | None = None
    model_id: str | None = None
    variant: str | None = None
    allow_fallback: bool = False
    local_only: bool = False
    require_text_generation: bool = True
    require_streaming: bool = False
    require_json_object: bool = False
    require_json_schema: bool = False
    require_native_tools: bool = False
    require_reasoning: bool = False
    require_vision: bool = False
    require_embeddings: bool = False
    require_reranking: bool = False
    min_context_window: int | None = None
    preference: RoutingPreference = "balanced"

    def normalized(self) -> "RouteRequest":
        return RouteRequest(
            task_type=self.task_type, provider_id=self.provider_id, model_id=self.model_id,
            variant=self.variant, allow_fallback=False, local_only=self.local_only,
            require_text_generation=self.require_text_generation,
            require_streaming=self.require_streaming,
            require_json_object=self.require_json_object,
            require_json_schema=self.require_json_schema,
            require_native_tools=self.require_native_tools,
            require_reasoning=self.require_reasoning, require_vision=self.require_vision,
            require_embeddings=self.require_embeddings, require_reranking=self.require_reranking,
            min_context_window=self.min_context_window, preference=self.preference,
        )


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    provider_id: str
    configuration: Literal["configured", "missing_credentials", "disabled"]
    runtime: Literal["unknown", "healthy", "degraded", "unavailable"] = "unknown"
    message: str | None = None

    @property
    def routeable(self) -> bool:
        return self.configuration == "configured" and self.runtime != "unavailable"


@dataclass(frozen=True, slots=True)
class RouteAttempt:
    provider_id: str
    model_id: str
    attempt: int
    reason: str = "selected"
    success: bool = False
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class RouteDecision:
    provider_id: str
    model_id: str
    attempts: tuple[RouteAttempt, ...] = ()
    fallback_used: bool = False

    def __post_init__(self) -> None:
        if self.fallback_used:
            raise ValueError("Model Runtime V2 does not permit cross-provider fallback")
        for attempt in self.attempts:
            if attempt.provider_id != self.provider_id or attempt.model_id != self.model_id:
                raise ValueError("route attempts must keep the selected provider/model")


@dataclass(frozen=True, slots=True)
class StructuredOutputSpec:
    """Provider-neutral description of a required structured model result."""

    name: str
    json_schema: Mapping[str, Any]
    strict: bool = True
    allow_prompt_fallback: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name) > 128:
            raise ValueError("structured output name is invalid")
        if not isinstance(self.json_schema, Mapping):
            raise TypeError("structured output schema must be a mapping")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "json_schema": dict(self.json_schema),
            "strict": self.strict,
            "allow_prompt_fallback": self.allow_prompt_fallback,
        }


@dataclass(slots=True)
class RequestExecutionContext:
    """Host-only state shared by every physical attempt and output repair."""

    budget: dict[str, Any] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=lambda: {"used": 0, "limit": 6})
    diagnostics: dict[str, Any] = field(default_factory=dict)
    recovery_hook: Any = None
    deadline: float | None = None
    deadline_kind: Literal["job_deadline", "turn_deadline"] = "job_deadline"
    managed: bool = True
    transport_policy: TransportPolicy | None = None


def execution_value(request, name, legacy_key, default=None):
    context = getattr(request, "execution_context", None)
    return getattr(context, name) if context is not None else request.metadata.get(legacy_key, default)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    prompt: str
    system_prompt: str | None = None
    task_type: TaskType = "general_chat"
    model_id: str | None = None
    provider_id: str | None = None
    temperature: float = 0.7
    max_tokens: int = 1024
    allow_fallback: bool = False
    local_only: bool = False
    structured_output: StructuredOutputSpec | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    execution_context: RequestExecutionContext | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Canonical response returned by every model-router provider adapter.

    ``content`` remains the compatibility spelling used by existing Chat and
    generation callers. New structured consumers should use ``text`` and the
    typed fields below rather than reading raw provider envelopes.
    """

    content: str
    provider_id: str
    model_id: str
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    raw_usage: dict[str, Any] = field(default_factory=dict)
    structured_output: dict[str, Any] | None = None
    tool_calls: tuple[dict[str, Any], ...] = ()
    finish_reason: str | None = None
    request_id: str | None = None
    reasoning: str | None = None
    truncated: bool = False

    @property
    def text(self) -> str | None:
        return self.content or None

    def token_usage(self) -> dict[str, int]:
        usage: dict[str, int] = {}
        if self.input_tokens is not None:
            usage["input_tokens"] = self.input_tokens
        if self.output_tokens is not None:
            usage["output_tokens"] = self.output_tokens
        if self.total_tokens is not None:
            usage["total_tokens"] = self.total_tokens
        return usage


CanonicalModelResponse = ModelResponse
CanonicalResponse = ModelResponse


@dataclass(frozen=True, slots=True)
class CanonicalStreamEvent:
    type: Literal["text_delta", "reasoning_delta", "tool_call_delta", "done", "error"]
    text: str | None = None
    tool_call: dict[str, Any] | None = None
    tool_call_index: int | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str | None = None
    arguments: dict[str, Any] | None = None
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ModelRoute:
    task_type: TaskType
    provider_id: str
    model_id: str
    fallback_enabled: bool = False
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
