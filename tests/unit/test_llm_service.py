from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError
from typing import Any

import httpx
import pytest

from backend.services.llm_service import (
    AnthropicService,
    GeminiService,
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMResponseError,
    LLMService,
    LLMTimeoutError,
    OpenAIService,
    OpenRouterService,
)


def mock_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro: Any) -> Any:
    return asyncio.run(coro)


async def collect(service: LLMService, request: LLMRequest) -> str:
    return "".join([chunk async for chunk in service.generate_stream(request)])


def test_provider_values_are_stable_strings() -> None:
    assert [provider.value for provider in LLMProvider] == [
        "OPENAI",
        "ANTHROPIC",
        "GEMINI",
        "OPENROUTER",
        "OLLAMA",
        "LMSTUDIO",
    ]
    assert isinstance(LLMProvider.OPENAI, str)


def test_service_is_abstract() -> None:
    with pytest.raises(TypeError):
        LLMService()  # type: ignore[abstract]


def test_request_defaults_validation_immutability_and_round_trip() -> None:
    source = {"trace": {"tags": ["test"]}}
    request = LLMRequest(prompt="Hello", metadata=source)
    source["trace"]["tags"].append("changed")

    assert request.system_prompt is None
    assert request.temperature == 0.7
    assert request.max_tokens == 1024
    assert request.metadata["trace"]["tags"] == ("test",)
    assert LLMRequest.from_dict(request.to_dict()) == request
    with pytest.raises(FrozenInstanceError):
        request.prompt = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        request.metadata["new"] = True  # type: ignore[index]


@pytest.mark.parametrize("prompt", [None, 1, "", " ", "bad\x00prompt"])
def test_request_rejects_invalid_prompt(prompt: object) -> None:
    with pytest.raises(ValueError, match="prompt"):
        LLMRequest(prompt=prompt)  # type: ignore[arg-type]


@pytest.mark.parametrize("temperature", [-0.1, 2.1, float("nan"), True, "1"])
def test_request_rejects_invalid_temperature(temperature: object) -> None:
    with pytest.raises(ValueError, match="temperature"):
        LLMRequest(prompt="Hello", temperature=temperature)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_tokens", [0, -1, 1.5, True, "100"])
def test_request_rejects_invalid_max_tokens(max_tokens: object) -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        LLMRequest(prompt="Hello", max_tokens=max_tokens)  # type: ignore[arg-type]


def test_response_validation_immutability_and_round_trip() -> None:
    response = LLMResponse(
        content="Hello",
        provider=LLMProvider.OPENAI,
        model="model-a",
        token_usage={"input_tokens": 2, "output_tokens": 1},
        latency_ms=5,
    )

    assert LLMResponse.from_dict(response.to_dict()) == response
    with pytest.raises(FrozenInstanceError):
        response.content = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        response.token_usage["input_tokens"] = 3  # type: ignore[index]


@pytest.mark.parametrize("usage", [{"input": -1}, {"input": 1.5}, {1: 2}])
def test_response_rejects_invalid_token_usage(usage: object) -> None:
    with pytest.raises(ValueError, match="token_usage"):
        LLMResponse(
            content="Hello",
            provider=LLMProvider.OPENAI,
            model="model",
            token_usage=usage,  # type: ignore[arg-type]
        )


def test_openai_generate_builds_request_and_normalizes_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "gpt-test-2026",
                "choices": [{"message": {"content": "OpenAI answer"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                },
            },
        )

    client = mock_client(handler)
    service = OpenAIService(api_key="secret", model="gpt-test", client=client)
    response = run(
        service.generate(
            LLMRequest(
                prompt="Question",
                system_prompt="Be concise",
                temperature=0.2,
                max_tokens=50,
            )
        )
    )

    assert seen["request"].url.path == "/v1/chat/completions"
    assert seen["request"].headers["authorization"] == "Bearer secret"
    assert seen["payload"] == {
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "Be concise"},
            {"role": "user", "content": "Question"},
        ],
        "temperature": 0.2,
        "max_tokens": 50,
        "stream": False,
    }
    assert response.content == "OpenAI answer"
    assert response.provider is LLMProvider.OPENAI
    assert response.model == "gpt-test-2026"
    assert response.token_usage == {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
    }
    run(client.aclose())


def test_anthropic_generate_builds_request_and_normalizes_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "claude-test-2026",
                "content": [
                    {"type": "text", "text": "Anthropic "},
                    {"type": "text", "text": "answer"},
                ],
                "usage": {"input_tokens": 8, "output_tokens": 3},
            },
        )

    client = mock_client(handler)
    service = AnthropicService(
        api_key="secret", model="claude-test", client=client
    )
    response = run(
        service.generate(
            LLMRequest(prompt="Question", system_prompt="Be concise")
        )
    )

    assert seen["request"].url.path == "/v1/messages"
    assert seen["request"].headers["x-api-key"] == "secret"
    assert seen["request"].headers["anthropic-version"] == "2023-06-01"
    assert seen["payload"]["system"] == "Be concise"
    assert seen["payload"]["stream"] is False
    assert response.content == "Anthropic answer"
    assert response.provider is LLMProvider.ANTHROPIC
    assert response.token_usage["total_tokens"] == 11
    run(client.aclose())


