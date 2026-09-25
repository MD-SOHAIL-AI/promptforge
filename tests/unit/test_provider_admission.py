from __future__ import annotations

import asyncio

import pytest

from backend.model_router.admission import ProviderAdmissionController
from backend.services.llm_service import LLMProviderError


def test_nvidia_turns_are_serialized() -> None:
    controller = ProviderAdmissionController(queue_timeout_seconds=1)
    active = 0
    peak = 0

    async def worker() -> None:
        nonlocal active, peak
        async with controller.slot("nvidia_nim", "model"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    async def scenario() -> None:
        await asyncio.gather(worker(), worker(), worker())

    asyncio.run(scenario())
    assert peak == 1


def test_busy_provider_fails_before_an_outbound_request() -> None:
    controller = ProviderAdmissionController(
        limits={"nvidia_nim": 1}, queue_timeout_seconds=0.01
    )

    async def scenario() -> None:
        async with controller.slot("nvidia_nim", "model"):
            with pytest.raises(LLMProviderError) as caught:
                async with controller.slot("nvidia_nim", "model"):
                    pass
            assert caught.value.code == "PROVIDER_BUSY"
            assert caught.value.details["request_reached_provider"] is False

    asyncio.run(scenario())
