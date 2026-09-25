"""NVIDIA NIM OpenAI-compatible API provider adapter."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import replace

from backend.observability.diagnostics import emit_diagnostic_event

from ...core.config import nvidia_nim_timeout_seconds
from ...services.llm_service import (
    LLMProviderError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponseError,
    LLMTimeoutError,
    OpenAIService,
)
from ..models import CanonicalStreamEvent, ModelRequest
from ..retry import retry_after_ms_from_details, transient_retry_delay_ms
from .openai_provider import OpenAIProvider


NVIDIA_NIM_AGENT_MAX_ATTEMPTS = 4
NVIDIA_NIM_TIMEOUT_MAX_ATTEMPTS = 2
NVIDIA_NIM_MAX_RETRY_DELAY_MS = 30_000
NVIDIA_NIM_TRANSACTIONAL_STREAM_MAX_EVENTS = 50_000


class NvidiaNimService(OpenAIService):
    """OpenAI-compatible service with NVIDIA's coding-agent template."""

    def _payload(self, request: LLMRequest, *, stream: bool):
        payload = super()._payload(request, stream=stream)
        if request.metadata.get("forgex_nvidia_agent") is True:
            # NVIDIA recommends these sampling values for Nemotron 3 Super.
            payload["temperature"] = 1.0
            payload["top_p"] = 0.95
            # Agent turns need dependable tool calls more than a long hidden
            # reasoning trace. force_nonempty_content is NVIDIA's documented
            # coding-agent compatibility setting.
            payload["chat_template_kwargs"] = {
                "enable_thinking": False,
                "force_nonempty_content": True,
            }
        return payload


class NvidiaNimProvider(OpenAIProvider):
    """Route NVIDIA-hosted NIM requests through its OpenAI-compatible API."""

    provider_id = "nvidia_nim"
    service_type = NvidiaNimService

    def _service_timeout_seconds(self) -> float:
        # Never disable this bounded deadline: a cold or retired NIM must still
        # return control to ForgeX.
        return nvidia_nim_timeout_seconds()

    def _request_metadata(self, request: ModelRequest) -> dict[str, object]:
        metadata = super()._request_metadata(request)
        metadata.pop("forgex_disable_generation_timeout", None)
        if request.task_type == "planning" or metadata.get("forgex_native_tools"):
            metadata["forgex_nvidia_agent"] = True
        return metadata

    async def generate_event_stream(self, request: ModelRequest):
        """Retry complete NVIDIA Agent turns without replaying partial tools."""

        transactional = request.task_type == "planning" or bool(
            request.metadata.get("forgex_native_tools")
        )
        last_error = None
        for attempt in range(NVIDIA_NIM_AGENT_MAX_ATTEMPTS):
            emitted = False
            buffered: list[CanonicalStreamEvent] = []
            try:
                async with aclosing(
                    super().generate_event_stream(
                        replace(request, allow_fallback=False)
                    )
                ) as stream:
                    async for event in stream:
                        if transactional:
                            buffered.append(event)
                            if len(buffered) > NVIDIA_NIM_TRANSACTIONAL_STREAM_MAX_EVENTS:
                                raise LLMResponseError(
                                    "NVIDIA NIM Agent stream exceeded the safe event buffer",
                                    code="PROVIDER_STREAM_TOO_LARGE",
                                    retryable=False,
                                )
                        else:
                            emitted = True
                            yield event
                if transactional:
                    for event in buffered:
                        emitted = True
                        yield event
                return
            except (
                LLMProviderError,
                LLMRateLimitError,
                LLMResponseError,
                LLMTimeoutError,
            ) as exc:
                last_error = exc
                attempt_limit = (
                    NVIDIA_NIM_TIMEOUT_MAX_ATTEMPTS
                    if isinstance(exc, LLMTimeoutError)
                    else NVIDIA_NIM_AGENT_MAX_ATTEMPTS
                )
                retry_after_ms = retry_after_ms_from_details(
                    getattr(exc, "details", None)
                )
                if (
                    emitted
                    or attempt >= attempt_limit - 1
                    or not getattr(exc, "retryable", False)
                    or (
                        retry_after_ms is not None
                        and retry_after_ms > NVIDIA_NIM_MAX_RETRY_DELAY_MS
                    )
                ):
                    raise
                delay_ms = transient_retry_delay_ms(
                    exc,
                    attempt + 1,
                    base_delay_ms=2_000,
                    max_delay_ms=NVIDIA_NIM_MAX_RETRY_DELAY_MS,
                )
                emit_diagnostic_event(
                    component="provider",
                    event="provider.stream.retrying",
                    level="WARNING",
                    provider=self.provider_id,
                    model=request.model_id,
                    run_id=_metadata_text(request, "run_id"),
                    turn_id=_metadata_text(request, "turn_id"),
                    attempt=attempt + 1,
                    next_attempt=attempt + 2,
                    max_attempts=attempt_limit,
                    error_code=str(getattr(exc, "code", type(exc).__name__)),
                    status=getattr(exc, "status_code", None),
                    retryable=True,
                    transactional=transactional,
                    delay_ms=delay_ms,
                )
                await asyncio.sleep(delay_ms / 1000)
        if last_error is not None:
            raise last_error

    def _model_is_free(self, model_id: str) -> bool | None:
        # NVIDIA's API Catalog currently offers every hosted model under its
        # free trial tier. Keep this provider-specific instead of inferring
        # pricing from model-id suffixes used by other providers.
        return True


def _metadata_text(request: ModelRequest, key: str) -> str | None:
    value = request.metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None
