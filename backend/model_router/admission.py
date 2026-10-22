"""Cross-event-loop provider admission control for desktop model traffic."""

from __future__ import annotations

import asyncio
import threading
import time
import sqlite3
import hashlib
import uuid
from pathlib import Path
from contextlib import closing
from collections import deque
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from typing import AsyncIterator

from backend.observability.diagnostics import emit_diagnostic_event
from backend.services.llm_service import LLMProvider, LLMProviderError


@dataclass
class _AdmissionQueue:
    active: int = 0
    waiting: deque[object] = field(default_factory=deque)


class ProviderAdmissionController:
    """Bound turns per model endpoint, with fair cross-event-loop queues."""

    def __init__(
        self,
        *,
        limits: dict[str, int] | None = None,
        queue_timeout_seconds: float = 30.0,
        path: Path | None = None,
    ) -> None:
        configured = {"nvidia_nim": 1, "ollama": 2, "lmstudio": 2}
        configured.update(limits or {})
        self._limits = {key: max(1, int(value)) for key, value in configured.items()}
        self._default_limit = 4
        self._queue_timeout_seconds = max(0.01, min(float(queue_timeout_seconds), 120.0))
        self._lock = threading.Lock()
        self._queues: dict[tuple[str, str, str], _AdmissionQueue] = {}
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path, timeout=5)) as db:
                db.execute("CREATE TABLE IF NOT EXISTS provider_slots (sequence INTEGER PRIMARY KEY AUTOINCREMENT, ticket TEXT UNIQUE NOT NULL, endpoint TEXT NOT NULL, active INTEGER NOT NULL, expires REAL NOT NULL)")
                db.commit()

    def _distributed_acquire(self, ticket, endpoint, limit):
        now = time.time()
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM provider_slots WHERE expires <= ?", (now,))
            db.execute("INSERT OR IGNORE INTO provider_slots(ticket,endpoint,active,expires) VALUES(?,?,0,?)", (ticket, endpoint, now + 10))
            db.execute("UPDATE provider_slots SET expires=? WHERE ticket=?", (now + 10, ticket))
            active = db.execute("SELECT COUNT(*) FROM provider_slots WHERE endpoint=? AND active=1", (endpoint,)).fetchone()[0]
            first = db.execute("SELECT ticket FROM provider_slots WHERE endpoint=? AND active=0 ORDER BY sequence LIMIT 1", (endpoint,)).fetchone()
            acquired = active < limit and first and first[0] == ticket
            if acquired:
                db.execute("UPDATE provider_slots SET active=1 WHERE ticket=?", (ticket,))
            db.commit()
            return bool(acquired)

    def _renew(self, ticket):
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            result = db.execute("UPDATE provider_slots SET expires=? WHERE ticket=? AND expires>?", (time.time() + 10, ticket, time.time()))
            db.commit()
            return result.rowcount == 1

    def _release(self, ticket):
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.execute("DELETE FROM provider_slots WHERE ticket=?", (ticket,))
            db.commit()

    @asynccontextmanager
    async def _distributed_slot(self, provider_id, model_id, endpoint_identity, deadline):
        ticket = uuid.uuid4().hex
        endpoint = hashlib.sha256(repr((provider_id, model_id, endpoint_identity)).encode()).hexdigest()
        started = time.monotonic()
        stop = min(started + self._queue_timeout_seconds, deadline if isinstance(deadline, (int, float)) else float("inf"))
        heartbeat = None
        lost = False
        owner = asyncio.current_task()
        async def renew():
            nonlocal lost
            try:
                while True:
                    try:
                        await asyncio.wait_for(asyncio.Event().wait(), timeout=2)
                    except asyncio.TimeoutError:
                        pass
                    if not self._renew(ticket):
                        lost = True
                        owner.cancel()
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                lost = True
                owner.cancel()
        try:
            while True:
                if time.monotonic() >= stop:
                    raise LLMProviderError("Another ForgeX task is using this model endpoint. No provider request was sent.",
                        provider=LLMProvider.OPENAI, code="PROVIDER_BUSY", retryable=True,
                        details={"provider_id": provider_id, "origin": "provider_admission", "request_reached_provider": False, "retry_after_ms": 2000})
                if self._distributed_acquire(ticket, endpoint, self.limit(provider_id)):
                    break
                await asyncio.sleep(min(0.05, max(0, stop - time.monotonic())))
            heartbeat = asyncio.create_task(renew())
            yield
        except asyncio.CancelledError:
            if lost:
                raise LLMProviderError("Model request ownership expired; its result was discarded.", provider=LLMProvider.OPENAI,
                    code="PROVIDER_ADMISSION_LEASE_LOST", retryable=False, details={"origin": "provider_admission"}) from None
            raise
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            self._release(ticket)

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
        endpoint_identity: str = "",
        deadline: float | None = None,
    ) -> AsyncIterator[None]:
        if self.path is not None:
            started = time.monotonic()
            async with self._distributed_slot(provider_id, model_id, endpoint_identity, deadline):
                emit_diagnostic_event(component="provider", event="provider.admission.acquired",
                    provider=provider_id, model=model_id, run_id=run_id, turn_id=turn_id,
                    wait_ms=max(0, int((time.monotonic() - started) * 1000)), concurrency_limit=self.limit(provider_id))
                yield
            return
        key = (provider_id, model_id, endpoint_identity)
        ticket = object()
        with self._lock:
            queue = self._queues.setdefault(key, _AdmissionQueue())
            queue.waiting.append(ticket)
        started = time.monotonic()
        acquired = False
        try:
            while True:
                with self._lock:
                    if queue.waiting[0] is ticket and queue.active < self.limit(provider_id):
                        queue.waiting.popleft()
                        queue.active += 1
                        acquired = True
                if acquired:
                    break
                remaining = self._queue_timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.05, remaining))
        finally:
            if not acquired:
                with self._lock:
                    queue.waiting.remove(ticket)
        waited_ms = max(0, int((time.monotonic() - started) * 1000))
        if not acquired:
            raise LLMProviderError(
                "Another ForgeX task is using this model endpoint. No provider request was sent.",
                provider=LLMProvider.OPENAI,
                code="PROVIDER_BUSY",
                retryable=True,
                details={
                    "provider_id": provider_id,
                    "retry_after_ms": 2_000,
                    "request_reached_provider": False,
                    "origin": "provider_admission",
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
            with self._lock:
                queue.active -= 1
