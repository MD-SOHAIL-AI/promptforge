"""Process-safe local operation locks for unified coding workflows."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


OPERATION_IN_PROGRESS = "CODING_WORKFLOW_OPERATION_IN_PROGRESS"
LOCK_INVALID = "CODING_WORKFLOW_LOCK_INVALID"
LOCK_NOT_FOUND = "CODING_WORKFLOW_LOCK_NOT_FOUND"
LOCK_NOT_STALE = "CODING_WORKFLOW_LOCK_NOT_STALE"
LOCK_PERSISTENCE_FAILED = "CODING_WORKFLOW_LOCK_PERSISTENCE_FAILED"

DEFAULT_LOCK_TTL_SECONDS = 30 * 60
MAX_LOCK_TTL_SECONDS = 24 * 60 * 60
LOCKS_DIRECTORY_NAME = "coding-workflow-locks"

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_OPERATION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class CodingWorkflowLockError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CodingWorkflowRunOperationLock:
    run_id: str
    operation: str
    token: str
    path: Path


class CodingWorkflowLockManager:
    def __init__(self, lock_dir: str | Path) -> None:
        self.lock_dir = Path(lock_dir)

    @classmethod
    def from_state_directory(cls, state_directory: str | Path) -> "CodingWorkflowLockManager":
        return cls(Path(state_directory) / LOCKS_DIRECTORY_NAME)

    def acquire_run_operation_lock(
        self,
        run_id: str,
        operation: str,
        *,
        ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    ) -> CodingWorkflowRunOperationLock:
        _identifier(run_id, "run_id")
        _operation(operation)
        ttl = _ttl(ttl_seconds)
        now = _utc_now()
        expires = now + timedelta(seconds=ttl)
        token = uuid.uuid4().hex
        path = self._lock_path(run_id)
        payload = {
            "schema_version": "forgex.coding_workflow_lock.v1",
            "run_id": run_id,
            "operation": operation,
            "token": token,
            "pid": os.getpid(),
            "created_at": _format_time(now),
            "expires_at": _format_time(expires),
        }
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise CodingWorkflowLockError(OPERATION_IN_PROGRESS, "Coding workflow operation is already in progress.") from exc
        except OSError as exc:
            raise CodingWorkflowLockError(LOCK_PERSISTENCE_FAILED, "Coding workflow lock could not be created.") from exc
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        except OSError as exc:
            try:
                os.close(descriptor)
            finally:
                self.release_run_operation_lock(CodingWorkflowRunOperationLock(run_id, operation, token, path))
            raise CodingWorkflowLockError(LOCK_PERSISTENCE_FAILED, "Coding workflow lock could not be written.") from exc
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
        return CodingWorkflowRunOperationLock(run_id=run_id, operation=operation, token=token, path=path)

    def release_run_operation_lock(self, lock: CodingWorkflowRunOperationLock) -> None:
        if not isinstance(lock, CodingWorkflowRunOperationLock):
            return
        try:
            metadata = self.get_run_lock_status(lock.run_id)
        except CodingWorkflowLockError:
            return
        if not metadata.get("locked") or metadata.get("token") != lock.token:
            return
        try:
            lock.path.unlink(missing_ok=True)
        except OSError:
            return

    def get_run_lock_status(self, run_id: str, *, now: datetime | None = None) -> dict[str, object]:
        _identifier(run_id, "run_id")
        path = self._lock_path(run_id)
        if not path.exists():
            return {"run_id": run_id, "locked": False, "stale": False}
        metadata = self._read_lock(path)
        checked_at = _coerce_time(now)
        expires_at = _parse_time(str(metadata["expires_at"]))
        return {
            "run_id": run_id,
            "locked": True,
            "stale": expires_at <= checked_at,
            "operation": metadata["operation"],
            "pid": metadata.get("pid"),
            "created_at": metadata["created_at"],
            "expires_at": metadata["expires_at"],
            "token": metadata["token"],
        }

    def detect_stale_locks(self, *, now: datetime | None = None, limit: int = 100) -> tuple[dict[str, object], ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 1000:
            raise CodingWorkflowLockError(LOCK_INVALID, "Lock stale detection limit is invalid.")
        checked_at = _coerce_time(now)
        stale: list[dict[str, object]] = []
        if not self.lock_dir.exists():
            return ()
        try:
            paths = sorted(self.lock_dir.glob("*.lock"), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise CodingWorkflowLockError(LOCK_PERSISTENCE_FAILED, "Coding workflow locks could not be listed.") from exc
        for path in paths:
            try:
                metadata = self._read_lock(path)
                expires_at = _parse_time(str(metadata["expires_at"]))
            except CodingWorkflowLockError:
                continue
            if expires_at > checked_at:
                continue
            stale.append({
                "run_id": metadata["run_id"],
                "operation": metadata["operation"],
                "created_at": metadata["created_at"],
                "expires_at": metadata["expires_at"],
                "pid": metadata.get("pid"),
                "suggested_action": "manual_clear_stale_lock",
            })
            if len(stale) >= limit:
                break
        return tuple(stale)

    def clear_stale_run_lock(self, run_id: str, *, now: datetime | None = None) -> dict[str, object]:
        status = self.get_run_lock_status(run_id, now=now)
        if not status.get("locked"):
            raise CodingWorkflowLockError(LOCK_NOT_FOUND, "Coding workflow lock was not found.")
        if not status.get("stale"):
            raise CodingWorkflowLockError(LOCK_NOT_STALE, "Coding workflow lock is not stale.")
        try:
            self._lock_path(run_id).unlink(missing_ok=True)
        except OSError as exc:
            raise CodingWorkflowLockError(LOCK_PERSISTENCE_FAILED, "Coding workflow stale lock could not be cleared.") from exc
        safe_status = {key: value for key, value in status.items() if key != "token"}
        safe_status["cleared"] = True
        return safe_status

    def _lock_path(self, run_id: str) -> Path:
        _identifier(run_id, "run_id")
        return self.lock_dir / f"{run_id}.lock"

    def _read_lock(self, path: Path) -> dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8")
            value = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CodingWorkflowLockError(LOCK_INVALID, "Coding workflow lock metadata is invalid.") from exc
        if not isinstance(value, dict):
            raise CodingWorkflowLockError(LOCK_INVALID, "Coding workflow lock metadata is invalid.")
        try:
            _identifier(value["run_id"], "run_id")
            _operation(value["operation"])
            _required_text(value["token"], "token", 64)
            _parse_time(str(value["created_at"]))
            _parse_time(str(value["expires_at"]))
            pid = value.get("pid")
            if pid is not None and (not isinstance(pid, int) or isinstance(pid, bool) or pid < 0):
                raise ValueError("pid invalid")
        except (KeyError, ValueError) as exc:
            raise CodingWorkflowLockError(LOCK_INVALID, "Coding workflow lock metadata is invalid.") from exc
        return value


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise CodingWorkflowLockError(LOCK_INVALID, f"{field_name} is invalid.")
    return value


def _operation(value: object) -> str:
    if not isinstance(value, str) or not _OPERATION_RE.fullmatch(value):
        raise CodingWorkflowLockError(LOCK_INVALID, "operation is invalid.")
    return value


def _required_text(value: object, field_name: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > max_chars:
        raise CodingWorkflowLockError(LOCK_INVALID, f"{field_name} is invalid.")
    return value


def _ttl(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_LOCK_TTL_SECONDS:
        raise CodingWorkflowLockError(LOCK_INVALID, "Lock TTL is invalid.")
    return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_time(value: datetime | None) -> datetime:
    if value is None:
        return _utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return _coerce_time(value).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _coerce_time(parsed)


__all__ = [
    "CodingWorkflowLockError",
    "CodingWorkflowLockManager",
    "CodingWorkflowRunOperationLock",
    "LOCK_INVALID",
    "LOCK_NOT_FOUND",
    "LOCK_NOT_STALE",
    "LOCK_PERSISTENCE_FAILED",
    "OPERATION_IN_PROGRESS",
]
