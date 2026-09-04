"""Persistent multi-turn Forge agent sessions."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


AGENT_SESSION_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "blocked", "timed_out"})
AGENT_MESSAGE_ROLES = frozenset({"user", "assistant", "tool", "system"})


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
        if not isinstance(message_id, str) or not isinstance(role, str) or not isinstance(content, str):
            return None
        if role not in AGENT_MESSAGE_ROLES:
            return None
        created_at = value.get("created_at")
        run_id = value.get("run_id")
        metadata = value.get("metadata")
        return cls(
            message_id=message_id,
            role=role,
            content=content,
            created_at=created_at if isinstance(created_at, str) else utc_now(),
            run_id=run_id if isinstance(run_id, str) else None,
            metadata=_safe_metadata(metadata if isinstance(metadata, Mapping) else {}),
        )


@dataclass(frozen=True, slots=True)
class AgentSession:
    session_id: str
    project_id: str
    title: str
    status: str = "idle"
    active_run_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    messages: tuple[AgentMessage, ...] = field(default_factory=tuple)

    def to_dict(self, *, include_messages: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "title": self.title,
            "status": self.status,
            "active_run_id": self.active_run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
        }
        if include_messages:
            value["messages"] = [message.to_dict() for message in self.messages]
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AgentSession | None":
        session_id = value.get("session_id")
        project_id = value.get("project_id")
        title = value.get("title")
        if not isinstance(session_id, str) or not isinstance(project_id, str) or not isinstance(title, str):
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
            messages=tuple(messages[-200:]),
        )


class AgentSessionStore:
    """Small JSON-backed session store for local desktop agent conversations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._sessions = self._load()

    def create(self, *, project_id: str, title: str | None = None) -> AgentSession:
        session = AgentSession(
            session_id=f"agent-session-{uuid.uuid4().hex}",
            project_id=_identifier(project_id),
            title=_title(title or "New Forge session"),
        )
        with self._lock:
            self._sessions[session.session_id] = session
            self._persist_unlocked()
        return session

    def list(self, *, project_id: str | None = None) -> tuple[AgentSession, ...]:
        with self._lock:
            values = tuple(self._sessions.values())
        if project_id:
            values = tuple(session for session in values if session.project_id == project_id)
        return tuple(sorted(values, key=lambda session: session.updated_at, reverse=True))

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
            if role == "user" and (not session.messages or title == "New Forge session"):
                title = _title(text)
            updated = replace(
                session,
                title=title,
                updated_at=utc_now(),
                messages=(*session.messages, message)[-200:],
            )
            self._sessions[session_id] = updated
            self._persist_unlocked()
            return updated

    def update_run(
        self,
        session_id: str,
        *,
        run_id: str | None,
        status: str,
    ) -> AgentSession:
        with self._lock:
            session = self.get(session_id)
            updated = replace(
                session,
                status=status,
                active_run_id=run_id,
                updated_at=utc_now(),
            )
            self._sessions[session_id] = updated
            self._persist_unlocked()
            return updated

    def _load(self) -> dict[str, AgentSession]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        sessions: dict[str, AgentSession] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, Mapping):
                continue
            session = AgentSession.from_dict(value)
            if session is not None:
                sessions[session.session_id] = session
        return sessions

    def _persist_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                payload = {
                    session_id: session.to_dict(include_messages=True)
                    for session_id, session in sorted(self._sessions.items())
                }
                handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


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


def _safe_metadata(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 80:
            continue
        if isinstance(item, (str, int, bool)) or item is None:
            safe[key] = item
    return safe