def test_gemini_generate_builds_request_and_normalizes_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "modelVersion": "gemini-test-002",
                "candidates": [
                    {"content": {"parts": [{"text": "Gemini answer"}]}}
                ],
                "usageMetadata": {
                    "promptTokenCount": 7,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 9,
                },
            },
        )

    client = mock_client(handler)
    service = GeminiService(api_key="secret", model="gemini-test", client=client)
    response = run(
        service.generate(
            LLMRequest(prompt="Question", system_prompt="Be concise")
        )
    )

    assert seen["request"].url.path.endswith(
        "/models/gemini-test:generateContent"
    )
    assert seen["request"].url.params["key"] == "secret"
    assert seen["payload"]["systemInstruction"] == {
        "parts": [{"text": "Be concise"}]
    }
    assert response.content == "Gemini answer"
    assert response.provider is LLMProvider.GEMINI
    assert response.token_usage["total_tokens"] == 9
    run(client.aclose())


def test_openrouter_headers_and_openai_compatible_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200,
            json={
                "model": "vendor/model",
                "choices": [{"message": {"content": "Router answer"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2},
            },
        )

    client = mock_client(handler)
    service = OpenRouterService(
        api_key="secret",
        model="vendor/model",
        client=client,
        site_url="https://promptforge.example",
        app_name="PromptForge",
    )
    response = run(service.generate(LLMRequest(prompt="Question")))

    assert seen["request"].headers["http-referer"] == (
        "https://promptforge.example"
    )
    assert seen["request"].headers["x-openrouter-title"] == "PromptForge"
    assert response.provider is LLMProvider.OPENROUTER
    assert response.content == "Router answer"
    run(client.aclose())


def test_openrouter_response_accepts_openai_content_parts() -> None:
    client = mock_client(
        lambda request: httpx.Response(
            200,
            json={
                "model": "vendor/model",
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "Router "},
                                {"type": "output_text", "text": "answer"},
                            ]
                        }
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2},
            },
        )
    )
    service = OpenRouterService(
        api_key="secret",
        model="vendor/model",
        client=client,
    )

    response = run(service.generate(LLMRequest(prompt="Question")))

    assert response.content == "Router answer"
    assert response.model == "vendor/model"
    run(client.aclose())


def test_openai_compatible_response_accepts_legacy_choice_text() -> None:
    client = mock_client(
        lambda request: httpx.Response(
            200,
            json={
                "model": "legacy/model",
                "choices": [{"text": "Legacy answer"}],
            },
        )
    )
    service = OpenAIService(
        api_key="secret",
        model="legacy/model",
        client=client,
    )

    response = run(service.generate(LLMRequest(prompt="Question")))

    assert response.content == "Legacy answer"
    run(client.aclose())


def test_openai_compatible_embedded_error_raises_provider_error() -> None:
    client = mock_client(
        lambda request: httpx.Response(
            200,
            json={
                "error": {
                    "message": "model is temporarily unavailable",
                    "code": "model_unavailable",
                }
            },
        )
    )
    service = OpenRouterService(
        api_key="secret",
        model="vendor/model",
        client=client,
    )

    with pytest.raises(LLMProviderError, match="temporarily unavailable") as exc_info:
        run(service.generate(LLMRequest(prompt="Question")))

    assert exc_info.value.code == "model_unavailable"
    assert exc_info.value.retryable is True
    run(client.aclose())


@pytest.mark.parametrize(
    ("service_factory", "events", "expected"),
    [
        (
            lambda client: OpenAIService(
                api_key="key", model="model", client=client
            ),
            [
                {"choices": [{"delta": {"content": "Open"}}]},
                {"choices": [{"delta": {"content": "AI"}}]},
            ],
            "OpenAI",
        ),
        (
            lambda client: AnthropicService(
                api_key="key", model="model", client=client
            ),
            [
                {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Anthro"},
                },
                {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "pic"},
                },
            ],
            "Anthropic",
        ),
        (
            lambda client: GeminiService(
                api_key="key", model="model", client=client
            ),
            [
                {"candidates": [{"content": {"parts": [{"text": "Gem"}]}}]},
                {"candidates": [{"content": {"parts": [{"text": "ini"}]}}]},
            ],
            "Gemini",
        ),
        (
            lambda client: OpenRouterService(
                api_key="key", model="model", client=client
            ),
            [
                {"choices": [{"delta": {"content": "Open"}}]},
                {"choices": [{"delta": {"content": "Router"}}]},
            ],
            "OpenRouter",
        ),
    ],
)
def test_provider_streaming(
    service_factory: Any,
    events: list[dict[str, Any]],
    expected: str,
) -> None:
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    body += "data: [DONE]\n\n"
    client = mock_client(lambda request: httpx.Response(200, text=body))
    service = service_factory(client)

    assert run(collect(service, LLMRequest(prompt="Question"))) == expected
    run(client.aclose())


