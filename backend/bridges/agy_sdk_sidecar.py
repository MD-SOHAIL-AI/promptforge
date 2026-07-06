"""Minimal JSONL sidecar for the optional Google Antigravity SDK."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


async def _run(payload: dict[str, object]) -> dict[str, object]:
    instruction = payload.get("instruction")
    workspace = payload.get("workspace")
    if not isinstance(instruction, str) or not instruction.strip():
        return {"status": "failed", "code": "invalid_instruction"}
    if not isinstance(workspace, str):
        return {"status": "failed", "code": "invalid_workspace"}
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir() or Path.cwd().resolve() != root:
        return {"status": "failed", "code": "workspace_mismatch"}
    try:
        from google.antigravity import Agent, LocalAgentConfig
        from google.antigravity.hooks.policy import allow, deny
    except (ImportError, ModuleNotFoundError):
        return {"status": "failed", "code": "sdk_unavailable"}

    policies = [
        deny("*"),
        allow("view_file"),
        allow("list_dir"),
        allow("find_by_name"),
        allow("grep_search"),
        allow("write_to_file"),
        allow("replace_file_content"),
        allow("multi_replace_file_content"),
    ]
    try:
        config = LocalAgentConfig(policies=policies)
        async with Agent(config) as agent:
            response = await agent.chat(instruction)
            await response.text()
    except Exception:
        return {"status": "failed", "code": "sdk_run_failed"}
    return {"status": "completed", "code": "ok"}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.readline())
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, json.JSONDecodeError):
        print(json.dumps({"status": "failed", "code": "invalid_request"}))
        return 2
    result = asyncio.run(_run(payload))
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
