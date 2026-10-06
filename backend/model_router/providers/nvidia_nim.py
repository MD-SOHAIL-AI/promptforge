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

    def _model_is_free(self, model_id: str) -> bool | None:
        # NVIDIA's API Catalog currently offers every hosted model under its
        # free trial tier. Keep this provider-specific instead of inferring
        # pricing from model-id suffixes used by other providers.
        return True


def _metadata_text(request: ModelRequest, key: str) -> str | None:
    value = request.metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None