@pytest.mark.parametrize(
    "service_factory",
    [
        lambda client: OpenAIService(
            api_key="key", model="model", client=client
        ),
        lambda client: AnthropicService(
            api_key="key", model="model", client=client
        ),
        lambda client: GeminiService(
            api_key="key", model="model", client=client
        ),
        lambda client: OpenRouterService(
            api_key="key", model="model", client=client
        ),
    ],
)
def test_health_check_returns_true_for_success(service_factory: Any) -> None:
    client = mock_client(lambda request: httpx.Response(200, json={}))
    service = service_factory(client)

    assert run(service.health_check()) is True
    run(client.aclose())


def test_health_check_returns_false_for_provider_failure() -> None:
    client = mock_client(
        lambda request: httpx.Response(401, json={"error": "invalid key"})
    )
    service = OpenAIService(api_key="key", model="model", client=client)

    assert run(service.health_check()) is False
    run(client.aclose())


@pytest.mark.parametrize(
    ("status", "error_type", "retryable"),
    [
        (401, LLMAuthenticationError, False),
        (403, LLMAuthenticationError, False),
        (429, LLMRateLimitError, True),
        (500, LLMProviderError, True),
        (400, LLMProviderError, False),
    ],
)
def test_http_errors_are_structured(
    status: int,
    error_type: type[Exception],
    retryable: bool,
) -> None:
    client = mock_client(
        lambda request: httpx.Response(
            status,
            json={"error": {"message": "provider failed", "type": "bad"}},
        )
    )
    service = OpenAIService(api_key="key", model="model", client=client)

    with pytest.raises(error_type) as exc_info:
        run(service.generate(LLMRequest(prompt="Question")))

    error = exc_info.value
    assert error.status_code == status
    assert error.retryable is retryable
    assert error.to_dict()["provider"] == "OPENAI"
    assert error.to_dict()["message"] == "provider failed"
    run(client.aclose())


def test_invalid_success_response_raises_structured_response_error() -> None:
    client = mock_client(lambda request: httpx.Response(200, json={"choices": []}))
    service = OpenAIService(api_key="key", model="model", client=client)

    with pytest.raises(LLMResponseError, match="invalid response"):
        run(service.generate(LLMRequest(prompt="Question")))
    run(client.aclose())


def test_invalid_stream_json_raises_structured_response_error() -> None:
    client = mock_client(
        lambda request: httpx.Response(200, text="data: {invalid}\n\n")
    )
    service = OpenAIService(api_key="key", model="model", client=client)

    with pytest.raises(LLMResponseError, match="invalid JSON"):
        run(collect(service, LLMRequest(prompt="Question")))
    run(client.aclose())


class SlowTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "late"}}],
                "usage": {},
            },
        )


def test_generation_timeout_is_structured_and_retryable() -> None:
    client = httpx.AsyncClient(transport=SlowTransport())
    service = OpenAIService(
        api_key="key", model="model", client=client, timeout_s=0.001
    )

    with pytest.raises(LLMTimeoutError) as exc_info:
        run(service.generate(LLMRequest(prompt="Question")))

    assert exc_info.value.retryable is True
    assert exc_info.value.details["operation"] == "generation"
    run(client.aclose())


@pytest.mark.parametrize("timeout_s", [60, 180, 300])
def test_injected_http_client_receives_configured_request_timeout(
    timeout_s: int,
) -> None:
    captured: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.extensions["timeout"])
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "generated"}}],
                "model": "model",
            },
        )

    client = mock_client(handler)
    service = OpenRouterService(
        api_key="key",
        model="model",
        client=client,
        timeout_s=timeout_s,
    )

    run(service.generate(LLMRequest(prompt="Generate firmware")))

    assert captured == [
        {
            "connect": float(timeout_s),
            "read": float(timeout_s),
            "write": float(timeout_s),
            "pool": float(timeout_s),
        }
    ]
    run(client.aclose())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_key": "", "model": "model"},
        {"api_key": "key", "model": ""},
        {"api_key": "key", "model": "model", "timeout_s": 0},
    ],
)
def test_invalid_provider_configuration_is_structured(kwargs: dict[str, Any]) -> None:
    with pytest.raises(LLMConfigurationError):
        OpenAIService(**kwargs)


def test_cancellation_is_not_converted_to_provider_error() -> None:
    class CancelTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(
            self, request: httpx.Request
        ) -> httpx.Response:
            raise asyncio.CancelledError

    client = httpx.AsyncClient(transport=CancelTransport())
    service = OpenAIService(api_key="key", model="model", client=client)

    with pytest.raises(asyncio.CancelledError):
        run(service.generate(LLMRequest(prompt="Question")))
    run(client.aclose())
