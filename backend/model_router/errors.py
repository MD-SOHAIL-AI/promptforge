from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

ModelErrorCategory = Literal[
    "configuration", "authentication", "rate_limit", "quota_exceeded",
    "timeout", "unavailable", "capability", "context_overflow",
    "content_policy", "invalid_request", "invalid_provider_output",
    "transport", "provider", "cancelled", "internal",
]


@dataclass(slots=True)
class ModelError(RuntimeError):
    message: str
    category: ModelErrorCategory = "provider"
    code: str = "MODEL_ERROR"
    retryable: bool = False
    provider_id: str | None = None
    model_id: str | None = None
    reached_provider: bool = False
    retry_after_ms: int | None = None
    request_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "category": self.category,
            "retryable": self.retryable,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "request_reached_provider": self.reached_provider,
            "retry_after_ms": self.retry_after_ms,
            "request_id": self.request_id,
            "details": dict(self.details),
        }
