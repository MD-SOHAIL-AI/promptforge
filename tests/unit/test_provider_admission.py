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
            assert caught.value.details["origin"] == "provider_admission"

    asyncio.run(scenario())


def test_different_models_and_credentials_do_not_share_a_local_slot():
    controller = ProviderAdmissionController(queue_timeout_seconds=0.01)
    async def scenario():
        async with controller.slot("nvidia_nim", "550b", endpoint_identity="account-one"):
            async with controller.slot("nvidia_nim", "120b", endpoint_identity="account-one"):
                async with controller.slot("nvidia_nim", "550b", endpoint_identity="account-two"):
                    pass
    asyncio.run(scenario())


def test_waiting_task_gets_a_turn_before_the_active_task_can_reacquire():
    controller = ProviderAdmissionController(queue_timeout_seconds=1)
    order = []
    async def waiting():
        async with controller.slot("nvidia_nim", "550b"):
            order.append("waiting")
    async def scenario():
        async with controller.slot("nvidia_nim", "550b"):
            waiter = asyncio.create_task(waiting())
            await asyncio.sleep(0.01)
        async with controller.slot("nvidia_nim", "550b"):
            order.append("reacquired")
        await waiter
    asyncio.run(scenario())
    assert order == ["waiting", "reacquired"]


def test_queue_failure_is_not_reported_as_an_upstream_outage():
    from backend.agent_runtime.failure_contract import failure_details
    from backend.agent_runtime.run_reporting import normalize_provider_error, actionable_message
    assert normalize_provider_error("PROVIDER_BUSY").value == "PROVIDER_BUSY"
    failure = failure_details("PROVIDER_BUSY", status="paused_model_unavailable")
    assert failure["category"] == "local_model_queue" and failure["origin"] == "forgex"
    assert "before sending" in actionable_message(normalize_provider_error("PROVIDER_BUSY"), "nvidia_nim")


def test_cancelled_queue_wait_returns_immediately_without_taking_a_slot():
    controller = ProviderAdmissionController(queue_timeout_seconds=30)
    async def queued():
        async with controller.slot("nvidia_nim", "model"):
            raise AssertionError("cancelled waiter acquired a slot")
    async def scenario():
        async with controller.slot("nvidia_nim", "model"):
            waiter = asyncio.create_task(queued())
            await asyncio.sleep(0.01)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(waiter, timeout=0.2)
        async with controller.slot("nvidia_nim", "model"):
            pass
    asyncio.run(scenario())
