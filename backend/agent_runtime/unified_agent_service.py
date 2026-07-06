"""Canonical ForgeX control plane for planner and sandbox-agent runs."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path

from ..bridges.generic.coordinator import GenericBridgeRunCoordinator
from ..bridges.generic.errors import BridgeDomainError
from ..bridges.generic.models import BridgeRunRequest, BridgeSandboxContext
from ..bridges.generic.registry import BridgeProviderRegistry
from ..bridges.generic.states import TERMINAL_STATES
from ..bridges.generic.validation import format_datetime
from ..bridges.sandbox_service import BridgeSandboxService
from .product_agent_service import ProductAgentService
from .approval_broker import AgentApprovalBroker
from ..provider_runtime import ProviderRunSummaryStore, ProviderType, ProviderState, WorkspaceMode, ForgeXRunSummary, GenerationStatus, detect_workspace_mode


LOCAL_PROVIDER_IDS = frozenset({"agy", "codex"})
PUBLIC_API_PROVIDER_IDS = frozenset({"openai", "openrouter", "groq"})
class UnifiedAgentService:
    def __init__(
        self,
        *,
        product_service: ProductAgentService,
        bridge_registry: BridgeProviderRegistry,
        bridge_coordinator: GenericBridgeRunCoordinator,
        sandbox_service: BridgeSandboxService,
        approval_broker: AgentApprovalBroker,
        enabled: bool,
        summary_store: ProviderRunSummaryStore | None = None,
    ) -> None:
        self.product_service = product_service
        self.bridge_registry = bridge_registry
        self.bridge_coordinator = bridge_coordinator
        self.sandbox_service = sandbox_service
        self.approval_broker = approval_broker
        self.enabled = bool(enabled)
        self.summary_store = summary_store
        self._local_tasks: dict[str, asyncio.Task[object]] = {}

    async def provider_statuses(self) -> tuple[dict[str, object], ...]:
        product: list[dict[str, object]] = []
        for item in self.product_service.provider_registry.safe_statuses():
            provider_id = str(item["provider_id"])
            if provider_id in LOCAL_PROVIDER_IDS or provider_id == "verified_template":
                continue
            if provider_id in PUBLIC_API_PROVIDER_IDS:
                product.append({
                    **item,
                    "provider_type": ProviderType.API.value,
                    "auth_type": "api_key",
                    "generation_source": item.get("display_name") or provider_id,
                    "state": _api_provider_state(str(item.get("state") or "")),
                })
            else:
                product.append(dict(item))
        local: list[dict[str, object]] = []
        for provider in self.bridge_registry.list_registered():
            if provider.provider_id not in LOCAL_PROVIDER_IDS:
                continue
            try:
                detected = await provider.detect()
            except Exception:
                detected = None
            execution_enabled = bool(getattr(provider, "execution_enabled", False))
            available = bool(detected and detected.available)
            routeable = self.enabled and execution_enabled and available
            state = (
                ProviderState.DISABLED.value if not self.enabled or not execution_enabled else
                ProviderState.CLI_NOT_FOUND.value if not detected or not detected.installed else
                ProviderState.NOT_LOGGED_IN.value if detected.authentication_status != "authenticated" else
                ProviderState.READY.value
            )
            local.append(
                {
                    "provider_id": provider.provider_id,
                    "display_name": provider.capabilities().display_name,
                    "kind": ProviderType.AGENT.value,
                    "provider_kind": ProviderType.AGENT.value,
                    "provider_type": ProviderType.AGENT.value,
                    "auth_type": "cli_session",
                    "generation_source": provider.capabilities().display_name,
                    "state": state,
                    "routeable": routeable,
                    "enabled_by_default": False,
                    "provider_flag": "FORGEX_ENABLE_AGY_SDK_PROVIDER" if provider.provider_id == "agy" else "FORGEX_ENABLE_CODEX_PROVIDER",
                    "provider_flag_enabled": execution_enabled,
                    "supports_toolplan": False,
                    "supports_streaming": provider.capabilities().supports_streaming,
                    "detected": bool(detected and detected.installed),
                    "authenticated": bool(detected and detected.authentication_status == "authenticated"),
                    "execution_mode": "sandbox_agent",
                    "workspace_mode": "managed_sandbox",
                    "production_eligible": False,
                    "product_routing_enabled": routeable,
                    "paused_reason": None if routeable else detected.safe_message if detected else "Provider detection failed safely.",
                }
            )
        template = {
            "provider_id": "verified_template",
            "display_name": "Verified template",
            "kind": ProviderType.TEMPLATE.value,
            "provider_kind": ProviderType.TEMPLATE.value,
            "provider_type": ProviderType.TEMPLATE.value,
            "auth_type": "none",
            "generation_source": "Verified template",
            "state": ProviderState.READY.value,
            "routeable": self.enabled,
            "production_eligible": True,
            "product_routing_enabled": self.enabled,
            "supports_toolplan": False,
            "supports_streaming": False,
        }
        prepared = [
            {
                "provider_id": provider_id,
                "display_name": display_name,
                "kind": ProviderType.API.value,
                "provider_kind": ProviderType.API.value,
                "provider_type": ProviderType.API.value,
                "auth_type": "api_key",
                "generation_source": display_name,
                "state": ProviderState.DISABLED.value,
                "routeable": False,
                "production_eligible": False,
                "product_routing_enabled": False,
                "paused_reason": "Adapter prepared for a later release.",
            }
            for provider_id, display_name in (("anthropic", "Anthropic API"), ("cerebras", "Cerebras API"))
        ]
        return tuple(sorted([template, *product, *prepared, *local], key=lambda item: str(item["provider_id"])))

    async def start_run(
        self,
        *,
        project_id: str,
        active_workspace_root: str | Path,
        instruction: str,
        provider_id: str,
        timeout_seconds: int = 300,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        if not self.enabled:
            raise PermissionError("AGENT_RUNTIME_DISABLED")
        if provider_id not in LOCAL_PROVIDER_IDS:
            fallback_provider_id = self.product_service.provider_registry.fallback_for_instruction(
                provider_id, instruction
            )
            run = await self.product_service.start_run(
                project_id=project_id,
                active_workspace_root=active_workspace_root,
                instruction=instruction,
                provider_id=provider_id,
                fallback_provider_id=fallback_provider_id,
            )
            return run.to_safe_dict()

        instruction_hash = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
        if idempotency_key:
            existing = next(
                (item for item in self.bridge_coordinator.list_recent_runs(limit=100) if item.correlation_id == idempotency_key),
                None,
            )
            if existing is not None:
                if (
                    existing.provider_id != provider_id
                    or existing.project_id != project_id
                    or existing.instruction_hash != instruction_hash
                ):
                    raise ValueError("AGENT_RUNTIME_IDEMPOTENCY_CONFLICT")
                return self._generic_run(existing.run_id)
        active = [item for item in self.bridge_coordinator.list_recent_runs(limit=100) if item.status not in TERMINAL_STATES]
        if len(active) >= 3 or any(item.project_id == project_id for item in active):
            raise ValueError("AGENT_RUNTIME_ACTIVE_RUN_LIMIT")

        suffix = uuid.uuid4().hex
        run_id = f"agent-run-{suffix}"
        sandbox_id = f"sandbox-{suffix}"
        correlation_id = idempotency_key or f"request-{suffix}"
        workspace = Path(active_workspace_root).resolve(strict=True)
        sandbox = self.sandbox_service.create_sandbox(run_id=run_id, workspace_root=workspace)
        request = BridgeRunRequest(
            run_id=run_id,
            provider_id=provider_id,
            project_id=project_id,
            sandbox_id=sandbox_id,
            requested_capability="edit_files",
            instruction=instruction,
            timeout_seconds=timeout_seconds,
            correlation_id=correlation_id,
            execution_mode="generic",
        )
        context = BridgeSandboxContext(
            sandbox_id=sandbox_id,
            run_id=run_id,
            project_id=project_id,
            creation_status="ready",
            cleanup_policy="retain_for_review",
            containment_verified=True,
            internal_sandbox_root=sandbox,
            managed_sandbox_root=self.sandbox_service.sandbox_root,
            active_workspace_root=workspace,
        )
        task = asyncio.create_task(self.bridge_coordinator.execute(request, context), name=run_id)
        self._local_tasks[run_id] = task
        task.add_done_callback(lambda _: self._local_tasks.pop(run_id, None))
        for _ in range(100):
            try:
                return self._generic_run(run_id)
            except BridgeDomainError:
                await asyncio.sleep(0)
        raise RuntimeError("AGENT_RUNTIME_PERSISTENCE_FAILED")

    def get_run(self, run_id: str) -> dict[str, object]:
        try:
            return self.product_service.get_run(run_id).to_safe_dict()
        except KeyError:
            return self._generic_run(run_id)

    def events(self, run_id: str, *, after_sequence: int = 0) -> tuple[dict[str, object], ...]:
        try:
            return self.product_service.events(run_id, after_sequence=after_sequence)
        except KeyError:
            run = self.bridge_coordinator.get_run(run_id)
            provider = self.bridge_registry.get(run.provider_id)
            source = provider.capabilities().display_name
            return tuple(
                _canonical_local_event(item.to_dict(), run_id=run_id, provider_id=run.provider_id, generation_source=source)
                for item in self.bridge_coordinator.list_run_events(run_id, after_sequence=after_sequence)
            )

    async def cancel(self, run_id: str) -> dict[str, object]:
        try:
            return self.product_service.cancel(run_id).to_safe_dict()
        except KeyError:
            self.approval_broker.clear_run(run_id)
            await self.bridge_coordinator.cancel(run_id)
            return self._generic_run(run_id)

    def resolve_approval(self, *, run_id: str, approval_id: str, decision: str) -> dict[str, str]:
        self.get_run(run_id)
        return self.approval_broker.resolve(run_id=run_id, approval_id=approval_id, decision=decision)

    async def close(self) -> None:
        await self.product_service.close()
        tasks = tuple(self._local_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _generic_run(self, run_id: str) -> dict[str, object]:
        record = self.bridge_coordinator.get_run(run_id)
        artifacts = self.bridge_coordinator.list_run_artifacts(run_id)
        review_id = next((item.get("review_id") for item in artifacts if item.get("review_id")), None)
        provider = self.bridge_registry.get(record.provider_id)
        message_reader = getattr(provider, "final_message", None)
        assistant_message = message_reader(run_id) if callable(message_reader) else None
        result = {
            "run_id": record.run_id,
            "project_id": record.project_id,
            "provider_id": record.provider_id,
            "status": record.status.value,
            "classification": record.failure_code,
            "review_id": review_id,
            "created_file_count": 0,
            "modified_file_count": 0,
            "deleted_file_count": 0,
            "tool_execution_count": 0,
            "active_workspace_unchanged": True,
            "raw_prompt_persisted": False,
            "raw_response_persisted": False,
            "apply_run": False,
            "build_run": False,
            "flash_run": False,
            "cancellable": record.status not in TERMINAL_STATES,
            "created_at": format_datetime(record.created_at),
            "updated_at": format_datetime(record.updated_at),
            "execution_mode": "sandbox_agent",
            "assistant_message": assistant_message,
            "pending_approvals": list(self.approval_broker.list_for_run(run_id)),
            "summary": ForgeXRunSummary(
                run_id=record.run_id,
                requested_provider=record.provider_id,
                actual_provider=record.provider_id,
                provider_type=ProviderType.AGENT,
                generation_source=provider.capabilities().display_name,
                workspace_mode=detect_workspace_mode(Path(record.workspace_root)) if getattr(record, "workspace_root", None) else WorkspaceMode.GENERATE_INTO_FOLDER,
                generation_status=(GenerationStatus.CONTENT_VERIFIED.value if record.status.value == "completed" else GenerationStatus.PROVIDER_FAILED.value if record.status in TERMINAL_STATES else GenerationStatus.STARTED.value),
                content_verification_status="verified" if review_id else "no_changes" if record.status.value == "completed" else "pending",
                no_op_status=record.status.value == "completed" and not review_id,
                next_suggested_action="Review generated changes before applying them." if review_id else None,
            ).to_safe_dict(),
        }
        if self.summary_store is not None:
            try:
                self.summary_store.save(result["summary"])  # type: ignore[arg-type]
            except (OSError, ValueError):
                pass
        return result


def _canonical_local_event(
    event: dict[str, object],
    *,
    run_id: str,
    provider_id: str,
    generation_source: str,
) -> dict[str, object]:
    value = dict(event)
    value.setdefault("run_id", run_id)
    value.setdefault("stage", "generation")
    value.setdefault("status", str(value.get("event_type") or "running"))
    value.setdefault("provider_id", provider_id)
    value.setdefault("provider_type", ProviderType.AGENT.value)
    value.setdefault("generation_source", generation_source)
    value.setdefault("message", str(value.get("safe_message") or value.get("event_type") or "generation update"))
    value.setdefault("timestamp", value.get("created_at") or value.get("occurred_at") or "")
    value.setdefault("safe_summary", {})
    return value


def _api_provider_state(value: str) -> str:
    normalized = value.casefold()
    if normalized.endswith("ready"):
        return ProviderState.READY.value
    if "key_missing" in normalized:
        return ProviderState.MISSING_API_KEY.value
    if "rate_limit" in normalized:
        return ProviderState.RATE_LIMITED.value
    if "quota" in normalized:
        return ProviderState.USAGE_LIMIT_REACHED.value
    if "disabled" in normalized or "confirmation_required" in normalized:
        return ProviderState.DISABLED.value
    return ProviderState.UNAVAILABLE.value
