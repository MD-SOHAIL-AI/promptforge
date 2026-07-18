"""Feature-gated Codex App Server provider for managed ForgeX sandboxes."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ...agent_runtime.approval_broker import AgentApprovalBroker
if TYPE_CHECKING:
    from backend.connection_registry import ConnectionRegistry
from ...provider_runtime import classify_cli_failure
from ..codex_status import CodexStatusService, build_codex_safe_user_env
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
from .sandbox_agent_artifacts import create_review_artifact


class CodexAppServerProvider:
    provider_id = "codex"
    environment_allowlist = frozenset(build_codex_safe_user_env().keys())
    detection_cache_seconds = 5.0

    def __init__(
        self,
        *,
        status_service: CodexStatusService,
        review_service: BridgeDiffService,
        managed_root: str | Path,
        approval_broker: AgentApprovalBroker,
        execution_enabled: bool = False,
        connections: ConnectionRegistry | None = None,
    ) -> None:
        self.status_service = status_service
        self.connections = connections
        self.review_service = review_service
        self.managed_root = Path(managed_root).resolve()
        self.execution_enabled = bool(execution_enabled)
        self.approval_broker = approval_broker
        self._version: str | None = None
        self._available = False
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._final_messages: dict[str, str] = {}
        self._project_threads: dict[str, str] = {}
        self._detection_cache: tuple[float, BridgeDetectionResult] | None = None
        self._detection_lock = asyncio.Lock()

    def capabilities(self) -> BridgeCapabilities:
        return BridgeCapabilities(
            provider_id=self.provider_id,
            display_name="OpenAI Codex",
            provider_version=self._version,
            available=self._available,
            non_interactive=True,
            sandbox_required=True,
            supports_streaming=True,
            supports_cancellation=True,
            supports_timeout=True,
            supports_artifacts=True,
            supports_structured_output=True,
            supports_subscription_auth=True,
            supports_api_key_auth=True,
            supports_resume=True,
        )

    async def detect(self) -> BridgeDetectionResult:
        now = time.monotonic()
        cached = self._detection_cache
        if cached is not None and now - cached[0] < self.detection_cache_seconds:
            return cached[1]
        async with self._detection_lock:
            now = time.monotonic()
            cached = self._detection_cache
            if cached is not None and now - cached[0] < self.detection_cache_seconds:
                return cached[1]
            if self.connections is not None:
                record = await asyncio.to_thread(self.connections.refresh_status, self.connections.CODEX_ID)
                self._version = record.version
                self._available = record.connection_ready
                authentication = (
                    "authenticated" if record.auth_state.value == "authenticated" else
                    "unauthenticated" if record.auth_state.value == "signed_out" else
                    "unknown"
                )
                result = BridgeDetectionResult(
                    provider_id=self.provider_id,
                    installed=record.detected,
                    available=record.connection_ready,
                    safe_message="Codex CLI connection is ready." if record.connection_ready else "Codex authentication is unavailable or unverified.",
                    provider_version=record.version,
                    authentication_status=authentication,
                    unavailability_code=None if record.connection_ready else BridgeErrorCode.PROVIDER_UNAVAILABLE.value,
                    capabilities=self.capabilities(),
                )
            else:
                status = await asyncio.to_thread(self.status_service.status)
                self._version = status.codex_version
                self._available = bool(status.codex_installed and status.oauth_bridge_ready)
                authentication = (
                    "authenticated" if status.auth_status == "signed_in" else
                    "unauthenticated" if status.auth_status == "signed_out" else
                    "not_installed" if not status.codex_installed else "unknown"
                )
                result = BridgeDetectionResult(
                    provider_id=self.provider_id,
                    installed=status.codex_installed,
                    available=self._available,
                    safe_message="Codex App Server is ready." if self._available else "Codex authentication is required.",
                    provider_version=status.codex_version,
                    authentication_status=authentication,
                    unavailability_code=None if self._available else BridgeErrorCode.PROVIDER_UNAVAILABLE.value,
                    capabilities=self.capabilities(),
                )
            self._detection_cache = (time.monotonic(), result)
            return result

    async def validate(self, request: BridgeRunRequest) -> BridgeValidationResult:
        if request.provider_id != self.provider_id or request.requested_capability != "edit_files":
            return BridgeValidationResult(False, BridgeErrorCode.PROVIDER_UNSUPPORTED.value, "Codex request is unsupported.")
        if not self.execution_enabled:
            return BridgeValidationResult(False, BridgeErrorCode.PROVIDER_DISABLED.value, "Codex provider is disabled.")
        detected = await self.detect()
        if not detected.available:
            return BridgeValidationResult(False, BridgeErrorCode.PROVIDER_UNAVAILABLE.value, detected.safe_message)
        return BridgeValidationResult(True, safe_message="Codex request is valid for managed sandbox execution.")

    async def start(self, context: BridgeRunContext) -> BridgeRunResult:
        validation = await self.validate(context.request)
        if not validation.valid:
            return BridgeRunResult(False, failure_code=validation.failure_code, safe_message=validation.safe_message)
        sandbox = context.sandbox.internal_sandbox_root
        baseline = self.review_service.inspect_workspace(sandbox)
        active_baseline = self.review_service.inspect_workspace(context.sandbox.active_workspace_root)
        executable = self.status_service.resolve_executable(build_codex_safe_user_env())
        if executable is None:
            return BridgeRunResult(False, failure_code=BridgeErrorCode.PROVIDER_UNAVAILABLE.value, safe_message="Codex executable is unavailable.")
        command = [executable.path, *executable.prefix_args, "app-server", "--listen", "stdio://"]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(sandbox),
                env=build_codex_safe_user_env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            return BridgeRunResult(False, failure_code=BridgeErrorCode.PROCESS_START_FAILED.value, safe_message="Codex App Server could not start.")
        self._processes[context.request.run_id] = process
        stderr_task = asyncio.create_task(self._drain(process.stderr))
        try:
            final_message = await asyncio.wait_for(
                self._run_protocol(process, context),
                timeout=context.request.timeout_seconds,
            )
            if final_message:
                self._remember_message(context.request.run_id, final_message)
        except asyncio.TimeoutError:
            await self._terminate(process)
            if self.review_service.diff_snapshot(active_baseline, context.sandbox.active_workspace_root):
                return BridgeRunResult(False, failure_code=BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED.value, safe_message="The active workspace changed during Codex execution.")
            return BridgeRunResult(False, failure_code=BridgeErrorCode.TIMEOUT.value, safe_message="Codex run timed out.")
        except (ValueError, RuntimeError, json.JSONDecodeError, asyncio.IncompleteReadError) as exc:
            await self._terminate(process)
            if self.review_service.diff_snapshot(active_baseline, context.sandbox.active_workspace_root):
                return BridgeRunResult(False, failure_code=BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED.value, safe_message="The active workspace changed during Codex execution.")
            code = classify_cli_failure(str(exc))
            return BridgeRunResult(False, failure_code=code.value, safe_message="Codex App Server protocol failed safely.")
        finally:
            await self._terminate(process)
            self._processes.pop(context.request.run_id, None)
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
        if self.review_service.diff_snapshot(active_baseline, context.sandbox.active_workspace_root):
            return BridgeRunResult(False, failure_code=BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED.value, safe_message="The active workspace changed during Codex execution.")
        artifact = create_review_artifact(
            provider_id=self.provider_id,
            run_id=context.request.run_id,
            sandbox_root=sandbox,
            managed_root=self.managed_root,
            baseline=baseline,
            review_service=self.review_service,
            artifact_source="codex_app_server",
        )
        if artifact is None:
            if final_message:
                return BridgeRunResult(
                    True,
                    safe_message="Codex completed with a conversational response.",
                    final_status="completed",
                )
            return BridgeRunResult(False, failure_code=BridgeErrorCode.NO_CHANGES_PRODUCED.value, safe_message="Codex completed without sandbox changes.")
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

    def final_message(self, run_id: str) -> str | None:
        return self._final_messages.get(run_id)

    async def _run_protocol(self, process: asyncio.subprocess.Process, context: BridgeRunContext) -> str | None:
        await self._send(process, {"method": "initialize", "id": 1, "params": {"clientInfo": {"name": "forgex", "title": "ForgeX", "version": "0.1.0"}}})
        await self._wait_for_response(process, 1)
        await self._send(process, {"method": "initialized", "params": {}})
        existing_thread = self._project_threads.get(context.request.project_id)
        await self._send(process, {
            "method": "thread/resume" if existing_thread else "thread/start",
            "id": 2,
            "params": ({
                "threadId": existing_thread,
                "cwd": str(context.sandbox.internal_sandbox_root),
                "approvalPolicy": "on-request",
                "sandbox": "workspace-write",
            } if existing_thread else {
                "cwd": str(context.sandbox.internal_sandbox_root),
                "approvalPolicy": "on-request",
                # Codex 0.142.x generated schemas use the kebab-case v2 value.
                "sandbox": "workspace-write",
                "serviceName": "forgex",
            }),
        })
        response = await self._wait_for_response(process, 2)
        thread = response.get("result", {}).get("thread", {})
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("codex_thread_missing")
        if context.request.project_id not in self._project_threads and len(self._project_threads) >= 100:
            self._project_threads.pop(next(iter(self._project_threads)))
        self._project_threads[context.request.project_id] = thread_id
        await self._send(process, {
            "method": "turn/start",
            "id": 3,
            "params": {"threadId": thread_id, "input": [{"type": "text", "text": context.request.instruction}]},
        })
        message_parts: dict[str, str] = {}
        final_item_ids: list[str] = []
        while True:
            message = await self._read(process)
            method = message.get("method")
            if "id" in message and method:
                if method == "item/fileChange/requestApproval":
                    decision = "accept"
                else:
                    requested = await self.approval_broker.request(
                        run_id=context.request.run_id,
                        kind="network" if "network" in method.casefold() else "command",
                        safe_message="Codex requests permission for a sandbox operation.",
                    )
                    decision = "acceptForSession" if requested == "approve_session" else "accept" if requested == "approve_once" else "cancel" if requested == "cancel" else "decline"
                await self._send(process, {"id": message["id"], "result": {"decision": decision}})
                continue
            params = message.get("params", {})
            if method == "item/agentMessage/delta" and isinstance(params, dict):
                item_id = params.get("itemId")
                delta = params.get("delta")
                if isinstance(item_id, str) and isinstance(delta, str):
                    message_parts[item_id] = (message_parts.get(item_id, "") + delta)[:16_384]
                    self._remember_message(context.request.run_id, message_parts[item_id])
            elif method == "item/completed" and isinstance(params, dict):
                item = params.get("item", {})
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    item_id = item.get("id")
                    text = item.get("text")
                    if isinstance(item_id, str) and isinstance(text, str):
                        message_parts[item_id] = text[:16_384]
                        self._remember_message(context.request.run_id, message_parts[item_id])
                        if item.get("phase") == "final_answer" or not final_item_ids:
                            final_item_ids.append(item_id)
            if method == "turn/completed":
                if final_item_ids:
                    return message_parts.get(final_item_ids[-1]) or None
                return next((text for text in reversed(tuple(message_parts.values())) if text), None)
            if method == "turn/failed" or (message.get("id") == 3 and "error" in message):
                diagnostic = json.dumps(message.get("error") or params, ensure_ascii=True)[:4096]
                raise RuntimeError(classify_cli_failure(diagnostic).value)

    async def _wait_for_response(self, process: asyncio.subprocess.Process, request_id: int) -> dict[str, Any]:
        while True:
            message = await self._read(process)
            if message.get("id") == request_id:
                if "error" in message:
                    diagnostic = json.dumps(message.get("error"), ensure_ascii=True)[:4096]
                    raise RuntimeError(classify_cli_failure(diagnostic).value)
                return message

    def _remember_message(self, run_id: str, message: str) -> None:
        if run_id not in self._final_messages and len(self._final_messages) >= 100:
            self._final_messages.pop(next(iter(self._final_messages)))
        self._final_messages[run_id] = message[:16_384]

    @staticmethod
    async def _send(process: asyncio.subprocess.Process, payload: dict[str, Any]) -> None:
        if process.stdin is None:
            raise RuntimeError("codex_stdin_missing")
        process.stdin.write((json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8"))
        await process.stdin.drain()

    @staticmethod
    async def _read(process: asyncio.subprocess.Process) -> dict[str, Any]:
        if process.stdout is None:
            raise RuntimeError("codex_stdout_missing")
        raw = await process.stdout.readline()
        if not raw or len(raw) > 1024 * 1024:
            raise RuntimeError("codex_message_invalid")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError("codex_message_invalid")
        return value

    @staticmethod
    async def _drain(stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while await stream.read(8192):
            pass

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
