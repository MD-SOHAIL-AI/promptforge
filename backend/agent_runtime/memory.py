"""Verified project memory with provenance and deterministic invalidation."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .orchestration_store import AgentOrchestrationStore, utc_now


@dataclass(frozen=True, slots=True)
class ProjectMemoryEntry:
    memory_id: str
    project_id: str
    kind: str
    value: str
    source_type: str
    source_id: str
    source_hash: str
    verified: bool
    confidence: float
    workspace_revision: str | None = None
    affected_paths: tuple[str, ...] = field(default_factory=tuple)
    board_id: str | None = None
    status: str = "active"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "project_id": self.project_id,
            "kind": self.kind,
            "value": self.value,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_hash": self.source_hash,
            "verified": self.verified,
            "confidence": self.confidence,
            "workspace_revision": self.workspace_revision,
            "affected_paths": list(self.affected_paths),
            "board_id": self.board_id,
            "status": self.status,
            "created_at": self.created_at,
        }


class ProjectMemoryService:
    def __init__(self, store: AgentOrchestrationStore) -> None:
        self.store = store

    def add_verified(
        self,
        *,
        project_id: str,
        kind: str,
        value: str,
        source_type: str,
        source_id: str,
        source_payload: str,
        workspace_revision: str | None = None,
        affected_paths: Iterable[str] = (),
        board_id: str | None = None,
        confidence: float = 1.0,
    ) -> ProjectMemoryEntry:
        clean_value = " ".join(str(value).split())[:2_000]
        if not clean_value:
            raise ValueError("AGENT_MEMORY_VALUE_REQUIRED")
        source_hash = hashlib.sha256(source_payload.encode("utf-8")).hexdigest()
        entry = ProjectMemoryEntry(
            memory_id=f"memory-{uuid.uuid4().hex}",
            project_id=project_id,
            kind=kind,
            value=clean_value,
            source_type=source_type,
            source_id=source_id,
            source_hash=source_hash,
            verified=True,
            confidence=max(0.0, min(float(confidence), 1.0)),
            workspace_revision=workspace_revision,
            affected_paths=tuple(dict.fromkeys(str(path) for path in affected_paths)),
            board_id=board_id,
        )
        self.store.remember(entry.to_dict())
        return entry

    def active(
        self,
        project_id: str,
        *,
        current_workspace_revision: str | None = None,
        board_id: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        for entry in self.store.memories(project_id):
            stale_revision = bool(
                current_workspace_revision
                and entry.get("workspace_revision")
                and entry.get("workspace_revision") != current_workspace_revision
            )
            stale_board = bool(entry.get("board_id") and board_id and entry.get("board_id") != board_id)
            if stale_revision or stale_board:
                self.store.invalidate_memory(
                    str(entry["memory_id"]),
                    "board_changed" if stale_board else "workspace_revision_changed",
                )
        return self.store.memories(project_id)

    def invalidate_for_change(
        self,
        project_id: str,
        *,
        changed_paths: Iterable[str],
        new_workspace_revision: str | None = None,
        board_id: str | None = None,
    ) -> tuple[str, ...]:
        changed = set(changed_paths)
        invalidated: list[str] = []
        for entry in self.store.memories(project_id):
            paths = set(str(item) for item in entry.get("affected_paths", []) if isinstance(item, str))
            stale_revision = bool(
                entry.get("workspace_revision")
                and new_workspace_revision
                and entry.get("workspace_revision") != new_workspace_revision
                and paths.intersection(changed)
            )
            stale_board = bool(entry.get("board_id") and board_id and entry.get("board_id") != board_id)
            if paths.intersection(changed) or stale_revision or stale_board:
                memory_id = str(entry["memory_id"])
                reason = "board_changed" if stale_board else "source_files_changed"
                self.store.invalidate_memory(memory_id, reason)
                invalidated.append(memory_id)
        return tuple(invalidated)

    def invalidate_source(self, project_id: str, source_id: str, *, reason: str) -> tuple[str, ...]:
        invalidated: list[str] = []
        for entry in self.store.memories(project_id):
            if entry.get("source_id") != source_id:
                continue
            memory_id = str(entry["memory_id"])
            self.store.invalidate_memory(memory_id, reason)
            invalidated.append(memory_id)
        return tuple(invalidated)
