"""Immutable backend compatibility identity captured when the process starts."""

from __future__ import annotations

import hashlib
from pathlib import Path


BACKEND_RUNTIME_CONTRACT = "forgex-agent-runtime-v3"


def compute_backend_source_fingerprint(repo_root: str | Path | None = None) -> str:
    root = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[1]
    backend_root = root / "backend"
    digest = hashlib.sha256()
    files = list(path for path in backend_root.rglob("*.py") if "__pycache__" not in path.parts)
    for release_input in ("requirements.txt", "requirements.in", "requirements.lock", "pyproject.toml"):
        candidate = root / release_input
        if candidate.is_file():
            files.append(candidate)
    files = sorted(files, key=lambda path: path.relative_to(root).as_posix())
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


BACKEND_SOURCE_FINGERPRINT = compute_backend_source_fingerprint()
