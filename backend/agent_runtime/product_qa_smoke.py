"""No-network product-agent smoke using the persistent review path."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.review_store import BridgeReviewStore

from .product_agent_service import ProductAgentService, TERMINAL_AGENT_STATUSES
from .product_provider_registry import ProductProviderRegistry


async def run_smoke() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="forgex-product-agent-smoke-") as temporary:
        root = Path(temporary)
        active = root / "active"
        active.mkdir()
        (active / "README.txt").write_text("ForgeX product runtime fixture.\n", encoding="utf-8")
        store = BridgeReviewStore(
            snapshots_path=root / "state" / "snapshots.jsonl",
            reviews_path=root / "state" / "reviews.jsonl",
        )
        reviews = BridgeDiffService(store=store)
        service = ProductAgentService(
            managed_sandbox_root=root / "sandboxes",
            review_service=reviews,
            provider_registry=ProductProviderRegistry(fake_enabled=True),
            enabled=True,
        )
        run = await service.start_run(
            project_id="fake-product-project",
            active_workspace_root=active,
            instruction="Create the deterministic fake product artifact.",
        )
        for _ in range(500):
            run = service.get_run(run.run_id)
            if run.status in TERMINAL_AGENT_STATUSES:
                break
            await asyncio.sleep(0.01)
        payload = run.to_safe_dict()
        payload["persistent_review"] = bool(run.review_id and reviews.get_review(run.review_id))
        await service.close()
        return payload


def main() -> int:
    payload = asyncio.run(run_smoke())
    print(payload.get("classification"))
    print(f"review_created={str(bool(payload.get('review_id'))).lower()}")
    print(f"created_file_count={payload.get('created_file_count', 0)}")
    print(f"modified_file_count={payload.get('modified_file_count', 0)}")
    print(f"deleted_file_count={payload.get('deleted_file_count', 0)}")
    print(f"active_workspace_unchanged={str(bool(payload.get('active_workspace_unchanged'))).lower()}")
    print("apply_run=false")
    print("build_run=false")
    print("flash_run=false")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0 if payload.get("classification") == "TOOL_RUNTIME_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
