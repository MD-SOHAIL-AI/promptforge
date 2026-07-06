"""QA-only reusable AGY workspace with strict containment and locking."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .diff_service import IGNORED_DIRS, is_safe_relative_path
from .sandbox_service import BridgeSandboxError


TRUSTED_WORKSPACE_FLAG = "FORGEX_ENABLE_AGY_TRUSTED_WORKSPACE"
TRUSTED_WORKSPACE_MARKER = ".forgex-agy-trusted-workspace.json"
TRUSTED_WORKSPACE_MARKER_VALUE = {"owner": "ForgeX", "purpose": "agy-qa-trusted-workspace", "version": 1}
LOCK_STALE_SECONDS = 60 * 60


@dataclass(frozen=True, slots=True)
class AGYTrustedWorkspaceLease:
    workspace_root: Path
    lock_path: Path
    token: str


class AGYTrustedWorkspaceService:
    """Own a stable AGY cwd without granting it access to the active workspace."""

    def __init__(self, managed_root: str | Path, *, env: dict[str, str] | None = None) -> None:
        self.managed_root = Path(managed_root).expanduser().resolve()
        self.env = env if env is not None else os.environ

    def is_enabled(self) -> bool:
        return self.env.get("FORGEX_QA_MODE") == "1" and self.env.get(TRUSTED_WORKSPACE_FLAG) == "1"

    def workspace_for(self, active_workspace: str | Path) -> Path:
        active = Path(active_workspace).expanduser().resolve()
        digest = hashlib.sha256(str(active).casefold().encode("utf-8")).hexdigest()[:24]
        return self.managed_root / digest

    def prepare(self, active_workspace: str | Path) -> Path:
        active = self._validate_active(active_workspace)
        workspace = self.workspace_for(active)
        self.managed_root.mkdir(parents=True, exist_ok=True)
        if workspace.exists():
            self._validate_workspace(workspace, active, require_marker=True)
        else:
            workspace.mkdir(parents=False, exist_ok=False)
        self._reset_contents(workspace, active)
        self._write_marker(workspace)
        self._validate_workspace(workspace, active, require_marker=True)
        return workspace

    def verify(self, active_workspace: str | Path) -> Path:
        active = self._validate_active(active_workspace)
        workspace = self.workspace_for(active)
        self._validate_workspace(workspace, active, require_marker=True)
        return workspace

    def acquire_and_reset(self, active_workspace: str | Path) -> AGYTrustedWorkspaceLease:
        if not self.is_enabled():
            raise BridgeSandboxError("AGY trusted workspace mode is disabled.")
        active = self._validate_active(active_workspace)
        workspace = self.verify(active)
        lock_path = self.managed_root / f"{workspace.name}.lock"
        token = self._acquire_lock(lock_path)
        try:
            self._validate_workspace(workspace, active, require_marker=True)
            self._reset_contents(workspace, active)
            self._write_marker(workspace)
            self._validate_workspace(workspace, active, require_marker=True)
        except Exception:
            self.release(AGYTrustedWorkspaceLease(workspace, lock_path, token))
            raise
        return AGYTrustedWorkspaceLease(workspace, lock_path, token)

    def release(self, lease: AGYTrustedWorkspaceLease) -> None:
        expected = self.managed_root / f"{lease.workspace_root.name}.lock"
        if lease.lock_path != expected or not self._is_direct_child(lease.workspace_root):
            raise BridgeSandboxError("Trusted workspace lock ownership is invalid.")
        try:
            value = json.loads(lease.lock_path.read_text(encoding="utf-8"))
            if value.get("token") != lease.token:
                raise BridgeSandboxError("Trusted workspace lock ownership is invalid.")
            lease.lock_path.unlink(missing_ok=True)
        except (OSError, ValueError, AttributeError) as exc:
            raise BridgeSandboxError("Trusted workspace lock could not be released safely.") from exc

    def _validate_active(self, active_workspace: str | Path) -> Path:
        active = Path(active_workspace).expanduser().resolve()
        if not active.exists() or not active.is_dir() or active.is_symlink():
            raise BridgeSandboxError("Active workspace must be an existing real directory.")
        return active

    def _validate_workspace(self, workspace: Path, active: Path, *, require_marker: bool) -> None:
        requested = workspace.absolute()
        if not requested.exists() or not requested.is_dir() or requested.is_symlink():
            raise BridgeSandboxError("Trusted workspace is missing or invalid.")
        resolved = requested.resolve()
        if resolved != requested or not self._is_direct_child(resolved):
            raise BridgeSandboxError("Trusted workspace escaped its managed root.")
        forbidden = self._forbidden_roots(active)
        if resolved in forbidden or resolved == active or self._paths_overlap(resolved, active):
            raise BridgeSandboxError("Trusted workspace conflicts with a protected root.")
        self._reject_symlinks(resolved)
        if require_marker:
            marker = resolved / TRUSTED_WORKSPACE_MARKER
            if not marker.is_file() or marker.is_symlink():
                raise BridgeSandboxError("Trusted workspace marker is missing or invalid.")
            try:
                value = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise BridgeSandboxError("Trusted workspace marker is invalid.") from exc
            if value != TRUSTED_WORKSPACE_MARKER_VALUE:
                raise BridgeSandboxError("Trusted workspace marker is invalid.")

    def _forbidden_roots(self, active: Path) -> set[Path]:
        home = Path.home().resolve()
        values = {active, home, (home / "Desktop").resolve(), Path(active.anchor).resolve()}
        for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            value = self.env.get(name)
            if value:
                values.add(Path(value).expanduser().resolve())
        # A repository root may be the active workspace's nearest Git owner.
        for parent in (active, *active.parents):
            if (parent / ".git").exists():
                values.add(parent.resolve())
                break
        return values

    def _reset_contents(self, workspace: Path, source: Path) -> None:
        self._validate_workspace(workspace, source, require_marker=workspace.joinpath(TRUSTED_WORKSPACE_MARKER).exists())
        for child in workspace.iterdir():
            if child.is_symlink():
                raise BridgeSandboxError("Trusted workspace reset refused a symbolic link.")
            resolved = child.resolve()
            if resolved.parent != workspace.resolve():
                raise BridgeSandboxError("Trusted workspace reset target escaped containment.")
            if child.is_dir():
                shutil.rmtree(child)
            elif child.is_file():
                child.unlink()
            else:
                raise BridgeSandboxError("Trusted workspace reset refused an unsupported entry.")
        self._copy_workspace(source, workspace)

    def _copy_workspace(self, source: Path, destination: Path) -> None:
        for current_root, dirnames, filenames in os.walk(source, followlinks=False):
            current = Path(current_root)
            dirnames[:] = [
                name for name in dirnames
                if name.casefold() not in IGNORED_DIRS
                and name.casefold() != ".promptforge"
                and not (current / name).is_symlink()
            ]
            for filename in filenames:
                source_path = current / filename
                if source_path.is_symlink():
                    continue
                resolved = source_path.resolve()
                try:
                    relative = resolved.relative_to(source).as_posix()
                except ValueError as exc:
                    raise BridgeSandboxError("Trusted workspace copy escaped the active workspace.") from exc
                if not is_safe_relative_path(relative):
                    raise BridgeSandboxError("Trusted workspace copy rejected an unsafe path.")
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(resolved, target)

    def _write_marker(self, workspace: Path) -> None:
        marker = workspace / TRUSTED_WORKSPACE_MARKER
        marker.write_text(json.dumps(TRUSTED_WORKSPACE_MARKER_VALUE, separators=(",", ":")) + "\n", encoding="utf-8")

    def _acquire_lock(self, lock_path: Path) -> str:
        if lock_path.parent.resolve() != self.managed_root or lock_path.is_symlink():
            raise BridgeSandboxError("Trusted workspace lock escaped containment.")
        token = uuid.uuid4().hex
        payload = json.dumps({"pid": os.getpid(), "created": int(time.time()), "token": token}, separators=(",", ":"))
        try:
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if not self._recover_stale_lock(lock_path):
                raise BridgeSandboxError("Trusted workspace is already in use.")
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return token

    def _recover_stale_lock(self, lock_path: Path) -> bool:
        try:
            value = json.loads(lock_path.read_text(encoding="utf-8"))
            pid = int(value["pid"])
            created = int(value["created"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        if created > int(time.time()) or time.time() - created < LOCK_STALE_SECONDS or self._pid_alive(pid):
            return False
        lock_path.unlink()
        return True

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _is_direct_child(self, workspace: Path) -> bool:
        try:
            relative = workspace.resolve().relative_to(self.managed_root)
        except ValueError:
            return False
        return len(relative.parts) == 1 and workspace.resolve() != self.managed_root

    @staticmethod
    def _paths_overlap(first: Path, second: Path) -> bool:
        try:
            first.relative_to(second)
            return True
        except ValueError:
            pass
        try:
            second.relative_to(first)
            return True
        except ValueError:
            return False

    @staticmethod
    def _reject_symlinks(root: Path) -> None:
        for current_root, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(current_root)
            for name in (*dirnames, *filenames):
                if (current / name).is_symlink():
                    raise BridgeSandboxError("Trusted workspace contains a symbolic link.")
