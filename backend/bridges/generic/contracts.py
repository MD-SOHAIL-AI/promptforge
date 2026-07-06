"""Typed provider boundary. This module performs no provider routing or execution."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    BridgeCancellationResult,
    BridgeCapabilities,
    BridgeDetectionResult,
    BridgeRunContext,
    BridgeRunRequest,
    BridgeRunResult,
    BridgeValidationResult,
)


@runtime_checkable
class BridgeProvider(Protocol):
    """A provider resolves its own executable and receives only a verified sandbox context."""

    provider_id: str
    environment_allowlist: frozenset[str]

    def capabilities(self) -> BridgeCapabilities: ...

    async def detect(self) -> BridgeDetectionResult: ...

    async def validate(self, request: BridgeRunRequest) -> BridgeValidationResult: ...

    async def start(self, context: BridgeRunContext) -> BridgeRunResult: ...

    async def cancel(self, run_id: str, reason_code: str = "user_requested") -> BridgeCancellationResult: ...
