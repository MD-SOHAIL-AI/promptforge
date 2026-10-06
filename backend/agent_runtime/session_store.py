"""Persistent multi-turn Forge agent sessions."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from .public_progress import public_text

AGENT_SESSION_TERMINAL_STATUSES = frozenset(
    {"completed", "completed_with_warning", "failed", "cancelled", "blocked", "timed_out"}
)
AGENT_MESSAGE_ROLES = frozenset({"user", "assistant", "tool", "system"})
AGENT_SESSION_SCHEMA_VERSION = 2
CHAT_TITLE_MAX_LENGTH = 64
CHAT_GREETING_TITLES = frozenset(
    {
        "hi",
        "hi bro",
        "hi there",
        "hello",
        "hello bro",
        "hello there",
        "hey",
        "hey bro",
        "hey there",
        "howdy",
        "sup",
        "yo",
        "good morning",
        "good afternoon",
        "good evening",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class AgentMessage:
    message_id: str
    role: str
    content: str
    created_at: str = field(default_factory=utc_now)
    run_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "run_id": self.run_id,
            "metadata": _safe_metadata(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AgentMessage | None":
        message_id = value.get("message_id")
        role = value.get("role")
        content = value.get("content")
        if (
            not isinstance(message_id, str)
            or not isinstance(role, str)
            or not isinstance(content, str)
        ):
            return None
        if role not in AGENT_MESSAGE_ROLES:
            return None
        created_at = value.get("created_at")
        run_id = value.get("run_id")
        metadata = value.get("metadata")
        return cls(
            message_id=message_id,
            role=role,
            content=public_text(content, 64000) if role == "assistant" else content,
            created_at=created_at if isinstance(created_at, str) else utc_now(),
            run_id=run_id if isinstance(run_id, str) else None,
            metadata=_safe_metadata(metadata if isinstance(metadata, Mapping) else {}),
        )


@dataclass(frozen=True, slots=True)
class AgentPlanRecord:
    plan_id: str
    revision: int
    plan_hash: str
    source_run_id: str
    status: str
    steps: tuple[Mapping[str, str], ...]
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "revision": self.revision,
            "plan_hash": self.plan_hash,
            "source_run_id": self.source_run_id,
            "status": self.status,
            "steps": [dict(item) for item in self.steps],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AgentPlanRecord | None":
        try:
            plan_id = str(value["plan_id"])
            revision = int(value["revision"])
            plan_hash = str(value["plan_hash"])
            source_run_id = str(value["source_run_id"])
            status = str(value.get("status") or "ready")
            raw_steps = value.get("steps")
        except (KeyError, TypeError, ValueError):
            return None
        if (
            revision < 1
            or status not in {"ready", "approved", "superseded"}
            or not isinstance(raw_steps, list)
        ):
            return None
        steps: list[Mapping[str, str]] = []
        for item in raw_steps[:32]:
            if not isinstance(item, Mapping):
                continue
            text = str(item.get("text") or "").strip()
            step_status = str(item.get("status") or "pending")
            if text:
                steps.append({"text": text[:300], "status": step_status[:32]})
        if not steps:
            return None
        return cls(
            plan_id,
            revision,
            plan_hash,
            source_run_id,
            status,
            tuple(steps),
            str(value.get("created_at") or utc_now()),
        )


@dataclass(frozen=True, slots=True)
class AgentSession:
    session_id: str
    project_id: str | None
    title: str
    status: str = "idle"
    active_run_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    messages: tuple[AgentMessage, ...] = field(default_factory=tuple)
    current_plan: AgentPlanRecord | None = None
    kind: str = "agent"
    provider_id: str | None = None
    model_id: str | None = None
    title_source: str = "auto"
    pending_clarification: Mapping[str, object] | None = None

    def to_dict(self, *, include_messages: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": AGENT_SESSION_SCHEMA_VERSION,
            "kind": self.kind,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "title": self.title,
            "title_source": self.title_source,
            "pending_clarification": dict(self.pending_clarification) if self.pending_clarification else None,
            "status": self.status,
            "active_run_id": self.active_run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
            "current_plan": (
                self.current_plan.to_dict() if self.current_plan is not None else None
            ),
        }
        if include_messages:
            value["messages"] = [message.to_dict() for message in self.messages]
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AgentSession | None":
        session_id = value.get("session_id")
        project_id = value.get("project_id")
        title = value.get("title")
        if (
            not isinstance(session_id, str)
            or (
                not isinstance(project_id, str)
                and not (project_id is None and value.get("kind") == "chat")
            )
            or not isinstance(title, str)
        ):
            return None
        messages_value = value.get("messages")
        messages: list[AgentMessage] = []
        if isinstance(messages_value, list):
            for item in messages_value:
                if isinstance(item, Mapping):
                    message = AgentMessage.from_dict(item)
                    if message is not None:
                        messages.append(message)
        status = value.get("status")
        active_run_id = value.get("active_run_id")
        created_at = value.get("created_at")
        updated_at = value.get("updated_at")
        return cls(
            session_id=session_id,
            project_id=project_id,
            title=title,
            status=status if isinstance(status, str) else "idle",
            active_run_id=active_run_id if isinstance(active_run_id, str) else None,
            created_at=created_at if isinstance(created_at, str) else utc_now(),
            updated_at=updated_at if isinstance(updated_at, str) else utc_now(),
            kind="chat" if value.get("kind") == "chat" else "agent",
            provider_id=value.get("provider_id"),
            model_id=value.get("model_id"),
            title_source="manual" if value.get("title_source") == "manual" else "auto",
            pending_clarification=value.get("pending_clarification") if isinstance(value.get("pending_clarification"), Mapping) else None,
            messages=tuple(messages),
            current_plan=(
                AgentPlanRecord.from_dict(value.get("current_plan"))
                if isinstance(value.get("current_plan"), Mapping)
                else None
            ),
        )


class AgentSessionStore:
    """SQLite-backed local session store with one-time JSON migration."""

    def __init__(
        self, path: str | Path, *, legacy_json_path: str | Path | None = None
    ) -> None:
        requested = Path(path)
        if requested.suffix.casefold() == ".json":
            self.path = requested.with_suffix(".sqlite3")
            self.legacy_json_path = requested
        else:
            self.path = requested
            self.legacy_json_path = (
                Path(legacy_json_path) if legacy_json_path is not None else None
            )
        self._lock = threading.RLock()
        self._initialize()
        self._sessions = self._load()
        if self._repair_auto_chat_titles():
            self._persist_unlocked()
        self._migrate_legacy_json_once()

    def create(
        self,
        *,
        project_id: str | None = None,
        title: str | None = None,
        kind: str = "agent",
    ) -> AgentSession:
        if kind not in {"agent", "chat"} or (kind == "agent" and not project_id):
            raise ValueError("AGENT_PROJECT_REQUIRED")
        session = AgentSession(
            kind=kind,
            session_id=f"agent-session-{uuid.uuid4().hex}",
            project_id=_identifier(project_id) if project_id else None,
            title=_title(
                title or ("New chat" if kind == "chat" else "New Forge session")
            ),
            title_source="manual" if title else "auto",
        )
        with self._lock:
            self._sessions[session.session_id] = session
            self._persist_unlocked()
        return session

    def list(
        self, *, project_id: str | None = None, kind: str = "agent"
    ) -> tuple[AgentSession, ...]:
        with self._lock:
            values = tuple(self._sessions.values())
        values = tuple(session for session in values if session.kind == kind)
        if project_id:
            values = tuple(
                session for session in values if session.project_id == project_id
            )
        return tuple(
            sorted(values, key=lambda session: session.updated_at, reverse=True)
        )

    def get(self, session_id: str) -> AgentSession:
        with self._lock:
            try:
                return self._sessions[_identifier(session_id, prefix="agent-session-")]
            except KeyError as exc:
                raise KeyError(session_id) from exc

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        run_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> AgentSession:
        if role not in AGENT_MESSAGE_ROLES:
            raise ValueError("AGENT_MESSAGE_ROLE_INVALID")
        text = _content(content)
        if role == "assistant":
            text = public_text(text, 64000)
        with self._lock:
            session = self.get(session_id)
            message = AgentMessage(
                message_id=f"agent-message-{uuid.uuid4().hex}",
                role=role,
                content=text,
                run_id=run_id,
                metadata=_safe_metadata(metadata or {}),
            )
            title = session.title
            if role == "user" and session.title_source == "auto":
                if not session.messages or title in {"New chat", "New Forge session"}:
                    title = _chat_title(text) if session.kind == "chat" else _title(text)
                elif (
                    session.kind == "chat"
                    and _is_chat_greeting(title)
                    and not _is_chat_greeting(text)
                ):
                    title = _chat_title(text)
            updated = replace(
                session,
                title=title,
                updated_at=utc_now(),
                messages=(*session.messages, message),
            )
            self._sessions[session_id] = updated
            self._persist_unlocked()
            return updated

    def set_clarification(self, session_id: str, pending: Mapping[str, object] | None) -> AgentSession:
        with self._lock:
            session = self.get(session_id)
            updated = replace(session, pending_clarification=dict(pending) if pending else None,
                              status="awaiting_clarification" if pending else "idle", updated_at=utc_now())
            self._sessions[session_id] = updated
            self._persist_unlocked(only_session_id=session_id)
            return updated

    def update_run(
        self,
        session_id: str,
        *,
        run_id: str | None,
        status: str,
        expected_run_id: str | None = None,
    ) -> AgentSession:
        with self._lock:
            session = self.get(session_id)
            if expected_run_id is not None and session.active_run_id != expected_run_id:
                return session
            updated = replace(
                session,
                status=status,
                active_run_id=run_id,
                updated_at=utc_now(),
            )
            self._sessions[session_id] = updated
            self._persist_unlocked()
            return updated

    def record_plan(
        self,
        session_id: str,
        *,
        run_id: str,
        steps: list[Mapping[str, object]] | tuple[Mapping[str, object], ...],
    ) -> AgentSession:
        clean: list[Mapping[str, str]] = []
        for item in steps[:32]:
            text = (
                str(item.get("text") or "").strip() if isinstance(item, Mapping) else ""
            )
            status = (
                str(item.get("status") or "pending")
                if isinstance(item, Mapping)
                else "pending"
            )
            if text:
                clean.append({"text": text[:300], "status": status[:32]})
        if not clean:
            raise ValueError("AGENT_PLAN_EMPTY")
        canonical = json.dumps(
            clean, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._lock:
            session = self.get(session_id)
            previous = session.current_plan
            revision = (previous.revision + 1) if previous is not None else 1
            plan = AgentPlanRecord(
                plan_id=f"agent-plan-{uuid.uuid4().hex}",
                revision=revision,
                plan_hash=digest,
                source_run_id=run_id,
                status="ready",
                steps=tuple(clean),
            )
            updated = replace(session, current_plan=plan, updated_at=utc_now())
            self._sessions[session_id] = updated
            self._persist_unlocked()
            return updated

    def approve_plan(
        self, session_id: str, *, plan_id: str, revision: int, plan_hash: str
    ) -> AgentPlanRecord:
        with self._lock:
            session = self.get(session_id)
            plan = session.current_plan
            if plan is None:
                raise ValueError("AGENT_PLAN_NOT_FOUND")
            if (
                plan.plan_id != plan_id
                or plan.revision != revision
                or plan.plan_hash != plan_hash
            ):
                raise ValueError("AGENT_PLAN_BINDING_MISMATCH")
            approved = replace(plan, status="approved")
            self._sessions[session_id] = replace(
                session, current_plan=approved, updated_at=utc_now()
            )
            self._persist_unlocked()
            return approved

    def _load(self) -> dict[str, AgentSession]:
        sessions: dict[str, AgentSession] = {}
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM agent_sessions ORDER BY updated_at, session_id"
            ).fetchall()
        for row in rows:
            try:
                value = json.loads(str(row[0]))
            except json.JSONDecodeError:
                continue
            session = (
                AgentSession.from_dict(value) if isinstance(value, Mapping) else None
            )
            if session is not None:
                sessions[session.session_id] = session
        return sessions

    def _repair_auto_chat_titles(self) -> bool:
        changed = False
        for identity, session in tuple(self._sessions.items()):
            if (
                session.kind != "chat"
                or session.title_source != "auto"
                or not _is_chat_greeting(session.title)
            ):
                continue
            meaningful = next(
                (
                    message.content
                    for message in session.messages
                    if message.role == "user"
                    and not _is_chat_greeting(message.content)
                ),
                None,
            )
            if meaningful is None:
                continue
            self._sessions[identity] = replace(
                session,
                title=_chat_title(meaningful),
            )
            changed = True
        return changed

    def _persist_unlocked(self, only_session_id: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for session_id, session in sorted(self._sessions.items()):
                if only_session_id is not None and session_id != only_session_id:
                    continue
                payload = json.dumps(
                    session.to_dict(include_messages=True),
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                connection.execute(
                    """INSERT INTO agent_sessions(session_id,project_id,status,payload_json,schema_version,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(session_id) DO UPDATE SET
                         project_id=excluded.project_id,
                         status=excluded.status,
                         payload_json=excluded.payload_json,
                         schema_version=excluded.schema_version,
                         updated_at=excluded.updated_at""",
                    (
                        session_id,
                        session.project_id,
                        session.status,
                        payload,
                        AGENT_SESSION_SCHEMA_VERSION,
                        session.created_at,
                        session.updated_at,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS agent_sessions(
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_sessions_project
                    ON agent_sessions(project_id,updated_at);
                CREATE TABLE IF NOT EXISTS agent_session_metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """)

            columns = connection.execute("PRAGMA table_info(agent_sessions)").fetchall()
            if any(row["name"] == "project_id" and row["notnull"] for row in columns):
                connection.executescript("""
                    BEGIN IMMEDIATE;
                    ALTER TABLE agent_sessions RENAME TO agent_sessions_v1;
                    DROP INDEX IF EXISTS idx_agent_sessions_project;
                    CREATE TABLE agent_sessions(session_id TEXT PRIMARY KEY, project_id TEXT, status TEXT NOT NULL, payload_json TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                    INSERT INTO agent_sessions SELECT * FROM agent_sessions_v1;
                    DROP TABLE agent_sessions_v1;
                    CREATE INDEX idx_agent_sessions_project ON agent_sessions(project_id,updated_at);
                    COMMIT;
                """)

    def update_message(
        self, session_id: str, message_id: str, *, content: str, status: str
    ) -> AgentSession:
        with self._lock:
            session = self.get(session_id)
            messages = tuple(
                (
                    replace(
                        m,
                        content=content[:64000],
                        metadata={**m.metadata, "status": status},
                    )
                    if m.message_id == message_id
                    else m
                )
                for m in session.messages
            )
            updated = replace(
                session,
                messages=messages,
                status="running" if status == "streaming" else "idle",
                updated_at=utc_now(),
            )
            self._sessions[session_id] = updated
            self._persist_unlocked(session_id)
            return updated

    def configure(
        self,
        session_id: str,
        *,
        title: str | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> AgentSession:
        with self._lock:
            session = self.get(session_id)
            updated = replace(
                session,
                title=_title(title) if title else session.title,
                title_source="manual" if title is not None else session.title_source,
                provider_id=(
                    provider_id if provider_id is not None else session.provider_id
                ),
                model_id=model_id if model_id is not None else session.model_id,
            )
            self._sessions[session_id] = updated
            self._persist_unlocked()
            return updated

    def clear_model_selection(self, session_id: str) -> AgentSession:
        with self._lock:
            updated = replace(self.get(session_id), provider_id=None, model_id=None)
            self._sessions[session_id] = updated
            self._persist_unlocked()
            return updated

    def delete(self, session_id: str) -> None:
        with self._lock:
            self.get(session_id)
            with closing(self._connect()) as connection:
                connection.execute(
                    "DELETE FROM agent_sessions WHERE session_id=?", (session_id,)
                )
                connection.commit()
            del self._sessions[session_id]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _migrate_legacy_json_once(self) -> None:
        legacy = self.legacy_json_path
        if legacy is None:
            return
        with closing(self._connect()) as connection:
            migrated = connection.execute(
                "SELECT value FROM agent_session_metadata WHERE key='legacy_json_imported'"
            ).fetchone()
        if migrated is not None:
            return

        imported = 0
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, Mapping):
            with self._lock:
                for value in raw.values():
                    session = (
                        AgentSession.from_dict(value)
                        if isinstance(value, Mapping)
                        else None
                    )
                    if session is not None and session.session_id not in self._sessions:
                        self._sessions[session.session_id] = session
                        imported += 1
                if imported:
                    self._persist_unlocked()

        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT OR REPLACE INTO agent_session_metadata(key,value,updated_at)
                   VALUES('legacy_json_imported',?,?)""",
                (str(imported), utc_now()),
            )
            connection.commit()
        if legacy.is_file():
            backup = legacy.with_name(f"{legacy.name}.migrated.bak")
            if not backup.exists():
                try:
                    os.replace(legacy, backup)
                except OSError:
                    pass


def _identifier(value: str, *, prefix: str | None = None) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 160
        or not all(character.isalnum() or character in "._-" for character in value)
    ):
        raise ValueError("AGENT_SESSION_ID_INVALID")
    if prefix and not value.startswith(prefix):
        raise ValueError("AGENT_SESSION_ID_INVALID")
    return value


def _content(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("AGENT_MESSAGE_EMPTY")
    if len(text) > 16_384:
        raise ValueError("AGENT_MESSAGE_TOO_LARGE")
    return text


def _title(value: str) -> str:
    text = " ".join(value.strip().split()) or "New Forge session"
    return text if len(text) <= 90 else f"{text[:87]}..."


def _chat_title(value: str) -> str:
    text = " ".join(value.strip().split()).rstrip(".!?") or "New chat"
    text = text[0].upper() + text[1:]
    if len(text) <= CHAT_TITLE_MAX_LENGTH:
        return text
    return f"{text[: CHAT_TITLE_MAX_LENGTH - 3].rstrip()}..."


def _is_chat_greeting(value: str) -> bool:
    normalized = " ".join(value.casefold().strip().rstrip(".!?,").split())
    return normalized in CHAT_GREETING_TITLES


def _safe_metadata(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 80:
            continue
        if isinstance(item, (str, int, bool)) or item is None:
            safe[key] = item
    return safe
