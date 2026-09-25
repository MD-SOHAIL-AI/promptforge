from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from backend.model_router import (
    MemoryCredentialStore,
    ModelRequest,
    ProviderRegistry,
    ProviderSettingsStorage,
)
from backend.model_router.models import CanonicalStreamEvent
from backend.model_router.providers.nvidia_nim import (
    NvidiaNimProvider,
    NvidiaNimService,
)
from backend.services.llm_service import LLMProvider, LLMRequest, LLMTimeoutError


def _registry(tmp_path: Path) -> ProviderRegistry:
    storage = ProviderSettingsStorage(
        tmp_path / "settings.json", credential_store=MemoryCredentialStore()
    )
    registry = ProviderRegistry(storage)
    registry.configure_provider(
        "nvidia_nim", {"api_key": "nvapi-test", "enabled": True}
    )
    return registry


class PartialTimeoutNvidiaService:
    attempts: list[str] = []
    failures_remaining = 0

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_s: float,
        base_url: str | None = None,
        client: object | None = None,
    ) -> None:
        del api_key, timeout_s, base_url, client
        self.model = model
        self.attempts.append(model)

    async def generate_event_stream(self, request: LLMRequest):
        del request
        if self.failures_remaining > 0:
            type(self).failures_remaining -= 1
            yield CanonicalStreamEvent("text_delta", text="discard this partial")
            raise LLMTimeoutError(
                "NVIDIA NIM streaming generation timed out",
                provider=LLMProvider.OPENAI,
                code="PROVIDER_TIMEOUT",
                retryable=True,
            )
        yield CanonicalStreamEvent("text_delta", text="complete")
        yield CanonicalStreamEvent("done", finish_reason="stop")

    async def aclose(self) -> None:
        return None


class RetryingNvidiaProvider(NvidiaNimProvider):
    service_type = PartialTimeoutNvidiaService


def test_nvidia_agent_stream_retries_transactionally_on_same_model(
    tmp_path: Path, monkeypatch
) -> None:
    from backend.model_router.providers import nvidia_nim as nvidia_module

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr(nvidia_module.asyncio, "sleep", no_delay)
    PartialTimeoutNvidiaService.attempts = []
    PartialTimeoutNvidiaService.failures_remaining = 1
    provider = RetryingNvidiaProvider(_registry(tmp_path))
    selected = "nvidia/nemotron-3-super-120b-a12b"
    attempt_diagnostics: dict[str, object] = {}

    async def consume() -> list[CanonicalStreamEvent]:
        return [
            event
            async for event in provider.generate_event_stream(
                ModelRequest(
                    prompt="create project",
                    task_type="planning",
                    model_id=selected,
                    metadata={
                        "run_id": "run-nim",
                        "turn_id": "run-nim:turn-1",
                        "forgex_attempt_diagnostics": attempt_diagnostics,
                    },
                )
            )
        ]

    events = asyncio.run(consume())
    assert [event.text for event in events if event.type == "text_delta"] == [
        "complete"
    ]
    assert [event.type for event in events] == ["text_delta", "done"]
    assert PartialTimeoutNvidiaService.attempts == [selected, selected]
    assert attempt_diagnostics["physical_request_count"] == 2
    assert attempt_diagnostics["http_status"] == 200
    assert attempt_diagnostics["last_attempt_succeeded"] is True


def test_nvidia_agent_http_payload_matches_production_template() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, text="data: [DONE]\n\n")

    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "nvidia_nim"
        / "agent_tool_request.template.json"
    )
    expected = json.loads(fixture.read_text(encoding="utf-8"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = NvidiaNimService(
        api_key="nvapi-test", model=expected["model"], client=client
    )
    request = LLMRequest(
        prompt=expected["messages"][1]["content"],
        system_prompt=expected["messages"][0]["content"],
        temperature=0,
        max_tokens=expected["max_tokens"],
        metadata={
            "forgex_nvidia_agent": True,
            "forgex_native_tools": expected["tools"],
        },
    )

    async def consume() -> list[CanonicalStreamEvent]:
        events = [event async for event in service.generate_event_stream(request)]
        await client.aclose()
        return events

    events = asyncio.run(consume())
    assert [event.type for event in events] == ["done"]
    assert captured == expected


def test_nvidia_finish_packet_and_done_sentinel_become_one_terminal_event() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"ready"},'
                '"finish_reason":"tool_calls"}],"usage":{"total_tokens":7}}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = NvidiaNimService(api_key="nvapi-test", model="model", client=client)

    async def consume() -> list[CanonicalStreamEvent]:
        events = [
            event
            async for event in service.generate_event_stream(
                LLMRequest(prompt="status")
            )
        ]
        await client.aclose()
        return events

    events = asyncio.run(consume())
    assert [event.type for event in events] == ["text_delta", "done"]
    assert events[-1].finish_reason == "tool_calls"
    assert events[-1].usage["total_tokens"] == 7
