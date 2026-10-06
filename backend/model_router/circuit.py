"""Persistent endpoint cooldown with one probe across desktop processes."""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager, closing
from pathlib import Path

from backend.services.llm_service import LLMError, LLMProvider, LLMProviderError


class ProviderCircuitBreaker:
    def __init__(self, path: Path, *, clock=time.time, jitter=random.uniform, failure_threshold: int = 2):
        self.path = path
        self.clock = clock
        self.jitter = jitter
        self.failure_threshold = max(2, int(failure_threshold))
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=5)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS endpoint_health (identity TEXT PRIMARY KEY, failures INTEGER NOT NULL, open_until REAL NOT NULL, probe_id TEXT, probe_until REAL NOT NULL)")
            db.commit()

    @staticmethod
    def identity(provider_id, model_id, base_url, credential):
        # No endpoint credentials, prompts or response bodies are persisted.
        return hashlib.sha256(json.dumps([provider_id, model_id, base_url, credential], separators=(",", ":")).encode()).hexdigest()

    def health(self, identity):
        now = self.clock()
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            row = db.execute("SELECT failures, open_until, probe_id, probe_until FROM endpoint_health WHERE identity=?", (identity,)).fetchone()
        failures, opened, probe, until = row or (0, 0, None, 0)
        due = max(opened, until if probe else 0)
        return {"state": "open" if opened > now else "half_open" if failures >= self.failure_threshold else "closed",
                "failures": failures, "retry_after_ms": max(0, int((due - now) * 1000) + 1) if due > now else 0,
                "probe_active": bool(probe and until > now)}

    def require_available(self, identity):
        health = self.health(identity)
        if health["retry_after_ms"]:
            raise LLMProviderError("The selected model endpoint is temporarily unavailable and cooling down.",
                provider=LLMProvider.OPENAI, code="PROVIDER_UNAVAILABLE", retryable=True,
                details={"origin": "provider_circuit", "retry_after_ms": health["retry_after_ms"],
                         "request_reached_provider": False})

    def _admit(self, identity):
        now = self.clock()
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT failures, open_until, probe_id, probe_until FROM endpoint_health WHERE identity=?", (identity,)).fetchone()
            if row and (row[1] > now or row[3] > now):
                delay_ms = max(1, int((max(row[1], row[3]) - now) * 1000) + 1)
                raise LLMProviderError("The selected model endpoint is cooling down after repeated failures.",
                    provider=LLMProvider.OPENAI, code="PROVIDER_UNAVAILABLE", retryable=True,
                    details={"origin": "provider_circuit", "retry_after_ms": delay_ms, "request_reached_provider": False})
            if row and row[0] >= self.failure_threshold:
                probe = uuid.uuid4().hex
                db.execute("UPDATE endpoint_health SET probe_id=?, probe_until=? WHERE identity=?", (probe, now + 350, identity))
                db.commit()
                return probe
            db.commit()
            return None

    def _settle(self, identity, probe, failure=None, *, cancelled=False):
        now = self.clock()
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT failures, open_until, probe_id, probe_until FROM endpoint_health WHERE identity=?", (identity,)).fetchone()
            if (probe and (not row or row[2] != probe)) or (row and ((row[2] and row[2] != probe) or (probe is None and row[1] > now))):
                db.commit()
                return
            if cancelled:
                if probe:
                    db.execute("UPDATE endpoint_health SET probe_id=NULL, probe_until=0 WHERE identity=? AND probe_id=?", (identity, probe))
            elif failure is None:
                db.execute("DELETE FROM endpoint_health WHERE identity=?", (identity,))
            else:
                failures = min(16, (row[0] if row else 0) + 1)
                from .retry import retry_after_ms_from_details
                hint = retry_after_ms_from_details(getattr(failure, "details", {}))
                hint_seconds = max(0, hint / 1000) if isinstance(hint, (int, float)) and not isinstance(hint, bool) else 0
                if hint_seconds:
                    failures = max(self.failure_threshold, failures)
                delay = max(min(120, 30 * 2 ** min(failures - self.failure_threshold, 2)) * self.jitter(1.0, 1.25), hint_seconds) if failures >= self.failure_threshold else 0
                db.execute("INSERT OR REPLACE INTO endpoint_health VALUES (?,?,?,?,?)", (identity, failures, now + delay if delay else 0, None, 0))
            db.commit()

    @asynccontextmanager
    async def slot(self, identity):
        probe = self._admit(identity)
        try:
            yield
        except asyncio.CancelledError:
            self._settle(identity, probe, cancelled=True)
            raise
        except GeneratorExit:
            self._settle(identity, probe, cancelled=True)
            raise
        except Exception as exc:
            transient = isinstance(exc, LLMError) and (getattr(exc, "status_code", 0) in {429, 500, 502, 503, 504, 524}
                or getattr(exc, "code", "") in {"PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT", "API_NETWORK_ERROR", "PROVIDER_CONNECTION_ERROR"})
            if getattr(exc, "details", {}).get("timeout_stage") in {"attempt_total", "turn_deadline"} and getattr(exc, "details", {}).get("stream_event_count", 0):
                transient = False
            self._settle(identity, probe, exc if transient else None)
            if transient:
                health = self.health(identity)
                if health["retry_after_ms"]:
                    # Preserve the original HTTP failure while handing long waits
                    # to durable job recovery, rather than masking it with a gate error.
                    from types import MappingProxyType
                    exc.details = MappingProxyType({**exc.details, "circuit_open": True,
                        "retry_after_ms": max(health["retry_after_ms"], exc.details.get("retry_after_ms") or 0)})
            raise
        else:
            self._settle(identity, probe)
