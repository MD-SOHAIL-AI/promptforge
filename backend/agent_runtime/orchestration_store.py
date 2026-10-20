"""Durable SQLite persistence for the canonical ForgeX agent runtime.

The append-only journal is historical truth.  Projection checkpoints and live
streaming are derived state.  The store remains backward compatible with the
legacy agent_events table used by existing SSE routes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .activity_stream import project_activity


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    session_id: str
    owner_id: str
    lease_epoch: int
    expires_at: str


class AgentOrchestrationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._transaction_state = threading.local()
        self._quarantined: dict[str, str] = {}
        self._observers = set()
        self._initialize()

    # ---- Runs / graph -------------------------------------------------
    def backup_before_reconciliation(self) -> Path:
        """SQLite backup includes committed WAL; never overwrite the original."""
        destination = self.path.with_name(self.path.name + ".pre-settlement-reconciliation.bak")
        with self._lock:
            if not destination.exists():
                with closing(self._connect()) as source, closing(sqlite3.connect(str(destination))) as target:
                    source.backup(target)
        return destination

    def retention_payloads(self):
        # Conservative: even old checkpoints remain protected until retirement.
        with closing(self._connect()) as connection:
            for table in ("agent_runs", "agent_run_checkpoints", "agent_operations"):
                for row in connection.execute(f"SELECT payload_json FROM {table}"):
                    yield _decode(row[0])

    def save_run(self, run_id: str, session_id: str, project_id: str, status: str, payload: Mapping[str, object]) -> None:
        now = utc_now()
        encoded = _encode(payload)
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO agent_runs(run_id,session_id,project_id,status,payload_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (run_id, session_id, project_id, status, encoded, now, now),
            )

    def load_run(self, run_id: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT payload_json FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
        return _decode(row[0]) if row else None

    def run_snapshot(self, run_id: str):
        """Read the run and its job from the same committed SQLite snapshot."""
        with closing(self._connect()) as connection:
            if not getattr(self._transaction_state, "active", False):
                connection.execute("BEGIN")
            row = connection.execute("SELECT payload_json FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            run = _decode(row[0])
            job_row = connection.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?", (run.get("job_id"),)).fetchone()
            return run, _decode(job_row[0]) if job_row else None

    def list_runs(self, *, session_id: str | None = None) -> tuple[dict[str, object], ...]:
        query = "SELECT payload_json FROM agent_runs"
        params: tuple[object, ...] = ()
        if session_id:
            query += " WHERE session_id=?"
            params = (session_id,)
        query += " ORDER BY created_at, run_id"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(_decode(row[0]) for row in rows)

    def save_node(self, run_id: str, node_id: str, status: str, payload: Mapping[str, object]) -> None:
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO agent_nodes(run_id,node_id,status,payload_json,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(run_id,node_id) DO UPDATE SET status=excluded.status,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (run_id, node_id, status, _encode(payload), utc_now()),
            )

    def graph(self, run_id: str) -> tuple[dict[str, object], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM agent_nodes WHERE run_id=? ORDER BY rowid", (run_id,)
            ).fetchall()
        return tuple(_decode(row[0]) for row in rows)

    # ---- Session ownership -------------------------------------------
    def acquire_lease(self, session_id: str, owner_id: str, *, ttl_seconds: int = 20) -> LeaseGrant:
        self.require_healthy_journal(session_id)
        ttl = max(5, min(int(ttl_seconds), 300))
        with self._transaction() as connection:
            now = datetime.now(timezone.utc)
            expires = now + timedelta(seconds=ttl)
            row = connection.execute(
                "SELECT owner_id,lease_epoch,expires_at FROM agent_session_leases WHERE session_id=?",
                (session_id,),
            ).fetchone()
            previous_epoch = int(row[1]) if row else 0
            if row:
                current_expiry = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
                if current_expiry > now and str(row[0]) != owner_id:
                    raise ValueError("AGENT_SESSION_LEASE_HELD")
            epoch = previous_epoch + 1
            connection.execute(
                """INSERT INTO agent_session_leases(session_id,owner_id,lease_epoch,expires_at,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(session_id) DO UPDATE SET owner_id=excluded.owner_id,lease_epoch=excluded.lease_epoch,expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
                (session_id, owner_id, epoch, _fmt(expires), utc_now()),
            )
        return LeaseGrant(session_id, owner_id, epoch, _fmt(expires))

    def renew_lease(self, grant: LeaseGrant, *, ttl_seconds: int = 20) -> LeaseGrant:
        self.require_healthy_journal(grant.session_id)
        ttl = max(5, min(int(ttl_seconds), 300))
        with self._transaction() as connection:
            # Lock acquisition can take time. Neither revive an expired owner
            # nor return a renewal whose lifetime was spent waiting to write.
            now = datetime.now(timezone.utc)
            expires = now + timedelta(seconds=ttl)
            row = connection.execute(
                "SELECT owner_id,lease_epoch,expires_at FROM agent_session_leases WHERE session_id=?",
                (grant.session_id,),
            ).fetchone()
            if not row or str(row[0]) != grant.owner_id or int(row[1]) != grant.lease_epoch:
                raise ValueError("AGENT_SESSION_LEASE_FENCED")
            current_expiry = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
            if now >= current_expiry:
                # An expired grant must be acquired again with a new epoch.  Letting
                # the previous owner revive it would create a split-brain window.
                raise ValueError("AGENT_SESSION_LEASE_EXPIRED")
            changed = connection.execute(
                """UPDATE agent_session_leases SET expires_at=?,updated_at=?
                   WHERE session_id=? AND owner_id=? AND lease_epoch=?""",
                (_fmt(expires), utc_now(), grant.session_id, grant.owner_id, grant.lease_epoch),
            ).rowcount
            if changed != 1:
                raise ValueError("AGENT_SESSION_LEASE_FENCED")
        return LeaseGrant(grant.session_id, grant.owner_id, grant.lease_epoch, _fmt(expires))

    def release_lease(self, grant: LeaseGrant) -> None:
        # Do not delete/reset the epoch; clear owner but preserve fencing history.
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT owner_id,lease_epoch FROM agent_session_leases WHERE session_id=?",
                (grant.session_id,),
            ).fetchone()
            if not row or str(row[0]) != grant.owner_id or int(row[1]) != grant.lease_epoch:
                raise ValueError("AGENT_SESSION_LEASE_FENCED")
            connection.execute(
                "UPDATE agent_session_leases SET owner_id='',expires_at=?,updated_at=? WHERE session_id=?",
                (utc_now(), utc_now(), grant.session_id),
            )

    def assert_lease(self, grant: LeaseGrant) -> None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT owner_id,lease_epoch,expires_at FROM agent_session_leases WHERE session_id=?",
                (grant.session_id,),
            ).fetchone()
        if not row or str(row[0]) != grant.owner_id or int(row[1]) != grant.lease_epoch:
            raise ValueError("AGENT_SESSION_LEASE_FENCED")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(str(row[2]).replace("Z", "+00:00")):
            raise ValueError("AGENT_SESSION_LEASE_EXPIRED")

    # ---- Append-only journal -----------------------------------------
    def append_event(
        self,
        session_id: str,
        payload: Mapping[str, object],
        *,
        grant: LeaseGrant | None = None,
        idempotency_key: str | None = None,
        event_version: int = 1,
        run_projection: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        if session_id in self._quarantined:
            raise ValueError("AGENT_JOURNAL_QUARANTINED")
        with self._transaction() as connection:
            if grant is not None:
                self._assert_lease_tx(connection, grant)
            if idempotency_key:
                existing = connection.execute(
                    "SELECT payload_json,payload_hash FROM agent_events WHERE session_id=? AND idempotency_key=?",
                    (session_id, idempotency_key),
                ).fetchone()
                if existing:
                    candidate = dict(payload)
                    candidate.pop("sequence", None)
                    candidate.pop("session_seq", None)
                    candidate_hash = _payload_hash(candidate)
                    if candidate_hash != str(existing[1]):
                        raise ValueError("AGENT_JOURNAL_IDEMPOTENCY_CONFLICT")
                    return _decode(existing[0])

            head = connection.execute(
                "SELECT last_sequence,last_checksum,health FROM agent_session_heads WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if head and head[2] == "quarantined":
                self._quarantined[session_id] = "quarantine_persisted"
                raise ValueError("AGENT_JOURNAL_QUARANTINED")
            if head is None:
                legacy = connection.execute(
                    "SELECT COALESCE(MAX(sequence),0) FROM agent_events WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                last_sequence = int(legacy[0])
                last_checksum = ""
                connection.execute(
                    "INSERT INTO agent_session_heads(session_id,last_sequence,last_checksum,health,updated_at) VALUES(?,?,?,?,?)",
                    (session_id, last_sequence, last_checksum, "healthy", utc_now()),
                )
            else:
                last_sequence = int(head[0])
                last_checksum = str(head[1] or "")
            sequence = last_sequence + 1
            event = dict(payload)
            event_id = str(event.get("event_id") or f"event-{uuid.uuid4().hex}")
            event["event_id"] = event_id
            event["event_version"] = int(event.get("event_version") or event_version)
            event["session_id"] = session_id
            event["sequence"] = sequence
            event["session_seq"] = sequence
            if grant is not None:
                event["lease_epoch"] = grant.lease_epoch
            event.setdefault("created_at", utc_now())
            event.setdefault("timestamp", event["created_at"])
            if event.get("run_id"):
                candidate = project_activity(event)
                if candidate:
                    previous_row = connection.execute(
                        "SELECT payload_json FROM agent_events WHERE run_id=? "
                        "AND json_extract(payload_json, '$.activity_record.correlation')=? ORDER BY sequence DESC LIMIT 1",
                        (event["run_id"], candidate["correlation"]),
                    ).fetchone()
                    previous = _decode(previous_row[0]).get("activity_record") if previous_row else None
                    event["activity_record"] = project_activity(event, previous)
            event["previous_checksum"] = last_checksum
            payload_hash = _payload_hash(event)
            checksum = hashlib.sha256((last_checksum + payload_hash).encode("utf-8")).hexdigest()
            event["event_checksum"] = checksum
            encoded = _encode(event)
            connection.execute(
                """INSERT INTO agent_events(
                       session_id,sequence,run_id,node_id,event_type,payload_json,created_at,
                       event_id,event_version,lease_epoch,idempotency_key,previous_checksum,event_checksum,payload_hash)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id,
                    sequence,
                    _optional(event.get("run_id")),
                    _optional(event.get("node_id")),
                    str(event.get("event_type") or "activity.updated"),
                    encoded,
                    str(event["created_at"]),
                    event_id,
                    int(event["event_version"]),
                    int(event.get("lease_epoch") or 0),
                    idempotency_key,
                    last_checksum,
                    checksum,
                    payload_hash,
                ),
            )
            connection.execute(
                "UPDATE agent_session_heads SET last_sequence=?,last_checksum=?,health='healthy',updated_at=? WHERE session_id=?",
                (sequence, checksum, utc_now(), session_id),
            )
            if run_projection is not None:
                connection.execute("""INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                    (str(run_projection["run_id"]), session_id, str(run_projection["project_id"]), str(run_projection["status"]),
                     _encode(run_projection), str(run_projection.get("created_at") or utc_now()), utc_now()))
        self.notify_observers(session_id)
        return event

    def notify_observers(self, session_id):
        if getattr(self._transaction_state, "active", False):
            self._transaction_state.notifications.add(session_id)
            return
        with self._lock:
            observers = tuple(self._observers)
        for observer in observers:
            try:
                observer(session_id)
            except RuntimeError:
                pass  # A disconnected observation cannot fail a committed event.

    async def wait_for_events(self, session_id, after_sequence, *, run_id=None, timeout=1.0):
        """Notifications are a wakeup hint; the journal remains the event source."""
        import asyncio
        loop = asyncio.get_running_loop()
        changed = asyncio.Event()
        def notify(identity):
            if identity == session_id:
                loop.call_soon_threadsafe(changed.set)
        with self._lock:
            self._observers.add(notify)
        try:
            if await asyncio.to_thread(self.events, session_id, after_sequence=after_sequence, run_id=run_id, limit=1):
                return
            try:
                await asyncio.wait_for(changed.wait(), timeout)
            except asyncio.TimeoutError:
                pass
        finally:
            with self._lock:
                self._observers.discard(notify)

    def events(self, session_id: str, *, after_sequence: int = 0, run_id: str | None = None, limit: int = 500) -> tuple[dict[str, object], ...]:
        query = "SELECT payload_json FROM agent_events WHERE session_id=? AND sequence>?"
        params: list[object] = [session_id, max(0, int(after_sequence))]
        if run_id:
            query += " AND run_id=?"
            params.append(run_id)
        query += " ORDER BY sequence LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with closing(self._connect()) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return tuple(_decode(row[0]) for row in rows)

    def verify_journal(self, session_id: str) -> dict[str, object]:
        with closing(self._connect()) as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN")
            return self._verify_snapshot(connection, session_id)

    def _verify_snapshot(self, connection, session_id: str) -> dict[str, object]:
        head = connection.execute("SELECT last_sequence,last_checksum,health FROM agent_session_heads WHERE session_id=?", (session_id,)).fetchone()
        if head and head[2] == "quarantined":
            return {"healthy": False, "reason": "quarantine_persisted"}
        rows = connection.execute(
            "SELECT sequence,payload_hash,previous_checksum,event_checksum,payload_json,event_id,event_version,run_id,node_id,event_type FROM agent_events WHERE session_id=? ORDER BY sequence",
            (session_id,),
        )
        previous = ""
        expected_sequence = 1
        for row in rows:
            sequence = int(row[0])
            if sequence != expected_sequence:
                return {"healthy": False, "reason": "sequence_gap", "sequence": sequence}
            if str(row[2] or "") != previous:
                return {"healthy": False, "reason": "checksum_parent_mismatch", "sequence": sequence}
            try:
                payload = _decode(row[4])
            except (ValueError, TypeError):
                return {"healthy": False, "reason": "payload_invalid", "sequence": sequence}
            if _payload_hash(payload) != str(row[1]):
                return {"healthy": False, "reason": "payload_hash_mismatch", "sequence": sequence}
            if (payload.get("session_id") != session_id or payload.get("sequence") != sequence
                    or payload.get("session_seq") != sequence or payload.get("event_id") != row[5]
                    or payload.get("event_version") != row[6] or payload.get("run_id") != row[7]
                    or payload.get("node_id") != row[8] or payload.get("event_type", "activity.updated") != row[9]
                    or payload.get("previous_checksum") != row[2] or payload.get("event_checksum") != row[3]):
                return {"healthy": False, "reason": "event_identity_mismatch", "sequence": sequence}
            checksum = hashlib.sha256((previous + str(row[1] or "")).encode("utf-8")).hexdigest()
            if checksum != str(row[3] or ""):
                return {"healthy": False, "reason": "checksum_mismatch", "sequence": sequence}
            previous = checksum
            expected_sequence += 1
        count = expected_sequence - 1
        if (count and not head) or (head and (int(head[0]) != count or str(head[1]) != previous)):
            return {"healthy": False, "reason": "head_mismatch"}
        return {"healthy": True, "event_count": count, "last_sequence": count, "checksum": previous}

    def require_healthy_journal(self, session_id: str) -> dict[str, object]:
        result = self.verify_journal(session_id)
        if not result["healthy"]:
            self._quarantined[session_id] = str(result["reason"])
            with self._transaction() as connection:
                connection.execute("UPDATE agent_session_heads SET health='quarantined' WHERE session_id=?", (session_id,))
            raise ValueError("AGENT_JOURNAL_QUARANTINED")
        if session_id in self._quarantined:
            raise ValueError("AGENT_JOURNAL_QUARANTINED")
        return result

    def _observation_integrity(self, connection, session_id):
        """Read cache only. Authorization always calls require_healthy_journal."""
        head = connection.execute("SELECT last_sequence,last_checksum,health FROM agent_session_heads WHERE session_id=?", (session_id,)).fetchone()
        stamps = []
        for path in (self.path, Path(str(self.path) + "-wal")):
            try:
                info = Path(path).stat()
                # SQLite recreates an empty WAL even for a read connection.
                # Its timestamp does not signal changed journal bytes.
                stamps.append((info.st_mtime_ns, info.st_size) if info.st_size else None)
            except OSError:
                stamps.append(None)
        signature = (tuple(head) if head else None, tuple(stamps))
        cache = getattr(self, "_observation_checks", {})
        prior = cache.get(session_id)
        if prior and prior[0] == signature:
            return prior[1]
        result = self._verify_snapshot(connection, session_id)
        if len(cache) >= 128:
            cache.clear()
        cache[session_id] = (signature, result)
        self._observation_checks = cache
        return result

    def snapshot_events(self, session_id: str, *, run_id: str | None = None, after_sequence: int = 0, observation: bool = False):
        """Page a consistent read snapshot through its captured head."""
        with closing(self._connect()) as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN")
            result = self._observation_integrity(connection, session_id) if observation else self._verify_snapshot(connection, session_id)
            if not result["healthy"] or session_id in self._quarantined:
                self._quarantined[session_id] = str(result.get("reason") or "quarantined")
                raise ValueError("AGENT_JOURNAL_QUARANTINED")
            head = connection.execute("SELECT last_sequence FROM agent_session_heads WHERE session_id=?", (session_id,)).fetchone()
            boundary = int(head[0]) if head else 0
            cursor = max(0, int(after_sequence))
            while cursor < boundary:
                query = "SELECT sequence,payload_json FROM agent_events WHERE session_id=? AND sequence>? AND sequence<=?"
                params: list[object] = [session_id, cursor, boundary]
                if run_id:
                    query += " AND run_id=?"
                    params.append(run_id)
                rows = connection.execute(query + " ORDER BY sequence LIMIT 500", params).fetchall()
                if not rows:
                    break
                for row in rows:
                    cursor = int(row[0])
                    yield _decode(row[1])

    # ---- Durable input queue -----------------------------------------
    def enqueue_input(self, session_id: str, run_id: str | None, *, mode: str, content: str, source: str = "user") -> dict[str, object]:
        mode = str(mode).upper()
        if mode not in {"FOLLOW_UP", "STEER", "CONTEXT_INJECTION"}:
            raise ValueError("AGENT_QUEUE_MODE_INVALID")
        text = " ".join(str(content).split()).strip()
        if not text or len(text) > 8000:
            raise ValueError("AGENT_QUEUE_CONTENT_INVALID")
        item_id = f"queue-{uuid.uuid4().hex}"
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(insertion_sequence),0) FROM agent_input_queue WHERE session_id=?",
                (session_id,),
            ).fetchone()
            insertion_sequence = int(row[0]) + 1
            payload = {
                "item_id": item_id,
                "session_id": session_id,
                "run_id": run_id,
                "mode": mode,
                "source": source,
                "content": text,
                "status": "queued",
                "insertion_sequence": insertion_sequence,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            connection.execute(
                """INSERT INTO agent_input_queue(item_id,session_id,run_id,mode,status,insertion_sequence,payload_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (item_id, session_id, run_id, mode, "queued", insertion_sequence, _encode(payload), payload["created_at"], payload["updated_at"]),
            )
        self.append_event(session_id, {"event_type": "agent.inbox.inserted", "run_id": run_id, "queue_item_id": item_id, "mode": mode, "message": text[:500]})
        return payload

    def queue_items(self, session_id: str, *, run_id: str | None = None, status: str | None = "queued") -> tuple[dict[str, object], ...]:
        query = "SELECT payload_json FROM agent_input_queue WHERE session_id=?"
        params: list[object] = [session_id]
        if run_id is not None:
            query += " AND run_id=?"
            params.append(run_id)
        if status is not None:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY insertion_sequence"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return tuple(_decode(row[0]) for row in rows)

    def edit_queue_item(self, item_id: str, content: str) -> dict[str, object]:
        text = " ".join(str(content).split()).strip()
        if not text or len(text) > 8000:
            raise ValueError("AGENT_QUEUE_CONTENT_INVALID")
        with self._transaction() as connection:
            row = connection.execute("SELECT session_id,status,payload_json FROM agent_input_queue WHERE item_id=?", (item_id,)).fetchone()
            if not row:
                raise KeyError(item_id)
            if str(row[1]) != "queued":
                raise ValueError("AGENT_QUEUE_ITEM_NOT_EDITABLE")
            payload = _decode(row[2]); payload["content"] = text; payload["updated_at"] = utc_now()
            connection.execute("UPDATE agent_input_queue SET payload_json=?,updated_at=? WHERE item_id=?", (_encode(payload), payload["updated_at"], item_id))
            session_id = str(row[0])
        self.append_event(session_id, {"event_type": "agent.inbox.edited", "run_id": payload.get("run_id"), "queue_item_id": item_id, "message": text[:500]})
        return payload

    def remove_queue_item(self, item_id: str) -> dict[str, object]:
        with self._transaction() as connection:
            row = connection.execute("SELECT session_id,status,payload_json FROM agent_input_queue WHERE item_id=?", (item_id,)).fetchone()
            if not row:
                raise KeyError(item_id)
            if str(row[1]) != "queued":
                raise ValueError("AGENT_QUEUE_ITEM_NOT_REMOVABLE")
            payload = _decode(row[2]); payload["status"] = "removed"; payload["updated_at"] = utc_now()
            connection.execute("UPDATE agent_input_queue SET status='removed',payload_json=?,updated_at=? WHERE item_id=?", (_encode(payload), payload["updated_at"], item_id))
            session_id = str(row[0])
        self.append_event(session_id, {"event_type": "agent.inbox.removed", "run_id": payload.get("run_id"), "queue_item_id": item_id})
        return payload

    def claim_queue_items(self, session_id: str, *, run_id: str | None, modes: tuple[str, ...] = ("STEER",), limit: int = 16) -> tuple[dict[str, object], ...]:
        placeholders = ",".join("?" for _ in modes)
        params: list[object] = [session_id]
        query = f"SELECT item_id,payload_json FROM agent_input_queue WHERE session_id=? AND status='queued' AND mode IN ({placeholders})"
        params.extend(modes)
        if run_id is not None:
            query += " AND (run_id=? OR run_id IS NULL)"; params.append(run_id)
        query += " ORDER BY insertion_sequence LIMIT ?"; params.append(max(1, min(limit, 32)))
        claimed: list[dict[str, object]] = []
        with self._transaction() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
            for row in rows:
                payload = _decode(row[1]); payload["status"] = "claimed"; payload["claimed_at"] = utc_now(); payload["updated_at"] = payload["claimed_at"]
                payload["run_id"] = run_id or payload.get("run_id")
                connection.execute("UPDATE agent_input_queue SET run_id=?,status='claimed',payload_json=?,updated_at=? WHERE item_id=? AND status='queued'", (payload["run_id"], _encode(payload), payload["updated_at"], str(row[0])))
                claimed.append(payload)
        for payload in claimed:
            self.append_event(session_id, {"event_type": "agent.inbox.claimed", "run_id": payload.get("run_id"), "queue_item_id": payload.get("item_id"), "mode": payload.get("mode")})
        return tuple(claimed)

    # ---- Projection checkpoints --------------------------------------
    def acknowledge_guidance(self, run_id: str) -> None:
        with self._transaction() as connection:
            rows = connection.execute("SELECT item_id,payload_json FROM agent_input_queue WHERE run_id=? AND status='claimed'", (run_id,)).fetchall()
            for row in rows:
                payload = _decode(row[1]); payload["status"] = "acknowledged"
                connection.execute("UPDATE agent_input_queue SET status='acknowledged',payload_json=?,updated_at=? WHERE item_id=?", (_encode(payload), utc_now(), row[0]))

    def transfer_unacknowledged_guidance(self, source: str, replacement: str) -> None:
        with self._transaction() as connection:
            rows = connection.execute("SELECT item_id,payload_json FROM agent_input_queue WHERE run_id=? AND status IN ('claimed','queued')", (source,)).fetchall()
            for row in rows:
                payload = _decode(row[1]); payload.update(run_id=replacement, status="queued")
                connection.execute("UPDATE agent_input_queue SET run_id=?,status='queued',payload_json=?,updated_at=? WHERE item_id=?", (replacement, _encode(payload), utc_now(), row[0]))

    def save_projection_checkpoint(self, session_id: str, projection: str, *, session_seq: int, reducer_version: str, payload: Mapping[str, object]) -> None:
        checksum = hashlib.sha256(_encode(payload).encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO agent_projection_checkpoints(session_id,projection,session_seq,reducer_version,checksum,payload_json,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(session_id,projection) DO UPDATE SET session_seq=excluded.session_seq,reducer_version=excluded.reducer_version,checksum=excluded.checksum,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (session_id, projection, int(session_seq), reducer_version, checksum, _encode(payload), utc_now()),
            )

    def projection_checkpoint(self, session_id: str, projection: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT session_seq,reducer_version,checksum,payload_json FROM agent_projection_checkpoints WHERE session_id=? AND projection=?", (session_id, projection)).fetchone()
        if not row:
            return None
        payload = _decode(row[3])
        if hashlib.sha256(_encode(payload).encode("utf-8")).hexdigest() != str(row[2]):
            return None
        return {"session_seq": int(row[0]), "reducer_version": str(row[1]), "payload": payload}

    # ---- Safe agent-turn checkpoints --------------------------------
    def save_run_checkpoint(
        self,
        run_id: str,
        session_id: str,
        *,
        turn: int,
        session_seq: int,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        """Seal admission or a settled turn without authorizing effect replay."""
        turn_number = max(0, int(turn))
        sequence = max(1, int(session_seq))
        with self._transaction() as connection:
            event = connection.execute(
                "SELECT run_id,event_type,event_checksum FROM agent_events "
                "WHERE session_id=? AND sequence=?",
                (session_id, sequence),
            ).fetchone()
            if (
                not event
                or str(event[0] or "") != run_id
                or str(event[1] or "") not in {"turn.settled", "runtime.admitted", "tool.checkpoint"}
                or (str(event[1]) == "runtime.admitted" and turn_number != 0)
                or (str(event[1]) in {"turn.settled", "tool.checkpoint"} and turn_number == 0)
            ):
                raise ValueError("AGENT_CHECKPOINT_BOUNDARY_INVALID")
            current = connection.execute(
                "SELECT turn,session_seq,payload_json FROM agent_run_checkpoints WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if current and (
                turn_number < int(current[0]) or sequence < int(current[1])
            ):
                return _decode(current[2])
            checkpoint = dict(payload)
            checkpoint.update({
                "checkpoint_version": "forgex.agent-checkpoint.v1",
                "run_id": run_id,
                "session_id": session_id,
                "turn": turn_number,
                "session_seq": sequence,
                "event_checksum": str(event[2] or ""),
                "safe_boundary": str(event[1]),
                "effect_replay_allowed": False,
                "resume_mode": "replacement_run_only",
                "updated_at": utc_now(),
            })
            if isinstance(checkpoint.get("resume"), dict):
                checkpoint["checkpoint_version"] = "forgex.agent-checkpoint.v2"
                if str(event[1]) == "runtime.admitted":
                    checkpoint["checkpoint_version"] = "forgex.agent-checkpoint.v3"
                checkpoint["resume_mode"] = "user_confirmed_continuation"
                checkpoint["checkpoint_id"] = hashlib.sha256(_encode(checkpoint).encode()).hexdigest()
            connection.execute(
                """INSERT INTO agent_run_checkpoints(
                       run_id,session_id,turn,session_seq,event_checksum,payload_json,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(run_id) DO UPDATE SET
                       session_id=excluded.session_id,turn=excluded.turn,
                       session_seq=excluded.session_seq,event_checksum=excluded.event_checksum,
                       payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (
                    run_id, session_id, turn_number, sequence,
                    str(event[2] or ""), _encode(checkpoint), checkpoint["updated_at"],
                ),
            )
            if isinstance(checkpoint.get("resume"), dict) and str(event[1]) == "turn.settled":
                rows = connection.execute("SELECT item_id,payload_json FROM agent_input_queue WHERE run_id=? AND status='claimed'", (run_id,)).fetchall()
                for row in rows:
                    item = _decode(row[1]); item.update(status="acknowledged", updated_at=utc_now())
                    connection.execute("UPDATE agent_input_queue SET status='acknowledged',payload_json=?,updated_at=? WHERE item_id=?", (_encode(item), item["updated_at"], row[0]))
        return checkpoint

    def latest_run_checkpoint(self, run_id: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json FROM agent_run_checkpoints WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        checkpoint = _decode(row[0])
        if checkpoint.get("checkpoint_version") not in {"forgex.agent-checkpoint.v1","forgex.agent-checkpoint.v2","forgex.agent-checkpoint.v3"}:
            raise ValueError("AGENT_CHECKPOINT_VERSION_UNSUPPORTED")
        if checkpoint.get("checkpoint_version") in {"forgex.agent-checkpoint.v2","forgex.agent-checkpoint.v3"}:
            unsigned = dict(checkpoint)
            digest = unsigned.pop("checkpoint_id", None)
            if hashlib.sha256(_encode(unsigned).encode()).hexdigest() != digest:
                raise ValueError("AGENT_CHECKPOINT_CORRUPT")
        return checkpoint

    # ---- Compacted context artifacts --------------------------------
    def save_context_artifact(
        self,
        run_id: str,
        session_id: str,
        *,
        observation_count: int,
        content_sha256: str,
        observation_delta: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        """Persist one append-only segment of compacted model evidence.

        Artifacts form a per-run chain so advancing a checkpoint stores only
        newly omitted observations rather than repeatedly copying its prefix.
        The aggregate digest is produced by the runtime over the reconstructed
        observation list and is verified again when the artifact is read.
        """
        count = int(observation_count)
        digest = str(content_sha256).casefold()
        if count < 1 or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("AGENT_CONTEXT_ARTIFACT_DIGEST_INVALID")
        delta = [dict(item) for item in observation_delta]
        if not delta:
            raise ValueError("AGENT_CONTEXT_ARTIFACT_DELTA_EMPTY")
        delta_json = _encode_observation_list(delta)
        if len(delta_json.encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError("AGENT_CONTEXT_ARTIFACT_SEGMENT_TOO_LARGE")
        delta_sha256 = hashlib.sha256(delta_json.encode("utf-8")).hexdigest()
        artifact_id = "context-" + hashlib.sha256(
            f"{run_id}:{digest}".encode("utf-8")
        ).hexdigest()[:40]
        created_at = utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT artifact_id,parent_artifact_id,observation_count,content_sha256,created_at "
                "FROM agent_context_artifacts WHERE run_id=? AND content_sha256=?",
                (run_id, digest),
            ).fetchone()
            if existing:
                return _context_artifact_metadata(existing)
            previous = connection.execute(
                "SELECT artifact_id,observation_count FROM agent_context_artifacts "
                "WHERE run_id=? ORDER BY observation_count DESC,created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            previous_count = int(previous[1]) if previous else 0
            if count != previous_count + len(delta):
                raise ValueError("AGENT_CONTEXT_ARTIFACT_CHAIN_GAP")
            parent_id = str(previous[0]) if previous else None
            prefix = (
                _load_context_observations(connection, run_id, parent_id)
                if parent_id is not None
                else []
            )
            reconstructed = prefix + delta
            if len(reconstructed) != count:
                raise ValueError("AGENT_CONTEXT_ARTIFACT_COUNT_MISMATCH")
            if hashlib.sha256(
                _encode_observation_list(reconstructed).encode("utf-8")
            ).hexdigest() != digest:
                raise ValueError("AGENT_CONTEXT_ARTIFACT_DIGEST_MISMATCH")
            connection.execute(
                """INSERT INTO agent_context_artifacts(
                       artifact_id,run_id,session_id,parent_artifact_id,
                       observation_count,content_sha256,delta_sha256,
                       payload_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    artifact_id,
                    run_id,
                    session_id,
                    parent_id,
                    count,
                    digest,
                    delta_sha256,
                    delta_json,
                    created_at,
                ),
            )
        return {
            "artifact_id": artifact_id,
            "parent_artifact_id": parent_id,
            "observation_count": count,
            "content_sha256": digest,
            "created_at": created_at,
        }

    def context_artifact(self, run_id: str, artifact_id: str) -> dict[str, object]:
        """Load and cryptographically verify a complete compacted context."""
        with closing(self._connect()) as connection:
            target = connection.execute(
                "SELECT artifact_id,parent_artifact_id,observation_count,content_sha256,created_at "
                "FROM agent_context_artifacts WHERE artifact_id=? AND run_id=?",
                (artifact_id, run_id),
            ).fetchone()
            if not target:
                raise KeyError(artifact_id)
            observations = _load_context_observations(
                connection, run_id, str(target[0])
            )
        metadata = _context_artifact_metadata(target)
        if len(observations) != int(metadata["observation_count"]):
            raise ValueError("AGENT_CONTEXT_ARTIFACT_COUNT_MISMATCH")
        reconstructed = hashlib.sha256(
            _encode_observation_list(observations).encode("utf-8")
        ).hexdigest()
        if reconstructed != str(metadata["content_sha256"]):
            raise ValueError("AGENT_CONTEXT_ARTIFACT_DIGEST_MISMATCH")
        return {**metadata, "observations": observations, "verified": True}

    # ---- Memory / approvals ------------------------------------------
    def remember(self, entry: Mapping[str, object]) -> str:
        memory_id = str(entry["memory_id"])
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO project_memory(memory_id,project_id,status,source_hash,workspace_revision,payload_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(memory_id) DO UPDATE SET status=excluded.status,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (memory_id, str(entry["project_id"]), str(entry.get("status") or "active"), str(entry["source_hash"]), _optional(entry.get("workspace_revision")), _encode(entry), str(entry.get("created_at") or utc_now()), utc_now()),
            )
        return memory_id

    def memories(self, project_id: str, *, include_invalidated: bool = False) -> tuple[dict[str, object], ...]:
        query = "SELECT payload_json FROM project_memory WHERE project_id=?"; params: list[object] = [project_id]
        if not include_invalidated: query += " AND status='active'"
        query += " ORDER BY created_at,memory_id"
        with closing(self._connect()) as connection: rows = connection.execute(query, tuple(params)).fetchall()
        return tuple(_decode(row[0]) for row in rows)

    def invalidate_memory(self, memory_id: str, reason: str) -> None:
        with self._transaction() as connection:
            row = connection.execute("SELECT payload_json FROM project_memory WHERE memory_id=?", (memory_id,)).fetchone()
            if not row: return
            payload = _decode(row[0]); payload["status"] = "invalidated"; payload["invalidated_at"] = utc_now(); payload["invalidation_reason"] = reason[:256]
            connection.execute("UPDATE project_memory SET status='invalidated',payload_json=?,updated_at=? WHERE memory_id=?", (_encode(payload), utc_now(), memory_id))

    def save_approval(self, approval: Mapping[str, object]) -> None:
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO agent_approvals(approval_id,run_id,action_type,fingerprint,status,expires_at,payload_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (str(approval["approval_id"]), str(approval["run_id"]), str(approval["action_type"]), str(approval["fingerprint"]), str(approval["status"]), str(approval["expires_at"]), _encode(approval), str(approval["created_at"])),
            )

    def consume_approval(self, approval_id: str, *, fingerprint: str) -> dict[str, object]:
        with self._transaction() as connection:
            row = connection.execute("SELECT status,fingerprint,payload_json FROM agent_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if not row: raise ValueError("AGENT_APPROVAL_NOT_FOUND")
            if str(row[0]) != "pending": raise ValueError("AGENT_APPROVAL_ALREADY_CONSUMED")
            if str(row[1]) != fingerprint: raise ValueError("AGENT_APPROVAL_FINGERPRINT_CHANGED")
            payload = _decode(row[2]); expires = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= expires:
                connection.execute("UPDATE agent_approvals SET status='expired' WHERE approval_id=? AND status='pending'", (approval_id,)); raise ValueError("AGENT_APPROVAL_EXPIRED")
            payload["status"] = "consumed"; payload["consumed_at"] = utc_now()
            connection.execute("UPDATE agent_approvals SET status='consumed',payload_json=? WHERE approval_id=? AND status='pending'", (_encode(payload), approval_id))
        return payload

    def invalidate_pending_approvals(self, run_id: str, reason: str) -> int:
        invalidated = 0
        with self._transaction() as connection:
            rows = connection.execute("SELECT approval_id,payload_json FROM agent_approvals WHERE run_id=? AND status='pending'", (run_id,)).fetchall()
            for row in rows:
                payload = _decode(row[1]); payload["status"] = "invalidated"; payload["invalidated_at"] = utc_now(); payload["invalidation_reason"] = reason[:256]
                connection.execute("UPDATE agent_approvals SET status='invalidated',payload_json=? WHERE approval_id=? AND status='pending'", (_encode(payload), str(row[0]))); invalidated += 1
        return invalidated

    # ---- Initialization / migration ----------------------------------
    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > 3:
                raise ValueError("AGENT_DATABASE_VERSION_UNSUPPORTED")
            if version < 2 and connection.execute("SELECT 1 FROM sqlite_master WHERE name='agent_events'").fetchone():
                backup = self.path.with_name(self.path.name + ".pre-hardening.bak")
                if not backup.exists():
                    with closing(sqlite3.connect(str(backup))) as target:
                        connection.backup(target)
            connection.execute("PRAGMA journal_mode=WAL")
            if version == 2:
                backup = self.path.with_name(self.path.name + ".pre-jobs-v3.bak")
                if not backup.exists():
                    with closing(sqlite3.connect(str(backup))) as target:
                        connection.backup(target)
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_runs(run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, project_id TEXT NOT NULL,status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_nodes(run_id TEXT NOT NULL, node_id TEXT NOT NULL, status TEXT NOT NULL,payload_json TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(run_id,node_id));
                CREATE TABLE IF NOT EXISTS agent_events(session_id TEXT NOT NULL, sequence INTEGER NOT NULL, run_id TEXT, node_id TEXT,event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,PRIMARY KEY(session_id,sequence));
                CREATE TABLE IF NOT EXISTS project_memory(memory_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL,source_hash TEXT NOT NULL, workspace_revision TEXT, payload_json TEXT NOT NULL,created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_approvals(approval_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action_type TEXT NOT NULL,fingerprint TEXT NOT NULL, status TEXT NOT NULL, expires_at TEXT NOT NULL,payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_session_heads(session_id TEXT PRIMARY KEY,last_sequence INTEGER NOT NULL DEFAULT 0,last_checksum TEXT NOT NULL DEFAULT '',health TEXT NOT NULL DEFAULT 'healthy',updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_session_leases(session_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL DEFAULT '',lease_epoch INTEGER NOT NULL DEFAULT 0,expires_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_input_queue(item_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,run_id TEXT,mode TEXT NOT NULL,status TEXT NOT NULL,insertion_sequence INTEGER NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_projection_checkpoints(session_id TEXT NOT NULL,projection TEXT NOT NULL,session_seq INTEGER NOT NULL,reducer_version TEXT NOT NULL,checksum TEXT NOT NULL,payload_json TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(session_id,projection));
                CREATE TABLE IF NOT EXISTS agent_run_checkpoints(run_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,turn INTEGER NOT NULL,session_seq INTEGER NOT NULL,event_checksum TEXT NOT NULL,payload_json TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_context_artifacts(artifact_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,session_id TEXT NOT NULL,parent_artifact_id TEXT,observation_count INTEGER NOT NULL,content_sha256 TEXT NOT NULL,delta_sha256 TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(run_id,content_sha256));
                CREATE TABLE IF NOT EXISTS agent_operations(operation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,kind TEXT NOT NULL,fingerprint TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_recoveries(source_run_id TEXT PRIMARY KEY,replacement_run_id TEXT NOT NULL UNIQUE,request_key TEXT NOT NULL,fingerprint TEXT NOT NULL,status TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_jobs(job_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,session_id TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_project_trust(project_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_job_requests(job_id TEXT NOT NULL,request_key TEXT NOT NULL,fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(job_id,request_key));
                """
            )
            # Additive migration for pre-canonical databases.  Columns must be
            # added before indexes reference them.
            request_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(agent_job_requests)")}
            if "payload_json" not in request_columns:
                connection.execute("ALTER TABLE agent_job_requests ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(agent_events)").fetchall()}
            additions = {
                "event_id": "TEXT",
                "event_version": "INTEGER NOT NULL DEFAULT 1",
                "lease_epoch": "INTEGER NOT NULL DEFAULT 0",
                "idempotency_key": "TEXT",
                "previous_checksum": "TEXT NOT NULL DEFAULT ''",
                "event_checksum": "TEXT NOT NULL DEFAULT ''",
                "payload_hash": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE agent_events ADD COLUMN {name} {declaration}")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_events_run ON agent_events(run_id,sequence)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_activity_correlation ON agent_events(run_id,json_extract(payload_json, '$.activity_record.correlation'),sequence)")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_events_event_id ON agent_events(event_id) WHERE event_id IS NOT NULL")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_events_idempotency ON agent_events(session_id,idempotency_key) WHERE idempotency_key IS NOT NULL")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_project_memory_project ON project_memory(project_id,status,created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_approvals_run ON agent_approvals(run_id,status)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_input_queue_session ON agent_input_queue(session_id,status,insertion_sequence)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_run_checkpoints_session ON agent_run_checkpoints(session_id,session_seq)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_context_artifacts_run ON agent_context_artifacts(run_id,observation_count)")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_context_artifacts_position ON agent_context_artifacts(run_id,observation_count)")
            # Only rows without ANY canonical identity may be migrated. Existing
            # evidence is never rewritten, even if its checksum is damaged.
            sessions = connection.execute("SELECT DISTINCT session_id FROM agent_events ORDER BY session_id").fetchall()
            for session_row in sessions:
                sid = str(session_row[0]); previous = ""; last_seq = 0
                rows = connection.execute("SELECT rowid,sequence,payload_json,event_id,payload_hash,event_checksum FROM agent_events WHERE session_id=? ORDER BY sequence", (sid,)).fetchall()
                legacy = version < 2 and all(not row[3] and not row[4] and not row[5] for row in rows)
                if not legacy:
                    continue
                for row in rows:
                    rowid, seq, payload_json = int(row[0]), int(row[1]), str(row[2])
                    event_id = f"event-{uuid.uuid4().hex}"
                    payload = _decode(payload_json)
                    payload.update(event_id=event_id, event_version=1, session_id=sid, sequence=seq, session_seq=seq, previous_checksum=previous)
                    payload_hash = _payload_hash(payload)
                    checksum = hashlib.sha256((previous + payload_hash).encode('utf-8')).hexdigest()
                    payload["event_checksum"] = checksum
                    connection.execute("UPDATE agent_events SET payload_json=?,event_id=?,payload_hash=?,previous_checksum=?,event_checksum=? WHERE rowid=?", (_encode(payload), event_id, payload_hash, previous, checksum, rowid))
                    previous = checksum; last_seq = seq
                connection.execute("""INSERT INTO agent_session_heads(session_id,last_sequence,last_checksum,health,updated_at) VALUES(?,?,?,?,?)
                    ON CONFLICT(session_id) DO UPDATE SET last_sequence=excluded.last_sequence,last_checksum=excluded.last_checksum,updated_at=excluded.updated_at""", (sid,last_seq,previous,"healthy",utc_now()))
            connection.execute("PRAGMA user_version=3")
            connection.commit()
        for session_row in sessions:
            sid = str(session_row[0])
            try:
                self.require_healthy_journal(sid)
            except ValueError:
                # Preserve damaged history and keep healthy sessions available.
                continue

    def _assert_lease_tx(self, connection: sqlite3.Connection, grant: LeaseGrant) -> None:
        row = connection.execute("SELECT owner_id,lease_epoch,expires_at FROM agent_session_leases WHERE session_id=?", (grant.session_id,)).fetchone()
        if not row or str(row[0]) != grant.owner_id or int(row[1]) != grant.lease_epoch:
            raise ValueError("AGENT_SESSION_LEASE_FENCED")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(str(row[2]).replace("Z", "+00:00")):
            raise ValueError("AGENT_SESSION_LEASE_EXPIRED")

    def operation(self, operation_id: str, run_id: str, kind: str, *, status: str, payload: Mapping[str, object]) -> dict[str, object]:
        if status not in {"prepared", "started", "completed", "failed", "unknown"}:
            raise ValueError("AGENT_OPERATION_STATUS_INVALID")
        fingerprint = hashlib.sha256(_encode(payload).encode()).hexdigest()
        with self._transaction() as connection:
            row = connection.execute("SELECT fingerprint,status,payload_json FROM agent_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ValueError("AGENT_OPERATION_CONFLICT")
                if row[1] in {"completed", "failed", "unknown"} and row[1] != status:
                    raise ValueError("AGENT_OPERATION_ALREADY_SETTLED")
                connection.execute("UPDATE agent_operations SET status=?,updated_at=? WHERE operation_id=?", (status, utc_now(), operation_id))
            else:
                if status not in {"prepared", "started"}:
                    raise ValueError("AGENT_OPERATION_NOT_PREPARED")
                connection.execute("INSERT INTO agent_operations VALUES(?,?,?,?,?,?,?)", (operation_id, run_id, kind, fingerprint, status, _encode(payload), utc_now()))
        return {"operation_id": operation_id, "kind": kind, "status": status}

    def operations(self, run_id: str) -> tuple[dict[str, object], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT operation_id,kind,status,payload_json FROM agent_operations WHERE run_id=? ORDER BY updated_at,operation_id", (run_id,)).fetchall()
        return tuple({"operation_id": row[0], "kind": row[1], "status": row[2], "payload": _decode(row[3])} for row in rows)

    def interrupt_operations(self, run_id: str) -> None:
        with self._transaction() as connection:
            connection.execute("UPDATE agent_operations SET status='unknown',updated_at=? WHERE run_id=? AND status='started'", (utc_now(), run_id))

    def reserve_recovery(self, run_id: str, key: str, fingerprint: str) -> str:
        with self._transaction() as connection:
            row = connection.execute("SELECT replacement_run_id,request_key,fingerprint FROM agent_recoveries WHERE source_run_id=?", (run_id,)).fetchone()
            if row:
                if row[2] != fingerprint:
                    raise ValueError("AGENT_RECOVERY_IDEMPOTENCY_CONFLICT")
                return str(row[0])
            replacement = "agent-run-" + uuid.uuid4().hex
            connection.execute("INSERT INTO agent_recoveries VALUES(?,?,?,?,?,?)", (run_id, replacement, key, fingerprint, "prepared", utc_now()))
        return replacement

    def complete_recovery(self, run_id: str, replacement: str) -> None:
        with self._transaction() as connection:
            connection.execute("UPDATE agent_recoveries SET status='completed',updated_at=? WHERE source_run_id=? AND replacement_run_id=?", (utc_now(), run_id, replacement))

    def recovery_replacement(self, run_id: str) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT replacement_run_id FROM agent_recoveries WHERE source_run_id=?", (run_id,)).fetchone()
        return str(row[0]) if row else None

    def _connect(self) -> sqlite3.Connection:
        current = getattr(self._transaction_state, "connection", None)
        if current is not None:
            return _BorrowedConnection(current)
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _transaction(self):
        return _Transaction(self)

    @contextmanager
    def atomic(self):
        """Join related writes explicitly; publish notifications after commit."""
        previous = getattr(self._transaction_state, "allow_join", False)
        self._transaction_state.allow_join = True
        try:
            with self._transaction() as connection:
                yield connection
        finally:
            self._transaction_state.allow_join = previous


class _BorrowedConnection:
    """A read helper cannot close the transaction that owns its connection."""
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def close(self):
        pass


class _Transaction:
    def __init__(self, store: AgentOrchestrationStore) -> None:
        self.store = store; self.connection: sqlite3.Connection | None = None
    def __enter__(self) -> sqlite3.Connection:
        self.started = time.monotonic()
        self.store._lock.acquire()
        self.locked_at = time.monotonic()
        try:
            if getattr(self.store._transaction_state, "active", False):
                if not getattr(self.store._transaction_state, "allow_join", False):
                    raise RuntimeError("Nested orchestration store transactions are not supported")
                self.connection = self.store._transaction_state.connection
                self.savepoint = "joined_" + uuid.uuid4().hex
                self.notifications_before = set(self.store._transaction_state.notifications)
                self.connection.execute("SAVEPOINT " + self.savepoint)
                return self.connection
            self.connection = self.store._connect()
            self.connection.execute("BEGIN IMMEDIATE")
            self.transaction_at = time.monotonic()
            self.store._transaction_state.active = True
            self.store._transaction_state.connection = self.connection
            self.store._transaction_state.notifications = set()
            return self.connection
        except BaseException:
            try:
                if self.connection is not None and not hasattr(self, "savepoint"):
                    self.connection.close()
                    self.connection = None
            finally:
                self.store._lock.release()
            raise
    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self.connection is not None
        if hasattr(self, "savepoint"):
            try:
                if exc_type:
                    self.connection.execute("ROLLBACK TO " + self.savepoint)
                    self.store._transaction_state.notifications = self.notifications_before
                self.connection.execute("RELEASE " + self.savepoint)
            finally:
                self.store._lock.release()
            return
        committed = False
        notifications = self.store._transaction_state.notifications
        try: self.connection.rollback() if exc_type else self.connection.commit()
        # Notification callbacks may open another connection. They must run
        # after releasing the writer, and never observe a rolled-back event.
        # A failed commit skips publication as well.
        except BaseException:
            raise
        else:
            committed = exc_type is None
        finally:
            try:
                self.connection.close()
            finally:
                self.store._transaction_state.active = False
                self.store._transaction_state.connection = None
                self.store._lock.release()
                finished = time.monotonic()
                if finished - self.started >= .1:
                    from backend.observability.diagnostics import emit_diagnostic_event
                    emit_diagnostic_event(component="storage", event="storage.transaction.slow", level="WARNING",
                        duration_ms=round((finished - self.started) * 1000),
                        thread_lock_wait_ms=round((self.locked_at - self.started) * 1000),
                        database_lock_wait_ms=round((self.transaction_at - self.locked_at) * 1000),
                        transaction_ms=round((finished - self.transaction_at) * 1000))
        if committed:
            for session_id in notifications:
                self.store.notify_observers(session_id)


def _encode(value: Mapping[str, object]) -> str:
    return json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))

def _decode(value: object) -> dict[str, object]:
    decoded = json.loads(str(value))
    if not isinstance(decoded, dict): raise ValueError("AGENT_STORE_PAYLOAD_INVALID")
    return decoded

def _encode_observation_list(value: Sequence[Mapping[str, object]]) -> str:
    # Keep this byte-for-byte compatible with the autonomous runtime digest.
    return json.dumps(
        [dict(item) for item in value],
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    )

def _decode_observation_list(value: object) -> list[dict[str, object]]:
    decoded = json.loads(str(value))
    if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
        raise ValueError("AGENT_CONTEXT_ARTIFACT_PAYLOAD_INVALID")
    return [dict(item) for item in decoded]

def _context_artifact_metadata(row: sqlite3.Row) -> dict[str, object]:
    return {
        "artifact_id": str(row[0]),
        "parent_artifact_id": str(row[1]) if row[1] else None,
        "observation_count": int(row[2]),
        "content_sha256": str(row[3]),
        "created_at": str(row[4]),
    }

def _load_context_observations(
    connection: sqlite3.Connection,
    run_id: str,
    artifact_id: str,
) -> list[dict[str, object]]:
    segments: list[list[dict[str, object]]] = []
    seen: set[str] = set()
    current_id: str | None = artifact_id
    while current_id is not None:
        if current_id in seen or len(seen) >= 4096:
            raise ValueError("AGENT_CONTEXT_ARTIFACT_CHAIN_INVALID")
        seen.add(current_id)
        row = connection.execute(
            "SELECT parent_artifact_id,delta_sha256,payload_json "
            "FROM agent_context_artifacts WHERE artifact_id=? AND run_id=?",
            (current_id, run_id),
        ).fetchone()
        if not row:
            raise ValueError("AGENT_CONTEXT_ARTIFACT_PARENT_MISSING")
        payload_json = str(row[2])
        if hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != str(row[1]):
            raise ValueError("AGENT_CONTEXT_ARTIFACT_SEGMENT_CORRUPT")
        segments.append(_decode_observation_list(payload_json))
        current_id = str(row[0]) if row[0] else None
    return [item for segment in reversed(segments) for item in segment]

def _optional(value: object) -> str | None:
    return str(value) if value is not None and str(value) else None

def _payload_hash(value: Mapping[str, object]) -> str:
    sanitized = dict(value)
    for key in ("event_checksum", "previous_checksum", "sequence", "session_seq"):
        sanitized.pop(key, None)
    return hashlib.sha256(_encode(sanitized).encode("utf-8")).hexdigest()

def _fmt(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
