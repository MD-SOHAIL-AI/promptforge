"""OpenAI API provider adapter."""

from __future__ import annotations

import time
from contextlib import aclosing

import httpx

from ...core.config import DEFAULT_LLM_TIMEOUT_SECONDS
from ...services.llm_service import LLMError, LLMRequest, OpenAIService
from ..models import ModelCapabilities, ModelInfo, ModelRequest, ModelResponse, ProviderHealth
from .base import BaseModelProvider, elapsed_ms


class OpenAIProvider(BaseModelProvider):
    provider_id = "openai"
    service_type = OpenAIService

    async def generate_stream(self, request: ModelRequest):
        diagnostics = _begin_physical_attempt(request)
        service = self._service(model_id=request.model_id)
        try:
            async with aclosing(
                service.generate_stream(
                    LLMRequest(
                        prompt=request.prompt,
                        system_prompt=request.system_prompt,
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                        metadata=self._request_metadata(request),
                    )
                )
            ) as stream:
                async for chunk in stream:
                    yield chunk
            _complete_physical_attempt(diagnostics)
        except Exception as exc:
            _fail_physical_attempt(diagnostics, exc)
            raise
        finally:
            await service.aclose()

    async def generate_event_stream(self, request: ModelRequest):
        diagnostics = _begin_physical_attempt(request)
        service = self._service(model_id=request.model_id)
        try:
            async with aclosing(
                service.generate_event_stream(
                    LLMRequest(
                        prompt=request.prompt,
                        system_prompt=request.system_prompt,
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                        metadata=self._request_metadata(request),
                    )
                )
            ) as stream:
                async for event in stream:
                    yield event
            _complete_physical_attempt(diagnostics)
        except Exception as exc:
            _fail_physical_attempt(diagnostics, exc)
            raise
        finally:
            await service.aclose()

    async def health(self) -> ProviderHealth:
        missing = self._configured_health()
        if missing is not None:
            return missing
        started = time.monotonic()
        try:
            service = self._service()
            try:
                ok = await service.health_check()
            finally:
                await service.aclose()
        except Exception as exc:
            return ProviderHealth(
                self.provider_id,
                False,
                "error",
                elapsed_ms(started),
                type(exc).__name__,
                str(exc),
            )
        return ProviderHealth(
            self.provider_id,
            ok,
            "connected" if ok else "error",
            elapsed_ms(started),
            None if ok else "HEALTH_CHECK_FAILED",
        )

    async def list_models(self) -> list[ModelInfo]:
        api_key = self.registry.api_key(self.provider_id)
        base_url = self.registry.base_url(self.provider_id)
        if not api_key or not base_url:
            raise ValueError("Configure the provider API key and base URL to import models.")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            data = response.json()
        raw_models = data.get("data") if isinstance(data, dict) else None
        if not isinstance(raw_models, list):
            raise ValueError("Provider returned an invalid model list.")
        models: list[ModelInfo] = []
        for item in raw_models:
            model_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(model_id, str) and model_id.strip():
                models.append(
                    ModelInfo(
                        provider_id=self.provider_id,
                        model_id=model_id,
                        display_name=model_id.split("/")[-1],
                        free=self._model_is_free(model_id),
                        capabilities=_discovered_capabilities(item),
                    )
                )
        if not models:
            raise ValueError("Provider returned no usable models.")
        return models

    def _model_is_free(self, model_id: str) -> bool | None:
        return ":free" in model_id

    async def generate(self, request: ModelRequest) -> ModelResponse:
        diagnostics = _begin_physical_attempt(request)
        service = self._service(model_id=request.model_id)
        try:
            response = await service.generate(
                LLMRequest(
                    prompt=request.prompt,
                    system_prompt=request.system_prompt,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    metadata=self._request_metadata(request),
                )
            )
            _complete_physical_attempt(diagnostics, request_id=response.request_id)
        except Exception as exc:
            _fail_physical_attempt(diagnostics, exc)
            raise
        finally:
            await service.aclose()
        return ModelResponse(
            content=response.content,
            provider_id=self.provider_id,
            model_id=response.model,
            latency_ms=response.latency_ms,
            input_tokens=response.token_usage.get("input_tokens"),
            output_tokens=response.token_usage.get("output_tokens"),
            total_tokens=response.token_usage.get("total_tokens"),
            raw_usage=dict(response.token_usage),
            structured_output=(
                dict(response.structured_output)
                if response.structured_output is not None
                else None
            ),
            tool_calls=tuple(dict(call) for call in response.tool_calls),
            finish_reason=response.finish_reason,
            request_id=response.request_id,
            reasoning=response.reasoning,
            truncated=response.truncated,
        )

    def _service(self, model_id: str | None = None) -> OpenAIService:
        api_key = self.registry.api_key(self.provider_id)
        if not api_key:
            provider = self.registry.provider(self.provider_id)
            raise ValueError(
                f"{provider.display_name} is not configured. Add your API key in Models."
            )
        return self.service_type(
            api_key=api_key,
            model=model_id or self.registry.default_model(self.provider_id),
            base_url=self.registry.base_url(self.provider_id),
            timeout_s=self._service_timeout_seconds(),
        )

    def _service_timeout_seconds(self) -> float:
        return DEFAULT_LLM_TIMEOUT_SECONDS

    def _request_metadata(self, request: ModelRequest) -> dict[str, object]:
        metadata = dict(request.metadata)
        spec = request.structured_output
        if spec is None:
            return metadata
        capabilities = self.registry.model_capabilities(
            self.provider_id,
            request.model_id or self.registry.default_model(self.provider_id),
        )
        mode = (
            "json_schema"
            if capabilities.json_schema
            else "native_tools"
            if capabilities.native_tools
            else "prompted_json"
        )
        metadata["forgex_structured_output"] = {
            **spec.to_dict(),
            "mode": mode,
        }
        return metadata


