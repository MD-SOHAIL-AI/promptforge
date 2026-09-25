from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping

from ..services.llm_service import LLMError
from .models import ModelRequest, ModelResponse, RouteAttempt, RouteDecision
from .providers.base import BaseModelProvider
from .resolver import ModelResolver, ResolvedModel
from .retry import RetryPolicy


class ModelRuntime:
    """Deterministic provider/model executor.

    Resolution returns exactly one model. Retries, when allowed, reuse exactly
    that provider/model; no candidate/fallback provider list exists here.
    """

    def __init__(
        self,
        resolver: ModelResolver,
        providers: Mapping[str, BaseModelProvider],
        *,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.resolver = resolver
        self.providers = providers
        self.retry_policy = retry_policy or RetryPolicy()
        self.last_decision: RouteDecision | None = None

    async def generate(self, request: ModelRequest, *, resolved: ResolvedModel | None = None) -> ModelResponse:
        resolved = resolved or self.resolver.resolve(request)
        provider = self.providers.get(resolved.provider_id)
        if provider is None:
            raise ValueError(f"No adapter installed for provider {resolved.provider_id}")
        routed = ModelRequest(
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            task_type=request.task_type,
            provider_id=resolved.provider_id,
            model_id=resolved.model_id,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            allow_fallback=False,
            local_only=request.local_only,
            structured_output=request.structured_output,
            metadata=dict(request.metadata),
        )
        attempts: list[RouteAttempt] = []
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                response = await provider.generate(routed)
                attempts.append(RouteAttempt(resolved.provider_id, resolved.model_id, attempt, "selected" if attempt == 1 else "same_model_retry", True))
                self.last_decision = RouteDecision(resolved.provider_id, resolved.model_id, tuple(attempts), False)
                return response
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempts.append(RouteAttempt(resolved.provider_id, resolved.model_id, attempt, "selected" if attempt == 1 else "same_model_retry", False, str(getattr(exc, "code", type(exc).__name__))))
                decision = self.retry_policy.decide(exc, attempt)
                if not decision.retry:
                    self.last_decision = RouteDecision(resolved.provider_id, resolved.model_id, tuple(attempts), False)
                    raise
                if decision.delay_ms:
                    await asyncio.sleep(decision.delay_ms / 1000)
        raise RuntimeError("unreachable")
