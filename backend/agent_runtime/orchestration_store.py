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
import uuid
from contextlib import closing
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
        self._initialize()

    # ---- Runs / graph -------------------------------------------------
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
        ttl = max(5, min(int(ttl_seconds), 300))
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl)
        with self._transaction() as connection:
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
        ttl = max(5, min(int(ttl_seconds), 300))
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl)
        with self._transaction() as connection:
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
    ) -> dict[str, object]:
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
                "SELECT last_sequence,last_checksum FROM agent_session_heads WHERE session_id=?",
                (session_id,),
            ).fetchone()
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
        return event

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
            rows = connection.execute(
                "SELECT sequence,payload_hash,previous_checksum,event_checksum FROM agent_events WHERE session_id=? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        previous = ""
        expected_sequence = 1
        for row in rows:
            sequence = int(row[0])
            if sequence != expected_sequence:
                return {"healthy": False, "reason": "sequence_gap", "sequence": sequence}
            if str(row[2] or "") != previous:
                return {"healthy": False, "reason": "checksum_parent_mismatch", "sequence": sequence}
            checksum = hashlib.sha256((previous + str(row[1] or "")).encode("utf-8")).hexdigest()
            if checksum != str(row[3] or ""):
                return {"healthy": False, "reason": "checksum_mismatch", "sequence": sequence}
            previous = checksum
            expected_sequence += 1
        return {"healthy": True, "event_count": len(rows), "last_sequence": expected_sequence - 1, "checksum": previous}

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
                connection.execute("UPDATE agent_input_queue SET status='claimed',payload_json=?,updated_at=? WHERE item_id=? AND status='queued'", (_encode(payload), payload["updated_at"], str(row[0])))
                claimed.append(payload)
        for payload in claimed:
            self.append_event(session_id, {"event_type": "agent.inbox.claimed", "run_id": payload.get("run_id"), "queue_item_id": payload.get("item_id"), "mode": payload.get("mode")})
        return tuple(claimed)

    # ---- Projection checkpoints --------------------------------------
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
        """Record a settled turn without authorizing effect replay."""
        turn_number = max(1, int(turn))
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
                or str(event[1] or "") != "turn.settled"
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
                "safe_boundary": "turn.settled",
                "effect_replay_allowed": False,
                "resume_mode": "replacement_run_only",
                "updated_at": utc_now(),
            })
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
        return checkpoint

    def latest_run_checkpoint(self, run_id: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json FROM agent_run_checkpoints WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return _decode(row[0]) if row else None

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
            connection.execute("PRAGMA journal_mode=WAL")
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
                """
            )
            # Additive migration for pre-canonical databases.  Columns must be
            # added before indexes reference them.
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
            # Backfill journal identities/checksums once for legacy rows.
            sessions = connection.execute("SELECT DISTINCT session_id FROM agent_events ORDER BY session_id").fetchall()
            for session_row in sessions:
                sid = str(session_row[0]); previous = ""; last_seq = 0
                rows = connection.execute("SELECT rowid,sequence,payload_json,event_id,payload_hash,event_checksum FROM agent_events WHERE session_id=? ORDER BY sequence", (sid,)).fetchall()
                for row in rows:
                    rowid, seq, payload_json = int(row[0]), int(row[1]), str(row[2])
                    event_id = str(row[3] or f"event-{uuid.uuid4().hex}")
                    payload_hash = str(row[4] or hashlib.sha256(payload_json.encode('utf-8')).hexdigest())
                    checksum = hashlib.sha256((previous + payload_hash).encode('utf-8')).hexdigest()
                    connection.execute("UPDATE agent_events SET event_id=?,payload_hash=?,previous_checksum=?,event_checksum=? WHERE rowid=?", (event_id, payload_hash, previous, checksum, rowid))
                    previous = checksum; last_seq = seq
                connection.execute("""INSERT INTO agent_session_heads(session_id,last_sequence,last_checksum,health,updated_at) VALUES(?,?,?,?,?)
                    ON CONFLICT(session_id) DO UPDATE SET last_sequence=excluded.last_sequence,last_checksum=excluded.last_checksum,updated_at=excluded.updated_at""", (sid,last_seq,previous,"healthy",utc_now()))
            connection.commit()

    def _assert_lease_tx(self, connection: sqlite3.Connection, grant: LeaseGrant) -> None:
        row = connection.execute("SELECT owner_id,lease_epoch,expires_at FROM agent_session_leases WHERE session_id=?", (grant.session_id,)).fetchone()
        if not row or str(row[0]) != grant.owner_id or int(row[1]) != grant.lease_epoch:
            raise ValueError("AGENT_SESSION_LEASE_FENCED")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(str(row[2]).replace("Z", "+00:00")):
            raise ValueError("AGENT_SESSION_LEASE_EXPIRED")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _transaction(self):
        return _Transaction(self)


class _Transaction:
    def __init__(self, store: AgentOrchestrationStore) -> None:
        self.store = store; self.connection: sqlite3.Connection | None = None
    def __enter__(self) -> sqlite3.Connection:
        self.store._lock.acquire()
        try:
            if getattr(self.store._transaction_state, "active", False):
                raise RuntimeError("Nested orchestration store transactions are not supported")
            self.connection = self.store._connect()
            self.connection.execute("BEGIN IMMEDIATE")
            self.store._transaction_state.active = True
            return self.connection
        except BaseException:
            try:
                if self.connection is not None:
                    self.connection.close()
                    self.connection = None
            finally:
                self.store._lock.release()
            raise
    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self.connection is not None
        try: self.connection.rollback() if exc_type else self.connection.commit()
        finally:
            try:
                self.connection.close()
            finally:
                self.store._transaction_state.active = False
                self.store._lock.release()


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
