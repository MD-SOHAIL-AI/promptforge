"""Guarded fake-provider smoke entry point; executes no external provider."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from backend.bridges.diff_service import BridgeDiffService

from .fake_api_provider import FakeApiProvider
from .tool_policy import fake_smoke_policy
from .tool_runtime import ForgeXToolRuntime


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="forgex-tool-runtime-qa-") as temporary:
        root = Path(temporary)
        active = root / "active"
        active.mkdir()
        (active / "README.txt").write_text("ForgeX fake-provider QA fixture.\n", encoding="utf-8")
        runtime = ForgeXToolRuntime(
            managed_sandbox_root=root / "managed-sandboxes",
            active_workspace_root=active,
            review_service=BridgeDiffService(),
        )
        result = runtime.run(
            task="Create the exact ForgeX tool runtime smoke artifact.",
            provider=FakeApiProvider(),
            policy=fake_smoke_policy(),
        )
        payload = result.to_safe_dict()
        print(payload["classification"])
        print(f"review_created={str(payload['review_created']).lower()}")
        print(f"created_file_count={payload['created_file_count']}")
        print(f"active_workspace_unchanged={str(payload['active_workspace_unchanged']).lower()}")
        print(f"apply_run={str(payload['apply_run']).lower()}")
        print(f"build_run={str(payload['build_run']).lower()}")
        print(f"flash_run={str(payload['flash_run']).lower()}")
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        return 0 if result.review_created else 1


if __name__ == "__main__":
    raise SystemExit(main())