def _discovered_capabilities(item: dict[str, object]) -> ModelCapabilities | None:
    supported = item.get("supported_parameters")
    if not isinstance(supported, list):
        return None
    parameters = {str(value).casefold() for value in supported}
    return ModelCapabilities(
        streaming=True,
        json_object="response_format" in parameters,
        json_schema="structured_outputs" in parameters,
        native_tools="tools" in parameters,
        reasoning=bool({"reasoning", "include_reasoning"} & parameters),
        system_prompt=True,
    )


def _attempt_diagnostics(request: ModelRequest) -> dict[str, object] | None:
    value = request.metadata.get("forgex_attempt_diagnostics")
    return value if isinstance(value, dict) else None


def _begin_physical_attempt(request: ModelRequest) -> dict[str, object] | None:
    diagnostics = _attempt_diagnostics(request)
    if diagnostics is not None:
        current = diagnostics.get("physical_request_count")
        count = current if isinstance(current, int) and not isinstance(current, bool) else 0
        diagnostics["physical_request_count"] = count + 1
    return diagnostics


def _complete_physical_attempt(
    diagnostics: dict[str, object] | None,
    *,
    request_id: str | None = None,
) -> None:
    if diagnostics is None:
        return
    diagnostics["request_reached_provider"] = True
    diagnostics["http_status"] = 200
    diagnostics["last_attempt_succeeded"] = True
    if isinstance(request_id, str) and request_id.strip():
        diagnostics["provider_request_id"] = request_id.strip()[:128]


def _fail_physical_attempt(
    diagnostics: dict[str, object] | None,
    exc: Exception,
) -> None:
    if diagnostics is None:
        return
    diagnostics["last_attempt_succeeded"] = False
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        diagnostics["http_status"] = status
        diagnostics["request_reached_provider"] = True
    if isinstance(exc, LLMError):
        retry_after = exc.details.get("retry_after_ms")
        if isinstance(retry_after, int) and not isinstance(retry_after, bool) and retry_after >= 0:
            diagnostics["retry_after_ms"] = retry_after
        request_id = exc.details.get("provider_request_id")
        if isinstance(request_id, str) and request_id.strip():
            diagnostics["provider_request_id"] = request_id.strip()[:128]
        reached = exc.details.get("request_reached_provider")
        if isinstance(reached, bool):
            diagnostics["request_reached_provider"] = reached
