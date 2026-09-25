import asyncio
import json
from contextlib import aclosing

import httpx
import pytest

from backend.model_router.providers.openai_provider import OpenAIProvider
from backend.model_router.models import ModelRequest
from backend.services.llm_service import GeminiService, OpenAIService, LLMRequest
from tests.unit.test_model_router import make_router


def test_real_openai_adapter_streams_content_and_closes_client(tmp_path):
    async def exercise():
        packets = [
            {"choices": [{"delta": {"reasoning_content": "PRIVATE"}}]},
            {"choices": [{"delta": {"content": "Hello "}}]},
            {"choices": [{"delta": {"content": "world"}}]},
        ]
        seen = []

        def respond(request):
            seen.append(json.loads(request.content))
            return httpx.Response(
                200,
                text="".join(
                    "data: " + json.dumps(packet) + "\n\n" for packet in packets
                )
                + "data: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        service = OpenAIService(api_key="test", model="selected-model", client=client)

        class Adapter(OpenAIProvider):
            def _service(self, model_id=None):
                assert model_id == "selected-model"
                return service

        router, registry = make_router(tmp_path, {})
        registry.configure_provider("openai", {"api_key": "test-key", "enabled": True})
        router._providers["openai"] = Adapter(registry)
        result = "".join(
            [
                chunk
                async for chunk in router.generate_stream(
                    LLMRequest(
                        prompt="Hello",
                        metadata={
                            "task_type": "general_chat",
                            "provider_id": "openai",
                            "model_id": "selected-model",
                        },
                    )
                )
            ]
        )
        assert result == "Hello world"
        assert seen[0]["stream"] is True
        assert seen[0]["model"] == "selected-model"
        # Injected clients are caller-owned by the existing service contract.
        await client.aclose()

    asyncio.run(exercise())


def test_adapter_closes_stream_and_service_on_early_exit():
    class Service:
        closed = False
        stream_closed = False

        async def generate_stream(self, request):
            try:
                yield "first"
                await asyncio.Event().wait()
            finally:
                self.stream_closed = True

        async def aclose(self):
            self.closed = True

    service = Service()

    class Adapter(OpenAIProvider):
        def _service(self, model_id=None):
            return service

    async def exercise():
        async with aclosing(
            Adapter(None).generate_stream(ModelRequest(prompt="Hello"))
        ) as stream:
            assert await anext(stream) == "first"
        assert service.closed and service.stream_closed

    asyncio.run(exercise())


def test_gemini_thought_parts_are_not_public_text():
    async def exercise():
        service = GeminiService(api_key="test", model="test")
        try:
            packet = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "PRIVATE", "thought": True},
                                {"text": "Public answer"},
                            ]
                        }
                    }
                ]
            }
            assert service._parse_stream_event(packet) == ("Public answer",)
        finally:
            await service.aclose()

    asyncio.run(exercise())
