from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.model_router import MemoryCredentialStore, ModelRequest, ProviderRegistry, ProviderSettingsStorage
from backend.model_router.providers.openrouter import OpenRouterProvider
from backend.services.llm_service import (
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMResponseError,
)


class FallbackService:
    attempts: list[str] = []

    def __init__(self, *, api_key: str, model: str, timeout_s: float = 180, base_url: str | None = None, client: object | None = None) -> None:
        del api_key, timeout_s, base_url, client
        self.model = model
        self.attempts.append(model)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        del request
        if self.model == "openai/gpt-oss-120b:free":
            raise LLMProviderError(
                "This model is unavailable for free. The paid version is available.",
                provider=LLMProvider.OPENROUTER,
            )
        return LLMResponse("OK", LLMProvider.OPENROUTER, self.model, latency_ms=1)

    async def aclose(self) -> None:
        return None


class AlwaysFailsService(FallbackService):
    async def generate(self, request: LLMRequest) -> LLMResponse:
        del request
        raise LLMProviderError(
            "This model is unavailable for free. The paid version is available.",
            provider=LLMProvider.OPENROUTER,
        )


class InvalidFreeResponseService(FallbackService):
    async def generate(self, request: LLMRequest) -> LLMResponse:
        del request
        if self.model == "openai/gpt-oss-20b:free":
            raise LLMResponseError(
                "OPENROUTER returned an invalid response: missing choices[0].message.content",
                provider=LLMProvider.OPENROUTER,
            )
        return LLMResponse("OK", LLMProvider.OPENROUTER, self.model, latency_ms=1)


class FallbackOpenRouterProvider(OpenRouterProvider):
    service_type = FallbackService


class FailingOpenRouterProvider(OpenRouterProvider):
    service_type = AlwaysFailsService


class InvalidFreeResponseOpenRouterProvider(OpenRouterProvider):
    service_type = InvalidFreeResponseService


def registry(tmp_path: Path) -> ProviderRegistry:
    store = ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore())
    configured = ProviderRegistry(store)
    configured.configure_provider("openrouter", {"api_key": "sk-test", "enabled": True})
    return configured


def test_openrouter_free_unavailable_tries_no_cost_fallback(tmp_path: Path) -> None:
    FallbackService.attempts = []
    provider = FallbackOpenRouterProvider(registry(tmp_path))

    response = asyncio.run(
        provider.generate(
            ModelRequest(
                prompt="hello",
                task_type="code_generation",
                model_id="openai/gpt-oss-120b:free",
            )
        )
    )

    assert response.content == "OK"
    assert response.model_id == "openai/gpt-oss-20b:free"
    assert FallbackService.attempts[:2] == ["openai/gpt-oss-120b:free", "openai/gpt-oss-20b:free"]


def test_openrouter_free_invalid_response_tries_no_cost_fallback(tmp_path: Path) -> None:
    InvalidFreeResponseService.attempts = []
    provider = InvalidFreeResponseOpenRouterProvider(registry(tmp_path))

    response = asyncio.run(
        provider.generate(
            ModelRequest(
                prompt="hello",
                task_type="code_generation",
                model_id="openai/gpt-oss-20b:free",
            )
        )
    )

    assert response.content == "OK"
    assert response.model_id == "cohere/north-mini-code:free"
    assert InvalidFreeResponseService.attempts[:2] == [
        "openai/gpt-oss-20b:free",
        "cohere/north-mini-code:free",
    ]


def test_openrouter_paid_model_failure_does_not_switch_models(tmp_path: Path) -> None:
    FallbackService.attempts = []
    provider = FailingOpenRouterProvider(registry(tmp_path))

    with pytest.raises(LLMProviderError):
        asyncio.run(
            provider.generate(
                ModelRequest(
                    prompt="hello",
                    task_type="code_generation",
                    model_id="openai/gpt-oss-120b",
                )
            )
        )

    assert FallbackService.attempts == ["openai/gpt-oss-120b"]


def test_openrouter_free_fallback_preserves_original_error_when_all_free_models_fail(tmp_path: Path) -> None:
    AlwaysFailsService.attempts = []
    provider = FailingOpenRouterProvider(registry(tmp_path))

    with pytest.raises(LLMProviderError, match="This model is unavailable for free"):
        asyncio.run(
            provider.generate(
                ModelRequest(
                    prompt="hello",
                    task_type="code_generation",
                    model_id="openai/gpt-oss-120b:free",
                )
            )
        )
