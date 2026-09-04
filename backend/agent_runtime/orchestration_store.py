"""SQLite persistence for orchestrated runs, nodes, events, and project memory."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AgentOrchestrationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

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

    def append_event(self, session_id: str, payload: Mapping[str, object]) -> dict[str, object]:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM agent_events WHERE session_id=?", (session_id,)
            ).fetchone()
            sequence = int(row[0]) + 1
            event = dict(payload)
            event["session_id"] = session_id
            event["sequence"] = sequence
            event.setdefault("created_at", utc_now())
            event.setdefault("timestamp", event["created_at"])
            connection.execute(
                "INSERT INTO agent_events(session_id,sequence,run_id,node_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    session_id,
                    sequence,
                    _optional(event.get("run_id")),
                    _optional(event.get("node_id")),
                    str(event.get("event_type") or "activity.updated"),
                    _encode(event),
                    str(event["created_at"]),
                ),
            )
        return event

    def events(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
        run_id: str | None = None,
        limit: int = 500,
    ) -> tuple[dict[str, object], ...]:
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

    def remember(self, entry: Mapping[str, object]) -> str:
        memory_id = str(entry["memory_id"])
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO project_memory(memory_id,project_id,status,source_hash,workspace_revision,payload_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(memory_id) DO UPDATE SET status=excluded.status,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (
                    memory_id,
                    str(entry["project_id"]),
                    str(entry.get("status") or "active"),
                    str(entry["source_hash"]),
                    _optional(entry.get("workspace_revision")),
                    _encode(entry),
                    str(entry.get("created_at") or utc_now()),
                    utc_now(),
                ),
            )
        return memory_id

    def memories(self, project_id: str, *, include_invalidated: bool = False) -> tuple[dict[str, object], ...]:
        query = "SELECT payload_json FROM project_memory WHERE project_id=?"
        params: list[object] = [project_id]
        if not include_invalidated:
            query += " AND status='active'"
        query += " ORDER BY created_at,memory_id"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return tuple(_decode(row[0]) for row in rows)

    def invalidate_memory(self, memory_id: str, reason: str) -> None:
        with self._transaction() as connection:
            row = connection.execute("SELECT payload_json FROM project_memory WHERE memory_id=?", (memory_id,)).fetchone()
            if not row:
                return
            payload = _decode(row[0])
            payload["status"] = "invalidated"
            payload["invalidated_at"] = utc_now()
            payload["invalidation_reason"] = reason[:256]
            connection.execute(
                "UPDATE project_memory SET status='invalidated',payload_json=?,updated_at=? WHERE memory_id=?",
                (_encode(payload), utc_now(), memory_id),
            )

    def save_approval(self, approval: Mapping[str, object]) -> None:
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO agent_approvals(approval_id,run_id,action_type,fingerprint,status,expires_at,payload_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    str(approval["approval_id"]),
                    str(approval["run_id"]),
                    str(approval["action_type"]),
                    str(approval["fingerprint"]),
                    str(approval["status"]),
                    str(approval["expires_at"]),
                    _encode(approval),
                    str(approval["created_at"]),
                ),
            )

    def consume_approval(self, approval_id: str, *, fingerprint: str) -> dict[str, object]:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT status,fingerprint,payload_json FROM agent_approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
            if not row:
                raise ValueError("AGENT_APPROVAL_NOT_FOUND")
            if str(row[0]) != "pending":
                raise ValueError("AGENT_APPROVAL_ALREADY_CONSUMED")
            if str(row[1]) != fingerprint:
                raise ValueError("AGENT_APPROVAL_FINGERPRINT_CHANGED")
            payload = _decode(row[2])
            expires = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= expires:
                connection.execute(
                    "UPDATE agent_approvals SET status='expired' WHERE approval_id=? AND status='pending'",
                    (approval_id,),
                )
                raise ValueError("AGENT_APPROVAL_EXPIRED")
            payload["status"] = "consumed"
            payload["consumed_at"] = utc_now()
            connection.execute(
                "UPDATE agent_approvals SET status='consumed',payload_json=? WHERE approval_id=? AND status='pending'",
                (_encode(payload), approval_id),
            )
        return payload

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS agent_runs(
                    run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_nodes(
                    run_id TEXT NOT NULL, node_id TEXT NOT NULL, status TEXT NOT NULL,
                    payload_json TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(run_id,node_id)
                );
                CREATE TABLE IF NOT EXISTS agent_events(
                    session_id TEXT NOT NULL, sequence INTEGER NOT NULL, run_id TEXT, node_id TEXT,
                    event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(session_id,sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_events_run ON agent_events(run_id,sequence);
                CREATE TABLE IF NOT EXISTS project_memory(
                    memory_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL,
                    source_hash TEXT NOT NULL, workspace_revision TEXT, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_project_memory_project ON project_memory(project_id,status,created_at);
                CREATE TABLE IF NOT EXISTS agent_approvals(
                    approval_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action_type TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, status TEXT NOT NULL, expires_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_approvals_run ON agent_approvals(run_id,status);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _transaction(self):
        return _Transaction(self)


class _Transaction:
    def __init__(self, store: AgentOrchestrationStore) -> None:
        self.store = store
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self.store._lock.acquire()
        self.connection = self.store._connect()
        self.connection.execute("BEGIN IMMEDIATE")
        return self.connection

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self.connection is not None
        try:
            self.connection.rollback() if exc_type else self.connection.commit()
        finally:
            self.connection.close()
            self.store._lock.release()


def _encode(value: Mapping[str, object]) -> str:
    return json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _decode(value: object) -> dict[str, object]:
    decoded = json.loads(str(value))
    if not isinstance(decoded, dict):
        raise ValueError("AGENT_STORE_PAYLOAD_INVALID")
    return decoded


def _optional(value: object) -> str | None:
    return str(value) if value is not None and str(value) else None
