"""Feature-gated Antigravity SDK sidecar provider."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

from ..diff_service import BridgeDiffService
from ..generic.errors import BridgeErrorCode
from ..generic.models import (
    BridgeCancellationDisposition,
    BridgeCancellationEscalation,
    BridgeCancellationResult,
    BridgeCapabilities,
    BridgeDetectionResult,
    BridgeRunContext,
    BridgeRunRequest,
    BridgeRunResult,
    BridgeValidationResult,
)
from .antigravity_runner import SAFE_ENV_NAMES, safe_bridge_env
from .sandbox_agent_artifacts import create_review_artifact


class AGYSDKProvider:
    provider_id = "agy"
    environment_allowlist = frozenset(SAFE_ENV_NAMES)

    def __init__(
        self,
        *,
        review_service: BridgeDiffService,
        managed_root: str | Path,
        execution_enabled: bool = False,
    ) -> None:
        self.review_service = review_service
        self.managed_root = Path(managed_root).resolve()
        self.execution_enabled = bool(execution_enabled)
        self._available = importlib.util.find_spec("google.antigravity") is not None
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    def capabilities(self) -> BridgeCapabilities:
        return BridgeCapabilities(
            provider_id=self.provider_id,
            display_name="Google Antigravity",
            available=self._available,
            non_interactive=True,
            sandbox_required=True,
            supports_streaming=False,
            supports_cancellation=True,
            supports_timeout=True,
            supports_artifacts=True,
            supports_structured_output=True,
            supports_subscription_auth=True,
        )

    async def detect(self) -> BridgeDetectionResult:
        self._available = importlib.util.find_spec("google.antigravity") is not None
        return BridgeDetectionResult(
            provider_id=self.provider_id,
            installed=self._available,
            available=self._available,
            safe_message="Antigravity SDK is available." if self._available else "Antigravity SDK is not installed.",
            authentication_status="unknown" if self._available else "not_installed",
            unavailability_code=None if self._available else BridgeErrorCode.PROVIDER_UNAVAILABLE.value,
            capabilities=self.capabilities(),
        )

    async def validate(self, request: BridgeRunRequest) -> BridgeValidationResult:
        if request.provider_id != self.provider_id or request.requested_capability != "edit_files":
            return BridgeValidationResult(False, BridgeErrorCode.PROVIDER_UNSUPPORTED.value, "AGY request is unsupported.")
        if not self.execution_enabled:
            return BridgeValidationResult(False, BridgeErrorCode.PROVIDER_DISABLED.value, "AGY SDK provider is disabled.")
        if not (await self.detect()).available:
            return BridgeValidationResult(False, BridgeErrorCode.PROVIDER_UNAVAILABLE.value, "Antigravity SDK is unavailable.")
        return BridgeValidationResult(True, safe_message="AGY request is valid for managed sandbox execution.")

    async def start(self, context: BridgeRunContext) -> BridgeRunResult:
        validation = await self.validate(context.request)
        if not validation.valid:
            return BridgeRunResult(False, failure_code=validation.failure_code, safe_message=validation.safe_message)
        sandbox = context.sandbox.internal_sandbox_root
        baseline = self.review_service.inspect_workspace(sandbox)
        active_baseline = self.review_service.inspect_workspace(context.sandbox.active_workspace_root)
        env = safe_bridge_env(dict(os.environ))
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "backend.bridges.agy_sdk_sidecar",
                cwd=str(sandbox),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            return BridgeRunResult(False, failure_code=BridgeErrorCode.PROCESS_START_FAILED.value, safe_message="AGY SDK sidecar could not start.")
        self._processes[context.request.run_id] = process
        request = json.dumps({"instruction": context.request.instruction, "workspace": str(sandbox)}, ensure_ascii=True) + "\n"
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(request.encode("utf-8")), timeout=context.request.timeout_seconds)
        except asyncio.TimeoutError:
            await self._terminate(process)
            if self.review_service.diff_snapshot(active_baseline, context.sandbox.active_workspace_root):
                return BridgeRunResult(False, failure_code=BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED.value, safe_message="The active workspace changed during AGY execution.")
            return BridgeRunResult(False, failure_code=BridgeErrorCode.TIMEOUT.value, safe_message="AGY SDK run timed out.")
        finally:
            self._processes.pop(context.request.run_id, None)
        try:
            payload = json.loads(stdout[:16_384])
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        if process.returncode != 0 or payload.get("status") != "completed":
            if self.review_service.diff_snapshot(active_baseline, context.sandbox.active_workspace_root):
                return BridgeRunResult(False, failure_code=BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED.value, safe_message="The active workspace changed during AGY execution.")
            code = payload.get("code")
            safe = "Antigravity SDK is unavailable." if code == "sdk_unavailable" else "AGY SDK run failed safely."
            return BridgeRunResult(False, failure_code=BridgeErrorCode.PROCESS_FAILED.value, safe_message=safe)
        if self.review_service.diff_snapshot(active_baseline, context.sandbox.active_workspace_root):
            return BridgeRunResult(False, failure_code=BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED.value, safe_message="The active workspace changed during AGY execution.")
        artifact = create_review_artifact(
            provider_id=self.provider_id,
            run_id=context.request.run_id,
            sandbox_root=sandbox,
            managed_root=self.managed_root,
            baseline=baseline,
            review_service=self.review_service,
            artifact_source="agy_sdk",
        )
        if artifact is None:
            return BridgeRunResult(False, failure_code=BridgeErrorCode.NO_CHANGES_PRODUCED.value, safe_message="AGY completed without sandbox changes.")
        return BridgeRunResult(True, artifact_ids=(artifact.artifact_id,), artifacts=(artifact,), review_id=artifact.review_id, final_status="completed")

    async def cancel(self, run_id: str, reason_code: str = "user_requested") -> BridgeCancellationResult:
        process = self._processes.get(run_id)
        if process is None:
            return BridgeCancellationResult(BridgeCancellationDisposition.NOT_FOUND, run_id, reason_code=reason_code)
        await self._terminate(process)
        return BridgeCancellationResult(
            BridgeCancellationDisposition.ACCEPTED,
            run_id,
            reason_code=reason_code,
            escalation=BridgeCancellationEscalation.PROCESS_TERMINATION,
        )

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
