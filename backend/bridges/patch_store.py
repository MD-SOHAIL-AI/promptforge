"""Index storage for exported bridge review patches."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

PatchIntegrityStatus = Literal["valid", "missing", "modified", "unknown"]


class BridgePatchStoreError(ValueError):
    code = "BRIDGE_PATCH_STORE_ERROR"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class BridgePatchRecord:
    patch_id: str
    review_id: str
    provider_id: str
    created_at: datetime
    patch_path: str
    metadata_path: str
    patch_size: int
    patch_sha256: str
    integrity_status: PatchIntegrityStatus
    changed_file_count: int
    created_files: tuple[str, ...]
    modified_files: tuple[str, ...]
    deleted_files: tuple[str, ...]
    workspace_root_hash: str
    review_status_at_export: str
    apply_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "review_id": self.review_id,
            "provider_id": self.provider_id,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "patch_path": self.patch_path,
            "metadata_path": self.metadata_path,
            "patch_size": self.patch_size,
            "patch_sha256": self.patch_sha256,
            "integrity_status": self.integrity_status,
            "changed_file_count": self.changed_file_count,
            "created_files": list(self.created_files),
            "modified_files": list(self.modified_files),
            "deleted_files": list(self.deleted_files),
            "workspace_root_hash": self.workspace_root_hash,
            "review_status_at_export": self.review_status_at_export,
            "apply_enabled": self.apply_enabled,
        }


class BridgePatchStore:
    """Append-friendly local patch index.

    The index stores metadata only. Patch text stays in individual `.patch` files.
    Paths stored in records are relative to the managed patch directory.
    """

    def __init__(self, patch_directory: str | Path, index_path: str | Path | None = None) -> None:
        self.patch_directory = Path(patch_directory)
        self.index_path = Path(index_path) if index_path else self.patch_directory / "index.jsonl"

    def upsert(self, record: BridgePatchRecord) -> None:
        records = {item.patch_id: item for item in self.list_records()}
        records[record.patch_id] = record
        self._write_records(records.values())

    def get(self, patch_id: str) -> BridgePatchRecord:
        for record in self.list_records():
            if record.patch_id == patch_id:
                return record
        raise BridgePatchStoreError("Patch record was not found.", {"patch_id": patch_id})

    def list_records(
        self,
        *,
        provider_id: str | None = None,
        review_id: str | None = None,
        integrity_status: str | None = None,
    ) -> list[BridgePatchRecord]:
        records = [record for record in self._read_records()]
        if provider_id:
            records = [record for record in records if record.provider_id == provider_id]
        if review_id:
            records = [record for record in records if record.review_id == review_id]
        if integrity_status:
            records = [record for record in records if record.integrity_status == integrity_status]
        return sorted(records, key=lambda record: record.created_at, reverse=True)

    def remove(self, patch_id: str) -> BridgePatchRecord:
        records = self.list_records()
        removed: BridgePatchRecord | None = None
        remaining: list[BridgePatchRecord] = []
        for record in records:
            if record.patch_id == patch_id:
                removed = record
            else:
                remaining.append(record)
        if removed is None:
            raise BridgePatchStoreError("Patch record was not found.", {"patch_id": patch_id})
        self._write_records(remaining)
        return removed

    def safe_child(self, relative_path: str) -> Path:
        if "/" in relative_path or "\\" in relative_path or relative_path in {"", ".", ".."}:
            raise BridgePatchStoreError("Patch path is not a managed patch file.", {"path": relative_path})
        path = (self.patch_directory / relative_path).resolve()
        try:
            path.relative_to(self.patch_directory.resolve())
        except ValueError as exc:
            raise BridgePatchStoreError("Patch path escaped patch directory.", {"path": relative_path}) from exc
        return path

    def _read_records(self) -> list[BridgePatchRecord]:
        if not self.index_path.exists():
            return []
        records: dict[str, BridgePatchRecord] = {}
        for line in self.index_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                record = record_from_dict(payload)
                records[record.patch_id] = record
        return list(records.values())

    def _write_records(self, records: Any) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            json.dumps(record.to_dict(), ensure_ascii=True, sort_keys=True)
            for record in sorted(records, key=lambda item: item.created_at)
        ]
        self.index_path.write_text(("\n".join(rows) + "\n") if rows else "", encoding="utf-8")


def record_from_dict(data: dict[str, Any]) -> BridgePatchRecord:
    return BridgePatchRecord(
        patch_id=str(data["patch_id"]),
        review_id=str(data["review_id"]),
        provider_id=str(data["provider_id"]),
        created_at=datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00")).astimezone(timezone.utc),
        patch_path=str(data["patch_path"]),
        metadata_path=str(data["metadata_path"]),
        patch_size=int(data.get("patch_size", 0)),
        patch_sha256=str(data.get("patch_sha256", "")),
        integrity_status=data.get("integrity_status", "unknown"),
        changed_file_count=int(data.get("changed_file_count", 0)),
        created_files=tuple(str(item) for item in data.get("created_files", [])),
        modified_files=tuple(str(item) for item in data.get("modified_files", [])),
        deleted_files=tuple(str(item) for item in data.get("deleted_files", [])),
        workspace_root_hash=str(data.get("workspace_root_hash", "")),
        review_status_at_export=str(data.get("review_status_at_export", "unknown")),
        apply_enabled=bool(data.get("apply_enabled", False)),
    )
