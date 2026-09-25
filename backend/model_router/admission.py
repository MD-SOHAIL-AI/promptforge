"""Cross-event-loop provider admission control for desktop model traffic."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from backend.observability.diagnostics import emit_diagnostic_event
from backend.services.llm_service import LLMProvider, LLMProviderError


class ProviderAdmissionController:
    """Bound concurrent logical turns before provider-specific retries begin."""

    def __init__(
        self,
        *,
        limits: dict[str, int] | None = None,
        queue_timeout_seconds: float = 30.0,
    ) -> None:
        configured = {"nvidia_nim": 1, "ollama": 2, "lmstudio": 2}
        configured.update(limits or {})
        self._limits = {key: max(1, int(value)) for key, value in configured.items()}
        self._default_limit = 4
        self._queue_timeout_seconds = max(0.01, min(float(queue_timeout_seconds), 120.0))
        self._lock = threading.Lock()
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}

    def limit(self, provider_id: str) -> int:
        return self._limits.get(provider_id, self._default_limit)

    @asynccontextmanager
    async def slot(
        self,
        provider_id: str,
        model_id: str,
        *,
        run_id: str | None = None,
        turn_id: str | None = None,
    ) -> AsyncIterator[None]:
        semaphore = self._semaphore(provider_id)
        started = time.monotonic()
        acquired = semaphore.acquire(blocking=False)
        if not acquired:
            acquire = asyncio.create_task(
                asyncio.to_thread(semaphore.acquire, True, self._queue_timeout_seconds)
            )
            try:
                acquired = await asyncio.shield(acquire)
            except asyncio.CancelledError:
                # A worker thread cannot be force-cancelled while waiting. Wait for
                # its bounded result and release any slot it acquired before exit.
                acquired = await acquire
                if acquired:
                    semaphore.release()
                raise
        waited_ms = max(0, int((time.monotonic() - started) * 1000))
        if not acquired:
            raise LLMProviderError(
                "The selected provider is busy with another ForgeX turn.",
                provider=LLMProvider.OPENAI,
                code="PROVIDER_BUSY",
                retryable=True,
                details={
                    "provider_id": provider_id,
                    "retry_after_ms": 2_000,
                    "request_reached_provider": False,
                },
            )
        emit_diagnostic_event(
            component="provider",
            event="provider.admission.acquired",
            provider=provider_id,
            model=model_id,
            run_id=run_id,
            turn_id=turn_id,
            wait_ms=waited_ms,
            concurrency_limit=self.limit(provider_id),
        )
        try:
            yield
        finally:
            semaphore.release()

    def _semaphore(self, provider_id: str) -> threading.BoundedSemaphore:
        with self._lock:
            semaphore = self._semaphores.get(provider_id)
            if semaphore is None:
                semaphore = threading.BoundedSemaphore(self.limit(provider_id))
                self._semaphores[provider_id] = semaphore
            return semaphore
