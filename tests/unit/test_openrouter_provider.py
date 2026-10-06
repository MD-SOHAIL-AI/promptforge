from __future__ import annotations

import asyncio
from backend.model_router.request_recovery import agent_stream
from dataclasses import replace


def recovered_stream(provider, request):
    request = replace(request, metadata={**request.metadata, "forgex_managed_recovery": True})
    return agent_stream(request, provider.generate_event_stream)

from pathlib import Path

import pytest

from backend.model_router import MemoryCredentialStore, ModelRequest, ProviderRegistry, ProviderSettingsStorage
from backend.model_router.models import CanonicalStreamEvent
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
        if self.model == "cohere/north-mini-code:free":
            raise LLMResponseError(
                "OPENROUTER returned an invalid response: missing choices[0].message.content",
                provider=LLMProvider.OPENROUTER,
            )
        return LLMResponse("OK", LLMProvider.OPENROUTER, self.model, latency_ms=1)

    async def generate_stream(self, request: LLMRequest):
        del request
        if self.model == "cohere/north-mini-code:free":
            raise LLMResponseError(
                "OPENROUTER returned an empty response",
                provider=LLMProvider.OPENROUTER,
            )
        yield "OK"


class StalledFreeStreamService(FallbackService):
    async def generate_stream(self, request: LLMRequest):
        del request
        if self.model == "cohere/north-mini-code:free":
            await asyncio.Event().wait()
        yield "OK"


class EmptyStreamService(FallbackService):
    async def generate_stream(self, request: LLMRequest):
        del request
        if False:
            yield ""


class TransientCanonicalStreamService(FallbackService):
    failures_remaining = 0

    async def generate_event_stream(self, request: LLMRequest):
        del request
        if self.failures_remaining > 0:
            type(self).failures_remaining -= 1
            raise LLMProviderError(
                "Provider returned error",
                provider=LLMProvider.OPENROUTER,
                status_code=502,
                code="PROVIDER_UNAVAILABLE",
                retryable=True,
            )
        yield CanonicalStreamEvent("text_delta", text="OK")
        yield CanonicalStreamEvent("done", finish_reason="stop")


class PartialTransientCanonicalStreamService(FallbackService):
    failures_remaining = 0

    async def generate_event_stream(self, request: LLMRequest):
        del request
        if self.failures_remaining > 0:
            type(self).failures_remaining -= 1
            yield CanonicalStreamEvent("text_delta", text="discarded partial")
            raise LLMProviderError(
                "Upstream idle timeout exceeded",
                provider=LLMProvider.OPENROUTER,
                status_code=504,
                code="PROVIDER_UNAVAILABLE",
                retryable=True,
            )
        yield CanonicalStreamEvent("text_delta", text="complete")
        yield CanonicalStreamEvent("done", finish_reason="stop")


class FallbackOpenRouterProvider(OpenRouterProvider):
    service_type = FallbackService


class FailingOpenRouterProvider(OpenRouterProvider):
    service_type = AlwaysFailsService


class InvalidFreeResponseOpenRouterProvider(OpenRouterProvider):
    service_type = InvalidFreeResponseService


class StalledFreeStreamOpenRouterProvider(OpenRouterProvider):
    service_type = StalledFreeStreamService


class EmptyStreamOpenRouterProvider(OpenRouterProvider):
    service_type = EmptyStreamService


class TransientCanonicalOpenRouterProvider(OpenRouterProvider):
    service_type = TransientCanonicalStreamService


class PartialTransientCanonicalOpenRouterProvider(OpenRouterProvider):
    service_type = PartialTransientCanonicalStreamService


def registry(tmp_path: Path) -> ProviderRegistry:
    store = ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore())
    configured = ProviderRegistry(store)
    configured.configure_provider("openrouter", {"api_key": "sk-test", "enabled": True})
    return configured


def test_openrouter_lists_union_alpha(tmp_path: Path) -> None:
    configured = registry(tmp_path)

    assert "opencode/union-alpha" in {
        model.model_id for model in configured.list_models("openrouter")
    }


def test_openrouter_free_unavailable_does_not_switch_models(tmp_path: Path) -> None:
    FallbackService.attempts = []
    provider = FallbackOpenRouterProvider(registry(tmp_path))

    with pytest.raises(LLMProviderError):
        asyncio.run(provider.generate(ModelRequest(
            prompt="hello", task_type="code_generation", model_id="openai/gpt-oss-120b:free"
        )))
    assert FallbackService.attempts == ["openai/gpt-oss-120b:free"]


def test_openrouter_free_invalid_response_does_not_switch_models(tmp_path: Path) -> None:
    InvalidFreeResponseService.attempts = []
    provider = InvalidFreeResponseOpenRouterProvider(registry(tmp_path))

    with pytest.raises(LLMResponseError):
        asyncio.run(provider.generate(ModelRequest(
            prompt="hello", task_type="code_generation", model_id="cohere/north-mini-code:free"
        )))
    assert InvalidFreeResponseService.attempts == ["cohere/north-mini-code:free"]


def test_openrouter_stream_does_not_switch_models_before_first_chunk(tmp_path: Path) -> None:
    InvalidFreeResponseService.attempts = []
    provider = InvalidFreeResponseOpenRouterProvider(registry(tmp_path))

    async def consume() -> str:
        return "".join(
            [
                chunk
                async for chunk in provider.generate_stream(
                    ModelRequest(
                        prompt="hello",
                        task_type="general_chat",
                        model_id="cohere/north-mini-code:free",
                    )
                )
            ]
        )

    with pytest.raises(LLMResponseError):
        asyncio.run(consume())
    assert InvalidFreeResponseService.attempts == ["cohere/north-mini-code:free"]


