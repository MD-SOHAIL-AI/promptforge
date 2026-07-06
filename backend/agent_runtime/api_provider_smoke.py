"""Explicitly gated real API provider smoke orchestration."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from backend.bridges.diff_service import BridgeDiffService

from .openai_api_provider import API_KEY_ENV, PROVIDER_FLAG, SPIKE_FLAG, OpenAIApiProvider
from .provider_contracts import ApiProviderClassification
from .tool_contracts import RuntimeClassification
from .tool_policy import api_provider_smoke_policy
from .tool_runtime import ForgeXToolRuntime


@dataclass(frozen=True, slots=True)
class ApiProviderSmokeSummary:
    classification: ApiProviderClassification
    provider_id: str = "openai_api"
    model_id: str = "gpt-4.1-mini"
    execution_attempted: bool = False
    provider_flags_enabled: bool = False
    confirm_flag_present: bool = False
    api_key_detected: bool = False
    api_key_printed: bool = False
    api_key_persisted: bool = False
    outbound_request_attempted: bool = False
    outbound_request_count: int = 0
    tool_plan_valid: bool = False
    tool_execution_count: int = 0
    created_file_count: int = 0
    modified_file_count: int = 0
    deleted_file_count: int = 0
    expected_file_created: bool = False
    content_exact: bool = False
    review_created: bool = False
    active_workspace_unchanged: bool = True
    marker_unchanged: bool = True
    raw_prompt_persisted: bool = False
    raw_response_persisted: bool = False
    apply_run: bool = False
    build_run: bool = False
    flash_run: bool = False
    patch_export_run: bool = False
    patch_verify_run: bool = False
    patch_preflight_run: bool = False

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification.value,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "execution_attempted": self.execution_attempted,
            "provider_flags_enabled": self.provider_flags_enabled,
            "confirm_flag_present": self.confirm_flag_present,
            "api_key_detected": self.api_key_detected,
            "api_key_printed": self.api_key_printed,
            "api_key_persisted": self.api_key_persisted,
            "outbound_request_attempted": self.outbound_request_attempted,
            "outbound_request_count": self.outbound_request_count,
            "tool_plan_valid": self.tool_plan_valid,
            "tool_execution_count": self.tool_execution_count,
            "created_file_count": self.created_file_count,
            "modified_file_count": self.modified_file_count,
            "deleted_file_count": self.deleted_file_count,
            "expected_file_created": self.expected_file_created,
            "content_exact": self.content_exact,
            "review_created": self.review_created,
            "active_workspace_unchanged": self.active_workspace_unchanged,
            "marker_unchanged": self.marker_unchanged,
            "raw_prompt_persisted": self.raw_prompt_persisted,
            "raw_response_persisted": self.raw_response_persisted,
            "apply_run": self.apply_run,
            "build_run": self.build_run,
            "flash_run": self.flash_run,
            "patch_export_run": self.patch_export_run,
            "patch_verify_run": self.patch_verify_run,
            "patch_preflight_run": self.patch_preflight_run,
        }


def run_smoke(
    *,
    provider_name: str,
    confirmed: bool,
    env: Mapping[str, str],
    transport=None,
) -> ApiProviderSmokeSummary:
    flags_enabled = env.get(SPIKE_FLAG, "").strip() == "1" and env.get(PROVIDER_FLAG, "").strip() == "1"
    if provider_name != "openai" or not confirmed:
        return ApiProviderSmokeSummary(
            ApiProviderClassification.DISABLED,
            provider_flags_enabled=flags_enabled,
            confirm_flag_present=confirmed,
        )
    if not flags_enabled:
        return ApiProviderSmokeSummary(ApiProviderClassification.DISABLED, confirm_flag_present=True)
    if not env.get(API_KEY_ENV, "").strip():
        return ApiProviderSmokeSummary(
            ApiProviderClassification.KEY_MISSING,
            provider_flags_enabled=True,
            confirm_flag_present=True,
        )

    provider = OpenAIApiProvider(confirm_real_api=True, env=env, transport=transport)
    with tempfile.TemporaryDirectory(prefix="forgex-api-provider-qa-") as temporary:
        root = Path(temporary)
        active = root / "active"
        active.mkdir()
        (active / "README.txt").write_text("ForgeX real API provider QA fixture.\n", encoding="utf-8")
        runtime = ForgeXToolRuntime(
            managed_sandbox_root=root / "managed-sandboxes",
            active_workspace_root=active,
            review_service=BridgeDiffService(),
        )
        result = runtime.run(
            task="Create the exact ForgeX API provider smoke artifact.",
            provider=provider,
            policy=api_provider_smoke_policy(),
        )
    classification = _api_classification(result.classification, result.provider_classification)
    provider_metadata = provider.to_safe_metadata()
    outbound_count = int(provider_metadata.get("outbound_request_count", 0))
    plan_valid = provider_metadata.get("classification") == ApiProviderClassification.PASS.value
    return ApiProviderSmokeSummary(
        classification=classification,
        model_id=provider.model_id,
        execution_attempted=result.execution_attempted,
        provider_flags_enabled=True,
        confirm_flag_present=True,
        api_key_detected=True,
        outbound_request_attempted=outbound_count == 1,
        outbound_request_count=outbound_count,
        tool_plan_valid=plan_valid,
        tool_execution_count=result.tool_execution_count,
        created_file_count=result.created_file_count,
        modified_file_count=result.modified_file_count,
        deleted_file_count=result.deleted_file_count,
        expected_file_created=result.created_file_count == 1 and result.modified_file_count == 0 and result.deleted_file_count == 0,
        content_exact=classification is ApiProviderClassification.PASS,
        review_created=result.review_created,
        active_workspace_unchanged=result.active_workspace_unchanged,
        marker_unchanged=result.marker_unchanged,
    )


def _api_classification(runtime: RuntimeClassification, provider_classification: str | None) -> ApiProviderClassification:
    if provider_classification:
        return ApiProviderClassification(provider_classification)
    return {
        RuntimeClassification.PASS: ApiProviderClassification.PASS,
        RuntimeClassification.POLICY_DENIED: ApiProviderClassification.POLICY_DENIED,
        RuntimeClassification.PATH_UNSAFE: ApiProviderClassification.PATH_UNSAFE,
        RuntimeClassification.CONTENT_INVALID: ApiProviderClassification.CONTENT_INVALID,
        RuntimeClassification.EXTRA_CHANGES: ApiProviderClassification.EXTRA_CHANGES,
        RuntimeClassification.NO_CHANGES: ApiProviderClassification.NO_CHANGES,
        RuntimeClassification.UNSAFE_ABORTED: ApiProviderClassification.UNSAFE_ABORTED,
    }.get(runtime, ApiProviderClassification.UNKNOWN_SAFE_FAILURE)


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--provider", default="")
    parser.add_argument("--confirm-real-api", action="store_true")
    args = parser.parse_args(argv)
    summary = run_smoke(
        provider_name=args.provider,
        confirmed=args.confirm_real_api,
        env=env if env is not None else os.environ,
    )
    payload = summary.to_safe_dict()
    print(summary.classification.value)
    print(f"review_created={str(summary.review_created).lower()}")
    print(f"created_file_count={summary.created_file_count}")
    print(f"outbound_request_attempted={str(summary.outbound_request_attempted).lower()}")
    print(f"outbound_request_count={summary.outbound_request_count}")
    print(f"active_workspace_unchanged={str(summary.active_workspace_unchanged).lower()}")
    print(f"apply_run={str(summary.apply_run).lower()}")
    print(f"build_run={str(summary.build_run).lower()}")
    print(f"flash_run={str(summary.flash_run).lower()}")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    if summary.classification in {ApiProviderClassification.PASS, ApiProviderClassification.KEY_MISSING}:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
