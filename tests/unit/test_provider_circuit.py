import asyncio

import pytest

from backend.model_router.circuit import ProviderCircuitBreaker
from backend.services.llm_service import LLMProvider, LLMProviderError


def unavailable(retry_after_ms=None):
    return LLMProviderError("overloaded", provider=LLMProvider.OPENAI, code="PROVIDER_UNAVAILABLE",
        status_code=503, retryable=True, details={"retry_after_ms": retry_after_ms})


def test_cooldown_survives_restart_and_allows_one_probe(tmp_path):
    now = [1000.0]
    circuit = ProviderCircuitBreaker(tmp_path / "health.sqlite3", clock=lambda: now[0], jitter=lambda a, b: a)
    identity = circuit.identity("nvidia_nim", "550b", "https://endpoint", "private-credential")
    async def scenario():
        for hint in (None, 60_000):
            with pytest.raises(LLMProviderError, match="overloaded"):
                async with circuit.slot(identity):
                    raise unavailable(hint)
        restarted = ProviderCircuitBreaker(circuit.path, clock=lambda: now[0], jitter=lambda a, b: a)
        with pytest.raises(LLMProviderError) as caught:
            async with restarted.slot(identity):
                pytest.fail("outbound request during cooldown")
        assert caught.value.details["request_reached_provider"] is False
        assert caught.value.details["retry_after_ms"] >= 60_000
        now[0] += 61
        async with restarted.slot(identity):
            with pytest.raises(LLMProviderError):
                async with circuit.slot(identity):
                    pytest.fail("second probe admitted")
        async with circuit.slot(identity):
            pass
    asyncio.run(scenario())
    assert b"private-credential" not in circuit.path.read_bytes()


def test_model_endpoint_and_credentials_have_independent_cooldowns(tmp_path):
    circuit = ProviderCircuitBreaker(tmp_path / "health.sqlite3")
    original = circuit.identity("nvidia_nim", "550b", "https://one", "key")
    async def scenario():
        for _ in range(2):
            with pytest.raises(LLMProviderError):
                async with circuit.slot(original):
                    raise unavailable()
        for args in [("nvidia_nim", "120b", "https://one", "key"), ("nvidia_nim", "550b", "https://two", "key"), ("nvidia_nim", "550b", "https://one", "new-key")]:
            async with circuit.slot(circuit.identity(*args)):
                pass
    asyncio.run(scenario())


def test_retry_after_cools_down_the_endpoint_on_its_first_failure(tmp_path):
    circuit = ProviderCircuitBreaker(tmp_path / "health.sqlite3")
    async def scenario():
        with pytest.raises(LLMProviderError):
            async with circuit.slot("endpoint"):
                raise unavailable(120_000)
        with pytest.raises(LLMProviderError) as caught:
            async with circuit.slot("endpoint"):
                pytest.fail("request ignored server cooldown")
        assert caught.value.details["retry_after_ms"] > 119_000
        assert caught.value.details["request_reached_provider"] is False
    asyncio.run(scenario())


def test_cancelled_probe_releases_its_lease(tmp_path):
    now = [1000.0]
    circuit = ProviderCircuitBreaker(tmp_path / "health.sqlite3", clock=lambda: now[0], jitter=lambda a, b: a)
    async def scenario():
        for _ in range(2):
            with pytest.raises(LLMProviderError):
                async with circuit.slot("endpoint"):
                    raise unavailable()
        now[0] += 31
        with pytest.raises(asyncio.CancelledError):
            async with circuit.slot("endpoint"):
                raise asyncio.CancelledError()
        async with circuit.slot("endpoint"):
            pass
    asyncio.run(scenario())


def test_productive_attempt_cap_does_not_mark_the_endpoint_unavailable(tmp_path):
    from backend.services.llm_service import LLMTimeoutError
    circuit = ProviderCircuitBreaker(tmp_path / "health.sqlite3")
    async def scenario():
        for _ in range(3):
            with pytest.raises(LLMTimeoutError):
                async with circuit.slot("endpoint"):
                    raise LLMTimeoutError("attempt cap", provider=LLMProvider.OPENAI,
                        retryable=True, details={"timeout_stage": "attempt_total", "stream_event_count": 20})
        async with circuit.slot("endpoint"):
            pass
    asyncio.run(scenario())
