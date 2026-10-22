from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping

from ..services.llm_service import LLMError, LLMTimeoutError, LLMProvider
from .transport_policy import request_transport_policy
from .models import ModelRequest, ModelResponse, RouteAttempt, RouteDecision, execution_value
from .providers.base import BaseModelProvider
from .resolver import ModelResolver, ResolvedModel
from .retry import RetryPolicy
from .request_recovery import managed, retry_wait


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
        attempt_slot=None,
    ) -> None:
        self.resolver = resolver
        self.providers = providers
        self.retry_policy = retry_policy or RetryPolicy()
        self.last_decision: RouteDecision | None = None
        self.attempt_slot = attempt_slot

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
            execution_context=request.execution_context,
        )
        # A logical request has one absolute timeout, including provider admission,
        # physical attempts and retry backoff. A missing parent deadline must not
        # permit six unbounded expensive attempts.
        parent_deadline = execution_value(routed, "deadline", "forgex_turn_deadline")
        deadline = time.monotonic() + request_transport_policy(routed).total_seconds
        if isinstance(parent_deadline, (int, float)) and not isinstance(parent_deadline, bool):
            deadline = min(deadline, parent_deadline)
        try:
            async with asyncio.timeout_at(deadline):
                attempts: list[RouteAttempt] = []
                for attempt in range(1, self.retry_policy.max_attempts + 1):
                    try:
                        if self.attempt_slot is None:
                            response = await provider.generate(routed)
                        else:
                            async with self.attempt_slot(routed):
                                response = await provider.generate(routed)
                        attempts.append(RouteAttempt(resolved.provider_id, resolved.model_id, attempt, "selected" if attempt == 1 else "same_model_retry", True))
                        self.last_decision = RouteDecision(resolved.provider_id, resolved.model_id, tuple(attempts), False)
                        return response
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        attempts.append(RouteAttempt(resolved.provider_id, resolved.model_id, attempt, "selected" if attempt == 1 else "same_model_retry", False, str(getattr(exc, "code", type(exc).__name__))))
                        if managed(routed):
                            if await retry_wait(routed, exc, attempt):
                                continue
                            self.last_decision = RouteDecision(resolved.provider_id, resolved.model_id, tuple(attempts), False)
                            raise
                        decision = self.retry_policy.decide(exc, attempt)
                        retry_deadline = deadline
                        state = execution_value(routed, "attempts", "forgex_request_attempts", {})
                        exhausted = bool(state) and (int(state.get("used", 0)) >= int(state.get("limit", 6)) or attempt >= int(state.get("limit", 6)))
                        if not decision.retry or exhausted or isinstance(deadline, (float, int)) and time.monotonic() + decision.delay_ms / 1000 >= retry_deadline:
                            self.last_decision = RouteDecision(resolved.provider_id, resolved.model_id, tuple(attempts), False)
                            raise
                        if decision.delay_ms:
                            await asyncio.sleep(decision.delay_ms / 1000)
                raise RuntimeError("unreachable")
        except TimeoutError as exc:
            raise LLMTimeoutError(
                "The model request exceeded its overall deadline, including retries.",
                provider=LLMProvider.OPENROUTER,
                code="PROVIDER_REQUEST_DEADLINE_EXCEEDED", retryable=False,
                details={"origin": "request_deadline", "timeout_stage": "job_deadline"},
            ) from exc
