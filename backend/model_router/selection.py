"""Durable explicit model selection; credentials never enter this database."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

ROLES = frozenset({"main", "explore", "review", "verify", "compact", "title"})


def validate_selection_scope(session_id: str | None, role: str = "main") -> None:
    if role not in ROLES:
        raise ValueError("model_role_invalid")
    if session_id is not None and (
        not isinstance(session_id, str) or not session_id.strip()
        or session_id != session_id.strip() or len(session_id) > 256
        or any(ord(char) < 32 for char in session_id)
    ):
        raise ValueError("model_session_invalid")


@dataclass(frozen=True, slots=True)
class ModelRef:
    provider_id: str
    model_id: str

    def __post_init__(self):
        if not self.provider_id.strip() or not self.model_id.strip():
            raise ValueError("model_reference_empty")

    @property
    def id(self) -> str:
        return f"{self.provider_id}/{self.model_id}"


class ModelSelectionStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS model_selections(
                scope TEXT NOT NULL, role TEXT NOT NULL,
                provider_id TEXT NOT NULL, model_id TEXT NOT NULL,
                PRIMARY KEY(scope,role))""")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def set(self, model: ModelRef, *, session_id: str | None = None, role: str = "main") -> None:
        validate_selection_scope(session_id, role)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO model_selections VALUES(?,?,?,?) ON CONFLICT(scope,role) "
                "DO UPDATE SET provider_id=excluded.provider_id,model_id=excluded.model_id",
                (session_id or "", role, model.provider_id, model.model_id),
            )

    def get(self, *, session_id: str | None = None, role: str = "main") -> ModelRef | None:
        validate_selection_scope(session_id, role)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT provider_id,model_id FROM model_selections WHERE scope=? AND role=?",
                (session_id or "", role),
            ).fetchone()
        return ModelRef(*row) if row else None

    def resolve(self, *, session_id: str | None = None, role: str = "main") -> tuple[ModelRef | None, str]:
        validate_selection_scope(session_id, role)
        if role != "main":
            if session_id:
                selected = self.get(session_id=session_id, role=role)
                if selected:
                    return selected, "session_role"
            selected = self.get(role=role)
            if selected:
                return selected, "role"
        if session_id:
            selected = self.get(session_id=session_id)
            if selected:
                return selected, "session"
        selected = self.get()
        return selected, "application" if selected else "task_route"

    def references(self, provider_id: str) -> bool:
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT 1 FROM model_selections WHERE provider_id=? LIMIT 1", (provider_id,)
            ).fetchone() is not None

    def clear(self, *, session_id: str | None = None, role: str = "main") -> bool:
        validate_selection_scope(session_id, role)
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM model_selections WHERE scope=? AND role=?",
                (session_id or "", role),
            )
            return cursor.rowcount > 0
