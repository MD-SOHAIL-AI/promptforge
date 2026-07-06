"""Sanitized CLI wrapper for the shared Codex status service."""

from __future__ import annotations

import json

from backend.bridges.codex_status import CodexStatusService


if __name__ == "__main__":
    print(json.dumps(CodexStatusService().status().to_safe_dict(), sort_keys=True))
