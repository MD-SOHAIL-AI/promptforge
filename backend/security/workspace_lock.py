"""Cooperative, cross-process workspace mutation lock for ForgeX.

All ForgeX writers that operate on a workspace should use this lock around
compare/read -> write -> commit, not merely around the final os.replace().
Locks are advisory with respect to unrelated external text editors: their
writes are still protected by file revision checks, not by this protocol.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Iterator

_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_LOCAL = threading.local()


class WorkspaceLockTimeout(TimeoutError):
    """Another process held a workspace lock beyond the allowed wait."""


def _lock_directory() -> Path:
    # Keep lockfiles outside source trees so they never affect project hashes,
    # builds or ChangeSet baselines. Use the same per-user directory on Windows.
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
        directory = base / 'ForgeX' / 'workspace-locks'
    else:
        base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state')
        directory = base / 'forgex' / 'workspace-locks'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise OSError('WORKSPACE_LOCK_DIRECTORY_SYMLINK')
    return directory


def _lock_id(workspace: str | Path) -> str:
    canonical = str(Path(workspace).expanduser().resolve(strict=False))
    if os.name == 'nt':
        canonical = os.path.normcase(canonical)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _acquire_os_lock(fd: int, deadline: float) -> None:
    while True:
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except (BlockingIOError, OSError) as exc:
            # Windows reports lock contention as EACCES/EDEADLOCK/EAGAIN.
            import errno
            if getattr(exc, 'errno', None) not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK, 13, 36):
                raise
            if time.monotonic() >= deadline:
                raise WorkspaceLockTimeout('WORKSPACE_LOCK_TIMEOUT') from exc
            time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))


def _release_os_lock(fd: int) -> None:
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def workspace_lock(workspace: str | Path, *, timeout: float = 30) -> Iterator[None]:
    """Serialize ForgeX writers across threads, services and OS processes.

    A stable lock identity survives directory replacement during managed saves.
    Unlike removing a lockfile, keeping the file avoids split-brain waiters.
    """
    if timeout < 0:
        raise ValueError('timeout must be nonnegative')
    identity = _lock_id(workspace)
    with _GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(identity, threading.RLock())
    deadline = time.monotonic() + timeout
    if not thread_lock.acquire(timeout=max(0, deadline - time.monotonic())):
        raise WorkspaceLockTimeout('WORKSPACE_LOCK_TIMEOUT')
    # Reentrant calls on this thread reuse the existing OS lock and fd.
    active = getattr(_LOCAL, 'active', None)
    if active is None:
        active = _LOCAL.active = set()
    if identity in active:
        try:
            yield
        finally:
            thread_lock.release()
        return
    fd = None
    try:
        target = _lock_directory() / (identity + '.lock')
        if target.is_symlink():
            raise OSError('WORKSPACE_LOCK_SYMLINK')
        fd = os.open(str(target), os.O_CREAT | os.O_RDWR, 0o600)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b'0')
        _acquire_os_lock(fd, deadline)
        active.add(identity)
        try:
            yield
        finally:
            active.remove(identity)
            _release_os_lock(fd)
    finally:
        if fd is not None:
            os.close(fd)
        thread_lock.release()
