"""Local audit logging for bridge review actions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .review_models import BridgeAuditEntry


class BridgeAuditLog:
    """Append-only local bridge audit log.

    The log intentionally stores workspace hashes, not raw workspace paths.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(self, entry: BridgeAuditEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = entry.to_dict()
        self.path.open("a", encoding="utf-8").write(
            json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n"
        )

    def list_entries(self, limit: int = 100) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows[-max(1, limit) :]


def hash_workspace_root(workspace_root: str) -> str:
    return hashlib.sha256(workspace_root.encode("utf-8")).hexdigest()