def test_openrouter_stream_keeps_an_explicitly_selected_model(tmp_path: Path) -> None:
    InvalidFreeResponseService.attempts = []
    provider = InvalidFreeResponseOpenRouterProvider(registry(tmp_path))

    async def consume() -> str:
        return "".join(
            [
                chunk
                async for chunk in provider.generate_stream(
                    ModelRequest(
                        prompt="hello",
                        task_type="general_chat",
                        model_id="cohere/north-mini-code:free",
                        allow_fallback=False,
                    )
                )
            ]
        )

    with pytest.raises(LLMResponseError):
        asyncio.run(consume())
    assert InvalidFreeResponseService.attempts == ["cohere/north-mini-code:free"]


def test_openrouter_generate_keeps_an_explicitly_selected_model(tmp_path: Path) -> None:
    FallbackService.attempts = []
    provider = FallbackOpenRouterProvider(registry(tmp_path))

    with pytest.raises(LLMProviderError):
        asyncio.run(
            provider.generate(
                ModelRequest(
                    prompt="hello",
                    task_type="code_generation",
                    model_id="openai/gpt-oss-120b:free",
                    allow_fallback=False,
                )
            )
        )
    assert FallbackService.attempts == ["openai/gpt-oss-120b:free"]


def test_openrouter_empty_stream_recovers_with_same_selected_model(tmp_path: Path) -> None:
    EmptyStreamService.attempts = []
    provider = EmptyStreamOpenRouterProvider(registry(tmp_path))

    async def consume() -> str:
        return "".join(
            [
                chunk
                async for chunk in provider.generate_stream(
                    ModelRequest(
                        prompt="hello",
                        task_type="general_chat",
                        model_id="nvidia/nemotron-3-super-120b-a12b:free",
                        allow_fallback=False,
                    )
                )
            ]
        )

    assert asyncio.run(consume()) == "OK"
    assert EmptyStreamService.attempts == [
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
    ]


def test_openrouter_stream_retries_same_model_with_slow_first_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.model_router.providers import openrouter as openrouter_module

    monkeypatch.setattr(
        openrouter_module, "OPENROUTER_STREAM_FIRST_TOKEN_TIMEOUT_SECONDS", 0.01
    )
    from backend.model_router import request_recovery
    original_sleep = asyncio.sleep
    async def no_delay(_):
        await original_sleep(0)
    monkeypatch.setattr(request_recovery.asyncio, "sleep", no_delay)
    StalledFreeStreamService.attempts = []
    provider = StalledFreeStreamOpenRouterProvider(registry(tmp_path))

    async def consume() -> str:
        return "".join(
            [
                chunk
                async for chunk in provider.generate_stream(
                    ModelRequest(
                        prompt="hello",
                        task_type="general_chat",
                        model_id="cohere/north-mini-code:free",
                    )
                )
            ]
        )

    from backend.services.llm_service import LLMTimeoutError
    with pytest.raises(LLMTimeoutError):
        asyncio.run(consume())
    assert StalledFreeStreamService.attempts == ["cohere/north-mini-code:free"] * 6


def test_openrouter_agent_stream_retries_numeric_upstream_failure_on_same_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.model_router.providers import openrouter as openrouter_module

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr(openrouter_module.asyncio, "sleep", no_delay)
    TransientCanonicalStreamService.attempts = []
    TransientCanonicalStreamService.failures_remaining = 2
    provider = TransientCanonicalOpenRouterProvider(registry(tmp_path))
    selected = "nvidia/nemotron-3-ultra-550b-a55b:free"

    async def consume() -> list[CanonicalStreamEvent]:
        return [
            event
            async for event in recovered_stream(provider,
                ModelRequest(
                    prompt="create project",
                    task_type="planning",
                    model_id=selected,
                    allow_fallback=False,
                )
            )
        ]

    events = asyncio.run(consume())
    assert [event.type for event in events] == ["text_delta", "done"]
    assert events[0].text == "OK"
    assert TransientCanonicalStreamService.attempts == [selected, selected, selected]


def test_openrouter_agent_stream_discards_partial_attempt_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.model_router.providers import openrouter as openrouter_module

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr(openrouter_module.asyncio, "sleep", no_delay)
    PartialTransientCanonicalStreamService.attempts = []
    PartialTransientCanonicalStreamService.failures_remaining = 2
    provider = PartialTransientCanonicalOpenRouterProvider(registry(tmp_path))
    selected = "nvidia/nemotron-3-ultra-550b-a55b:free"

    async def consume() -> list[CanonicalStreamEvent]:
        return [
            event
            async for event in recovered_stream(provider,
                ModelRequest(
                    prompt="continue project",
                    task_type="planning",
                    model_id=selected,
                    allow_fallback=False,
                    metadata={"run_id": "run-504", "turn_id": "run-504:turn-4"},
                )
            )
        ]

    events = asyncio.run(consume())
    assert [event.text for event in events if event.type == "text_delta"] == [
        "complete"
    ]
    assert [event.type for event in events] == ["text_delta", "done"]
    assert PartialTransientCanonicalStreamService.attempts == [
        selected,
        selected,
        selected,
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
