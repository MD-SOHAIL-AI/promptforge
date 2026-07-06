"""Explicitly confirmed one-request API planner smoke through ProductAgentService."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.review_store import BridgeReviewStore

from .api_planner_provider import API_PLANNER_CONFIGS, ApiPlannerClassification, ApiPlannerProvider, safe_provider_status
from .product_agent_service import ProductAgentService, TERMINAL_AGENT_STATUSES
from .product_provider_registry import ProductProviderRegistry


async def run_smoke(*, provider_id: str, confirmed: bool, env: Mapping[str, str], transport=None) -> dict[str, object]:
    if provider_id not in API_PLANNER_CONFIGS:
        return _summary(provider_id, "API_PROVIDER_DISABLED")
    config = API_PLANNER_CONFIGS[provider_id]
    status = safe_provider_status(config, env, confirmed=confirmed)
    if not confirmed:
        status["status"] = ApiPlannerClassification.CONFIRMATION_REQUIRED.value
        status["routeable"] = False
    if status["status"] != ApiPlannerClassification.READY.value:
        return _summary(
            provider_id,
            str(status["status"]),
            model_configured=bool(status["model_configured"]),
            key_present=bool(status["key_present"]),
        )

    registry = ProductProviderRegistry(env=env, confirm_real_api=True, transport=transport)
    with tempfile.TemporaryDirectory(prefix="forgex-api-product-smoke-") as temporary:
        root = Path(temporary)
        active = root / "active"
        active.mkdir()
        (active / "README.txt").write_text("ForgeX API product smoke fixture.\n", encoding="utf-8")
        reviews = BridgeDiffService(store=BridgeReviewStore(
            snapshots_path=root / "state" / "snapshots.jsonl",
            reviews_path=root / "state" / "reviews.jsonl",
        ))
        service = ProductAgentService(
            managed_sandbox_root=root / "sandboxes",
            review_service=reviews,
            provider_registry=registry,
            enabled=True,
        )
        run = await service.start_run(
            project_id="api-product-smoke",
            active_workspace_root=active,
            instruction="Create the exact API provider smoke artifact using the allowed ToolPlan.",
            provider_id=provider_id,
        )
        for _ in range(1000):
            run = service.get_run(run.run_id)
            if run.status in TERMINAL_AGENT_STATUSES:
                break
            await asyncio.sleep(0.01)
        planner = registry.last_planner
        outbound_count = planner.outbound_request_count if isinstance(planner, ApiPlannerProvider) else 0
        payload = _summary(
            provider_id,
            run.classification or ApiPlannerClassification.UNKNOWN_SAFE_FAILURE.value,
            model_configured=True,
            key_present=True,
            outbound_request_count=outbound_count,
            review_created=bool(run.review_id),
            created=run.created_file_count,
            modified=run.modified_file_count,
            deleted=run.deleted_file_count,
            active_workspace_unchanged=run.active_workspace_unchanged,
        )
        await service.close()
        return payload


def _summary(
    provider_id: str,
    classification: str,
    *,
    model_configured: bool = False,
    key_present: bool = False,
    outbound_request_count: int = 0,
    review_created: bool = False,
    created: int = 0,
    modified: int = 0,
    deleted: int = 0,
    active_workspace_unchanged: bool = True,
) -> dict[str, object]:
    return {
        "provider": provider_id,
        "model_configured": model_configured,
        "key_present": key_present,
        "outbound_request_attempted": outbound_request_count == 1,
        "outbound_request_count": outbound_request_count,
        "classification": classification,
        "created_file_count": created,
        "modified_file_count": modified,
        "deleted_file_count": deleted,
        "review_created": review_created,
        "active_workspace_unchanged": active_workspace_unchanged,
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "api_key_printed": False,
        "api_key_persisted": False,
        "apply_run": False,
        "build_run": False,
        "flash_run": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--confirm-real-api", action="store_true")
    args = parser.parse_args(argv)
    payload = asyncio.run(run_smoke(provider_id=args.provider, confirmed=args.confirm_real_api, env=os.environ))
    print(payload["classification"])
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0 if payload["classification"] in {
        ApiPlannerClassification.SANDBOX_WRITE_PASS.value,
        ApiPlannerClassification.KEY_MISSING.value,
        ApiPlannerClassification.MODEL_MISSING.value,
        ApiPlannerClassification.DISABLED.value,
        ApiPlannerClassification.CONFIRMATION_REQUIRED.value,
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
