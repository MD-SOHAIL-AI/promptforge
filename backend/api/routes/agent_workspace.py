"""Unified Agent workspace over the durable workflow engine."""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, Header, Request
from pydantic import Field

from backend.agent_runtime.agent_adapter import (
    AdapterReadiness,
    AgentAdapterError,
    ApprovedBoundedContext,
)
from backend.agent_runtime.agent_profiles import AgentProfileService
from backend.agent_runtime.workflow_engine import (
    AgentWorkflowEngine,
    ApprovalBindingMismatch,
    ApprovalExpired,
    ExecutionOutcome,
    FlashApprovalBinding,
    HardwareSafetyDenied,
    WorkflowEngineError,
)
from backend.bridges.audit_log import hash_workspace_root
from backend.bridges.diff_service import hash_file
from backend.bridges.patch_apply_service import PATCH_APPLY_DISABLED_MESSAGE, PATCH_APPLY_REQUIRES_RESTORE_MESSAGE, PatchApplyError
from backend.bridges.patch_export_service import BridgePatchExportError
from backend.runtime.result import BuildResult
from backend.state.domain_persistence import DomainRepository, _decode, _json, _now, _safe_payload
from backend.state.durable_workflow import InvalidTransition, StaleRunVersion, TERMINAL, WorkflowState

from ..dependencies import resolve_project_from_request
from ..errors import APIError
from ..schemas.common import APIModel


router = APIRouter(prefix="/agent-workspace", tags=["agent-workspace"])
AGENT_EXECUTOR_FLAG = "FORGEX_ENABLE_AGENT_WORKSPACE_EXECUTORS"
AGENT_FLASH_FLAG = "FORGEX_ENABLE_AGENT_WORKSPACE_FLASH"


class FailClosedForgeXExecutors:
    production_eligible = False
    can_apply = False
    can_build = False
    can_flash = False

    def _unavailable(self, *args: object, **kwargs: object) -> ExecutionOutcome:
        return ExecutionOutcome(
            False,
            _hash("executor-unavailable"),
            "Executor parity is not production eligible.",
            False,
        )

    preflight_rollback_apply = _unavailable
    build = _unavailable
    flash = _unavailable
    monitor = _unavailable
    verify = _unavailable


@dataclass(slots=True)
class ForgeXWorkflowExecutors:
    """Thin durable-engine executor bridge to existing ForgeX services."""

    patch_export_service: Any
    patch_apply_service: Any
    platformio_service: Any
    review_service: Any
    enabled: bool = False
    flash_enabled: bool = False

    @property
    def production_eligible(self) -> bool:
        return bool(self.enabled)

    @property
    def can_apply(self) -> bool:
        return bool(self.enabled and self.patch_apply_service.is_enabled())

    @property
    def can_build(self) -> bool:
        return bool(self.enabled)

    @property
    def can_flash(self) -> bool:
        return bool(self.enabled and self.flash_enabled)

    def preflight_rollback_apply(self, *, run_id: str, review_id: str, workspace: Path) -> ExecutionOutcome:
        if not self.can_apply:
            return ExecutionOutcome(False, _hash(f"{run_id}:apply-disabled"), _apply_disabled_message(self.patch_apply_service))
        try:
            patch = self.patch_export_service.export_patch(review_id, workspace_root_hash=hash_workspace_root(str(workspace)))
            result = self.patch_apply_service.apply_patch(patch.patch_id, workspace_root=workspace, confirmation="APPLY")
        except (BridgePatchExportError, PatchApplyError) as exc:
            return ExecutionOutcome(False, _hash(f"{run_id}:apply:{type(exc).__name__}"), str(exc)[:500])
        digest = _hash(f"{result.apply_id}:{result.status}:{result.patch_id}")
        if result.status != "applied":
            return ExecutionOutcome(False, digest, "Patch apply did not complete successfully.")
        return ExecutionOutcome(True, digest, "Approved review was applied with rollback protection.")

    def build(self, *, run_id: str, workspace: Path) -> ExecutionOutcome:
        if not self.can_build:
            return ExecutionOutcome(False, _hash(f"{run_id}:build-disabled"), "Build executor is disabled.")
        try:
            result = _run_async_blocking(lambda: self.platformio_service.build(workspace))
        except Exception:
            return ExecutionOutcome(False, _hash(f"{run_id}:build-exception"), "ForgeX build did not complete successfully.", True)
        if not isinstance(result, BuildResult) or not result.success:
            return ExecutionOutcome(False, _hash(f"{run_id}:build-failed"), "ForgeX build did not complete successfully.", True)
        firmware_path = getattr(result, "firmware_path", None)
        artifact_hash = hash_file(Path(firmware_path)) if firmware_path else _hash(f"{run_id}:build:{result.duration_ms}")
        return ExecutionOutcome(True, artifact_hash, "ForgeX build completed successfully.")

    def flash(self, *, run_id: str, workspace: Path, binding: FlashApprovalBinding) -> ExecutionOutcome:
        if not self.can_flash:
            return ExecutionOutcome(False, _hash(f"{run_id}:flash-disabled"), "Flash is disabled until explicit hardware execution is enabled.")
        return ExecutionOutcome(False, _hash(f"{run_id}:flash-not-wired"), "Flash executor requires an explicit device runtime binding.")

    def monitor(self, *, run_id: str, port_hash: str) -> ExecutionOutcome:
        return ExecutionOutcome(True, _hash(f"{run_id}:monitor:{port_hash}"), "Monitor step was skipped or completed without unsafe raw log persistence.")

    def verify(self, *, run_id: str, workspace: Path) -> ExecutionOutcome:
        try:
            snapshot = self.review_service.inspect_workspace(workspace)
            digest = _hash(repr(sorted((path, item.hash) for path, item in snapshot.files.items())))
        except Exception:
            digest = _hash(f"{run_id}:verify")
        return ExecutionOutcome(True, digest, "Workspace verification completed.")


class StartRun(APIModel):
    project_id: str
    instruction: str = Field(min_length=1, max_length=20_000)
    profile_id: str = "firmware_engineer"
    profile_version: int | None = None
    routing_override: dict[str, str] | None = None


class RunAction(APIModel):
    expected_state: str
    expected_version: int
    action: str
    adapter_id: str | None = None
    review_id: str | None = None
    port_hash: str | None = None
    device_hash: str | None = None
    board_hash: str | None = None
    command_hash: str | None = None
    artifact_hash: str | None = None


def _key(value: str | None) -> str:
    if not value or len(value) < 8 or len(value) > 128:
        raise APIError(422, "IDEMPOTENCY_KEY_REQUIRED", "A valid Idempotency-Key header is required.", {})
    return value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _engine(request: Request) -> AgentWorkflowEngine:
    return request.app.state.agent_workflow_engine


def _profile_service(request: Request) -> AgentProfileService:
    return AgentProfileService(request.app.state.workflow_database)


def _context_cache(request: Request) -> dict[str, ApprovedBoundedContext]:
    cache = getattr(request.app.state, "agent_workspace_context_cache", None)
    if cache is None:
        cache = {}
        request.app.state.agent_workspace_context_cache = cache
    return cache


def _metadata(db: Any, run_id: str) -> dict[str, Any]:
    with db.connect() as con:
        row = con.execute(
            "SELECT payload_json FROM workflow_steps WHERE run_id=? AND step_id='agent_request'",
            (run_id,),
        ).fetchone()
    return _decode(row[0]) if row else {}


def _allowed(state: str, metadata: dict[str, Any], has_review: bool) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if state == "awaiting_context_consent":
        actions += [
            {"id": "approve_context", "label": "Approve context disclosure", "tone": "primary"},
            {"id": "decline_context", "label": "Decline", "tone": "danger"},
        ]
    elif state == "routing":
        actions.append({"id": "route", "label": "Confirm route", "tone": "primary"})
    elif state in {"generating", "repairing"}:
        actions.append({"id": "generate_review", "label": "Generate review", "tone": "primary"})
    elif state == "awaiting_review" and has_review:
        actions += [
            {"id": "inspect_review", "label": "Inspect review", "tone": "neutral"},
            {"id": "approve_review", "label": "Approve review", "tone": "primary"},
        ]
    elif state == "awaiting_apply_approval":
        actions.append({"id": "approve_apply", "label": "Apply approved patch", "tone": "primary"})
    elif state == "awaiting_build":
        actions += [
            {"id": "build", "label": "Build firmware", "tone": "primary"},
            {"id": "verify", "label": "Verify without build", "tone": "neutral"},
        ]
    elif state == "awaiting_flash_approval":
        actions += [
            {"id": "complete_without_flash", "label": "Complete without flash", "tone": "neutral"},
            {"id": "approve_flash", "label": "Approve flash", "tone": "primary"},
        ]
    elif state == "awaiting_monitor":
        actions += [
            {"id": "skip_monitor", "label": "Skip monitor", "tone": "neutral"},
            {"id": "monitor", "label": "Start monitor", "tone": "primary"},
        ]
    elif state == "verifying":
        actions.append({"id": "verify", "label": "Run verification", "tone": "primary"})
    if state not in {x.value for x in TERMINAL}:
        actions.append({"id": "cancel", "label": "Cancel run", "tone": "danger"})
    return actions


def _projection(request: Request, run_id: str, after: int = 0) -> dict[str, Any]:
    base = request.app.state.workflow_event_hub.projections.projection(run_id, after_sequence=after)
    db = request.app.state.workflow_database
    meta = _metadata(db, run_id)
    with db.connect() as con:
        approvals = [
            dict(row) | {"details": _decode(row["payload_json"])}
            for row in con.execute(
                "SELECT approval_id,status,action_code,requested_at,resolved_at,payload_json FROM approvals WHERE run_id=? ORDER BY requested_at",
                (run_id,),
            )
        ]
        artifacts = [
            dict(row)
            for row in con.execute(
                "SELECT artifact_id,artifact_type,content_hash,size_bytes,storage_reference,created_at FROM artifacts WHERE run_id=? ORDER BY created_at",
                (run_id,),
            )
        ]
        routing = con.execute(
            "SELECT payload_json FROM routing_decisions WHERE decision_id=?",
            (f"{run_id}.route",),
        ).fetchone()
        binding = con.execute(
            "SELECT profile_id,profile_version,profile_content_hash FROM workflow_run_profile_bindings WHERE run_id=?",
            (run_id,),
        ).fetchone()
    review = next((item for item in reversed(artifacts) if item["artifact_type"] == "review"), None)
    state = base["run"]["status"]
    stages = [
        "preparing_context",
        "awaiting_context_consent",
        "routing",
        "generating",
        "validating",
        "awaiting_review",
        "awaiting_apply_approval",
        "applying",
        "awaiting_build",
        "building",
        "repairing",
        "awaiting_flash_approval",
        "flashing",
        "awaiting_monitor",
        "monitoring",
        "verifying",
        "completed",
    ]
    executors = getattr(_engine(request), "executors", None)
    if state in {x.value for x in TERMINAL}:
        _context_cache(request).pop(run_id, None)
    return base | {
        "profile": dict(binding) if binding else None,
        "plan": [{"stage": item, "current": item == state, "completed": _stage_done(item, state, stages)} for item in stages],
        "context_disclosure": meta.get("context_disclosure"),
        "routing_decision": _decode(routing[0]) if routing else None,
        "review": review,
        "approvals": approvals,
        "artifacts": artifacts,
        "execution_status": {"build": _phase(state, "build"), "flash": _phase(state, "flash"), "monitor": _phase(state, "monitor")},
        "verification_report": {"status": state, "summary": base["run"].get("safe_summary"), "failure_code": base["run"].get("failure_code")} if state in {x.value for x in TERMINAL} else None,
        "allowed_actions": _allowed(state, meta, review is not None),
        "project": {"project_id": meta.get("project_id"), "project_name": meta.get("project_name")},
        "adapter_eligibility": meta.get("adapter_eligibility", []),
        "executor_eligibility": {
            "enabled": bool(getattr(executors, "production_eligible", False)),
            "can_apply": bool(getattr(executors, "can_apply", False)),
            "can_build": bool(getattr(executors, "can_build", False)),
            "can_flash": bool(getattr(executors, "can_flash", False)),
        },
        "runtime_context_available": run_id in _context_cache(request),
    }


def _stage_done(stage: str, state: str, stages: list[str]) -> bool:
    try:
        return stages.index(stage) < stages.index(state)
    except ValueError:
        return False


def _phase(state: str, kind: str) -> str:
    order = {
        "build": ["awaiting_build", "building"],
        "flash": ["awaiting_flash_approval", "flashing"],
        "monitor": ["awaiting_monitor", "monitoring"],
    }
    if state in order[kind]:
        return "active" if state.endswith("ing") else "waiting"
    terminal = {"completed": "completed", "failed": "failed", "cancelled": "cancelled", "timed_out": "failed"}
    return terminal.get(state, "pending")


@router.get("/bootstrap")
async def bootstrap(request: Request, project_id: str | None = None) -> dict[str, Any]:
    db = request.app.state.workflow_database
    with db.connect() as con:
        versions = [
            dict(row)
            for row in con.execute(
                "SELECT profile_id,version,content_hash,published_at,payload_json FROM published_agent_profile_versions ORDER BY profile_id,version DESC"
            )
        ]
        runs = [dict(row) for row in con.execute("SELECT run_id,status,version,safe_summary,updated_at FROM workflow_runs ORDER BY updated_at DESC LIMIT 25")]
    if project_id:
        runs = [item for item in runs if _metadata(db, item["run_id"]).get("project_id") == project_id]
    profiles = []
    for row in versions:
        payload = _decode(row.pop("payload_json"))
        profiles.append(row | {
            "identity": payload.get("identity"),
            "role": payload.get("role"),
            "routing_policy": payload.get("model_routing_policy"),
            "workflow_template": payload.get("workflow_template"),
        })
    router_service = request.app.state.model_router_service
    providers = [item.to_dict() for item in router_service.registry.list_providers()]
    routes = [item.to_dict() for item in router_service.registry.list_routes()]
    descriptors = [
        {
            "adapter_id": item.adapter_id,
            "display_name": item.display_name,
            "readiness": item.readiness.value,
            "production_eligible": item.production_eligible,
        }
        for item in request.app.state.agent_adapter_registry.descriptors()
    ]
    return {
        "schema_version": 1,
        "profiles": profiles,
        "runs": runs,
        "providers": providers,
        "routes": routes,
        "adapters": descriptors,
        "project_id": project_id,
        "legacy_compatibility_available": True,
        "authority": "backend",
    }


@router.post("/runs")
async def start_run(body: StartRun, request: Request, idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> dict[str, Any]:
    key = _key(idempotency_key)
    project = await resolve_project_from_request(request, body.project_id)
    service = _profile_service(request)
    db = request.app.state.workflow_database
    version = body.profile_version
    if version is None:
        with db.connect() as con:
            row = con.execute("SELECT MAX(version) FROM published_agent_profile_versions WHERE profile_id=?", (body.profile_id,)).fetchone()
            version = row[0] if row else None
    if not version:
        raise APIError(422, "PROFILE_NOT_PUBLISHED", "Select a published Agent Profile.", {})
    try:
        profile = service.get_version(body.profile_id, version)
    except KeyError as exc:
        raise APIError(404, "PROFILE_VERSION_NOT_FOUND", "Agent Profile version was not found.", {}) from exc
    override = body.routing_override or {}
    if override and not {"provider_id", "model_id"} <= set(override):
        raise APIError(422, "ROUTING_OVERRIDE_INVALID", "Advanced routing requires provider and model.", {})
    request_hash = _hash(body.instruction)
    run_id = f"agent-{_hash(key)[:24]}"
    engine = _engine(request)
    context = ApprovedBoundedContext(body.instruction, {}, consent_id="pending" if profile.context_policy.require_consent else "policy-approved")
    try:
        result = engine.request(run_id=run_id, idempotency_key=key, request_hash=request_hash)
    except Exception as exc:
        raise APIError(409, "AGENT_RUN_CONFLICT", str(exc), {}) from exc
    _context_cache(request)[run_id] = context
    if result.duplicate:
        meta = _metadata(db, run_id)
        if meta.get("request_hash") != request_hash or meta.get("project_id") != body.project_id or meta.get("profile_id") != body.profile_id:
            raise APIError(409, "IDEMPOTENCY_CONFLICT", "The idempotency key was used for a different run request.", {})
        return _projection(request, run_id)
    service.bind_run(run_id=run_id, profile_id=body.profile_id, version=version)
    descriptors = request.app.state.agent_adapter_registry.descriptors()
    meta = {
        "run_id": run_id,
        "step_id": "agent_request",
        "ordinal": 0,
        "status": "prepared",
        "version": 0,
        "project_id": body.project_id,
        "project_name": getattr(project, "project_name", body.project_id),
        "profile_id": body.profile_id,
        "profile_version": version,
        "request_hash": request_hash,
        "routing_override": override,
        "context_disclosure": {
            "privacy": profile.context_policy.privacy.value,
            "requires_consent": profile.context_policy.require_consent,
            "max_files": profile.context_policy.max_files,
            "max_file_bytes": profile.context_policy.max_file_bytes,
            "max_total_bytes": profile.context_policy.max_total_bytes,
            "recipient": "pending routing",
        },
        "adapter_eligibility": [
            {
                "adapter_id": item.adapter_id,
                "readiness": item.readiness.value,
                "production_eligible": item.production_eligible,
            }
            for item in descriptors
        ],
    }
    DomainRepository(db).insert("workflow_steps", meta)
    with db.transaction() as con:
        con.execute(
            "UPDATE conversation_messages SET role='user',content=? WHERE run_id=? AND event_sequence=1",
            (f"Requested an agent run for {meta['project_name']}.", run_id),
        )
    engine.bounded_context(
        run_id=run_id,
        context=context,
        context_hash=request_hash,
        requires_disclosure_consent=profile.context_policy.require_consent,
    )
    return _projection(request, run_id)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request, after_sequence: int = 0) -> dict[str, Any]:
    try:
        return _projection(request, run_id, after_sequence)
    except KeyError as exc:
        raise APIError(404, "AGENT_RUN_NOT_FOUND", "Agent run was not found.", {}) from exc


@router.post("/runs/{run_id}/actions")
async def action(run_id: str, body: RunAction, request: Request, idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> dict[str, Any]:
    key = _key(idempotency_key)
    try:
        expected_state = WorkflowState(body.expected_state)
    except ValueError as exc:
        raise APIError(422, "WORKFLOW_STATE_INVALID", "Expected state is invalid.", {}) from exc
    engine = _engine(request)
    machine = engine.machine
    meta = _metadata(request.app.state.workflow_database, run_id)
    try:
        if body.action == "approve_context":
            machine.transition(
                run_id=run_id,
                expected_state=expected_state,
                expected_version=body.expected_version,
                idempotency_key=key,
                actor="user",
                policy_decision="context_disclosure_approved",
                input_artifact_hashes=(meta["request_hash"],),
                output_artifact_hashes=(),
                target_state=WorkflowState.ROUTING,
                safe_message="Approved bounded context disclosure.",
            )
        elif body.action == "decline_context":
            machine.transition(
                run_id=run_id,
                expected_state=expected_state,
                expected_version=body.expected_version,
                idempotency_key=key,
                actor="user",
                policy_decision="context_disclosure_declined",
                input_artifact_hashes=(meta["request_hash"],),
                output_artifact_hashes=(),
                target_state=WorkflowState.CANCELLED,
                safe_message="Context disclosure was declined.",
            )
        elif body.action == "route":
            decision = _record_route(request, run_id, meta, key)
            decision_hash = _hash(_json(decision))
            machine.transition(
                run_id=run_id,
                expected_state=expected_state,
                expected_version=body.expected_version,
                idempotency_key=key,
                actor="forgex.router",
                policy_decision="model_route_selected",
                input_artifact_hashes=(meta["request_hash"],),
                output_artifact_hashes=(decision_hash,),
                target_state=WorkflowState.GENERATING,
                safe_message="Routing decision committed.",
            )
        elif body.action == "generate_review":
            await _generate_review(request, run_id, body, meta)
        elif body.action == "inspect_review":
            _require_current(machine, run_id, expected_state, body.expected_version)
        elif body.action == "approve_review":
            _approve_review(request, run_id, body, meta)
        elif body.action == "approve_apply":
            await _approve_apply(request, run_id, body)
        elif body.action == "build":
            await _run_build(request, run_id, body)
        elif body.action == "complete_without_flash":
            _complete_without_flash(request, run_id, body)
        elif body.action == "approve_flash":
            await _approve_flash(request, run_id, body)
        elif body.action == "monitor":
            _run_monitor(request, run_id, body)
        elif body.action == "skip_monitor":
            _require_current(machine, run_id, expected_state, body.expected_version)
            engine.skip_monitor(run_id=run_id)
        elif body.action == "verify":
            await _run_verify(request, run_id, body)
        elif body.action == "cancel":
            machine.transition(
                run_id=run_id,
                expected_state=expected_state,
                expected_version=body.expected_version,
                idempotency_key=key,
                actor="user",
                policy_decision="user_cancelled",
                input_artifact_hashes=(meta.get("request_hash", _hash(run_id)),),
                output_artifact_hashes=(),
                target_state=WorkflowState.CANCELLED,
                safe_message="Run cancelled.",
            )
        else:
            raise APIError(422, "ACTION_NOT_ALLOWED", "The requested action is not allowed by backend state.", {})
    except APIError:
        raise
    except (InvalidTransition, StaleRunVersion, KeyError, WorkflowEngineError, ApprovalExpired, ApprovalBindingMismatch) as exc:
        raise APIError(409, "WORKFLOW_COMMAND_REJECTED", str(exc), {}) from exc
    except AgentAdapterError as exc:
        _fail_if_live(engine, run_id, f"Adapter failed safely: {exc.code.value}.")
    except HardwareSafetyDenied as exc:
        raise APIError(409, "HARDWARE_SAFETY_DENIED", str(exc), {}) from exc
    request.app.state.workflow_event_hub.notify()
    return _projection(request, run_id)


def _record_route(request: Request, run_id: str, meta: Mapping[str, Any], key: str) -> dict[str, Any]:
    service = request.app.state.model_router_service
    override = meta.get("routing_override") or {}
    if override:
        provider_id, model_id = override["provider_id"], override["model_id"]
        source = "per_run_override"
    else:
        route = service.registry.route_for_task("code_generation")
        provider_id, model_id = route.provider_id, route.model_id
        source = "profile_default"
    profile = _profile_service(request).get_version(meta["profile_id"], int(meta["profile_version"]))
    definition = service.registry.definition(provider_id)
    policy = profile.model_routing_policy
    if policy.local_only and not definition.local:
        raise APIError(403, "ROUTING_POLICY_DENIED", "The selected profile requires a local model.", {})
    if policy.approved_recipients and provider_id not in policy.approved_recipients:
        raise APIError(403, "ROUTING_RECIPIENT_DENIED", "The provider is not an approved context recipient.", {})
    if override and model_id not in {item.model_id for item in service.registry.list_models(provider_id)}:
        raise APIError(422, "MODEL_NOT_DISCOVERED", "The requested model is not in the backend model registry.", {})
    decision = {
        "decision_id": f"{run_id}.route",
        "run_id": run_id,
        "selected_provider_id": provider_id,
        "selected_model_id": model_id,
        "source": source,
        "local": definition.local,
        "consent": "approved",
        "eligible_candidates": [{"provider_id": provider_id, "model_id": model_id}],
        "rejected_candidates": [],
    }
    db = request.app.state.workflow_database
    with db.transaction() as con:
        con.execute(
            "INSERT OR IGNORE INTO routing_policies(policy_id,policy_version,name,payload_json,created_at,updated_at) VALUES('agent-workspace',1,'Agent workspace',?,?,?)",
            (_json({"policy_id": "agent-workspace", "name": "Agent workspace"}), _now(), _now()),
        )
        con.execute(
            "INSERT INTO routing_decisions(decision_id,policy_id,connection_id,status,reason_code,idempotency_key,payload_json,created_at) VALUES(?, 'agent-workspace',NULL,'selected',NULL,?,?,?) ON CONFLICT(decision_id) DO NOTHING",
            (f"{run_id}.route", f"{run_id}.route", _json(_safe_payload(decision)), _now()),
        )
    return decision


async def _generate_review(request: Request, run_id: str, body: RunAction, meta: Mapping[str, Any]) -> None:
    engine = _engine(request)
    _require_current(engine.machine, run_id, WorkflowState(body.expected_state), body.expected_version)
    context = _context_cache(request).get(run_id)
    if context is None:
        raise APIError(409, "AGENT_CONTEXT_EXPIRED", "Runtime-only task context is unavailable. Start a new Agent Workspace run.", {})
    project = await resolve_project_from_request(request, str(meta["project_id"]))
    adapter = _select_adapter(request, body.adapter_id, context)
    try:
        await engine.generate_review(
            run_id=run_id,
            adapter=adapter,
            workspace=Path(project.project_path),
            context=context,
            timeout_seconds=60,
            repair=body.expected_state == WorkflowState.REPAIRING.value,
        )
    except AgentAdapterError:
        raise
    except Exception as exc:
        raise APIError(409, "AGENT_REVIEW_GENERATION_FAILED", "Agent review generation failed safely.", {"failure": type(exc).__name__}) from exc


def _select_adapter(request: Request, adapter_id: str | None, context: ApprovedBoundedContext) -> Any:
    registry = request.app.state.agent_adapter_registry
    if adapter_id:
        try:
            adapter = registry.get(adapter_id)
        except KeyError as exc:
            raise APIError(404, "AGENT_ADAPTER_NOT_FOUND", "Agent adapter was not found.", {}) from exc
        if adapter.readiness() is not AdapterReadiness.READY:
            raise APIError(503, "AGENT_ADAPTER_NOT_READY", "Selected Agent adapter is not ready.", {})
        return adapter
    for descriptor in registry.descriptors():
        if descriptor.adapter_id == "verified_template" and descriptor.readiness is AdapterReadiness.READY:
            return registry.get(descriptor.adapter_id)
    for descriptor in registry.descriptors():
        if descriptor.readiness is AdapterReadiness.READY and descriptor.production_eligible:
            return registry.get(descriptor.adapter_id)
    raise APIError(503, "AGENT_ADAPTER_NOT_READY", "No production-eligible Agent adapter is ready for this run.", {})


def _approve_review(request: Request, run_id: str, body: RunAction, meta: Mapping[str, Any]) -> None:
    engine = _engine(request)
    _require_current(engine.machine, run_id, WorkflowState(body.expected_state), body.expected_version)
    review_id = body.review_id or _latest_artifact_ref(request, run_id, "review")
    if not review_id:
        raise APIError(409, "AGENT_REVIEW_REQUIRED", "No review artifact is available for approval.", {})
    try:
        request.app.state.bridge_diff_service.approve_review(review_id)
    except Exception as exc:
        raise APIError(409, "AGENT_REVIEW_APPROVAL_FAILED", "Review could not be approved safely.", {"failure": type(exc).__name__}) from exc
    engine.submit_review(run_id=run_id, review_id=review_id, expires_at=datetime.now(timezone.utc) + timedelta(minutes=15))


async def _approve_apply(request: Request, run_id: str, body: RunAction) -> None:
    engine = _engine(request)
    _require_current(engine.machine, run_id, WorkflowState(body.expected_state), body.expected_version)
    executors = engine.executors
    if not bool(getattr(executors, "can_apply", False)):
        raise APIError(503, "AGENT_APPLY_EXECUTOR_DISABLED", _apply_disabled_message(executors), {})
    review_id = body.review_id or _latest_artifact_ref(request, run_id, "review")
    if not review_id:
        raise APIError(409, "AGENT_REVIEW_REQUIRED", "No review artifact is available for apply.", {})
    meta = _metadata(request.app.state.workflow_database, run_id)
    project = await _project_path(request, meta)
    engine.approve_apply(run_id=run_id, review_id=review_id, workspace=project)


async def _run_build(request: Request, run_id: str, body: RunAction) -> None:
    engine = _engine(request)
    _require_current(engine.machine, run_id, WorkflowState(body.expected_state), body.expected_version)
    if not bool(getattr(engine.executors, "can_build", False)):
        raise APIError(503, "AGENT_BUILD_EXECUTOR_DISABLED", "Build executor is disabled.", {})
    engine.build(run_id=run_id, workspace=await _project_path(request, _metadata(request.app.state.workflow_database, run_id)))


def _complete_without_flash(request: Request, run_id: str, body: RunAction) -> None:
    engine = _engine(request)
    _require_current(engine.machine, run_id, WorkflowState(body.expected_state), body.expected_version)
    current = engine.machine.get(run_id)
    engine.machine.transition(
        run_id=run_id,
        expected_state=current.state,
        expected_version=current.version,
        idempotency_key=f"complete-without-flash-{current.version}",
        actor="user",
        policy_decision="flash_skipped_by_user",
        input_artifact_hashes=(),
        output_artifact_hashes=(),
        target_state=WorkflowState.VERIFYING,
        safe_message="Flash was skipped by user decision.",
    )


async def _approve_flash(request: Request, run_id: str, body: RunAction) -> None:
    engine = _engine(request)
    _require_current(engine.machine, run_id, WorkflowState(body.expected_state), body.expected_version)
    if not bool(getattr(engine.executors, "can_flash", False)):
        raise APIError(503, "AGENT_FLASH_EXECUTOR_DISABLED", "Flash is disabled until explicit hardware execution is enabled.", {})
    build_hash = body.artifact_hash or _latest_artifact_hash(request, run_id, "build")
    values = (build_hash, body.device_hash, body.port_hash, body.board_hash, body.command_hash)
    if any(not _is_sha256(value) for value in values):
        raise APIError(422, "FLASH_BINDING_REQUIRED", "Flash requires exact artifact, device, port, board, and command hashes.", {})
    binding = FlashApprovalBinding(build_hash, body.device_hash or "", body.port_hash or "", body.board_hash or "", body.command_hash or "")
    engine.approve_flash(run_id=run_id, binding=binding, expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    engine.flash(run_id=run_id, workspace=await _project_path(request, _metadata(request.app.state.workflow_database, run_id)), binding=binding)


def _run_monitor(request: Request, run_id: str, body: RunAction) -> None:
    engine = _engine(request)
    _require_current(engine.machine, run_id, WorkflowState(body.expected_state), body.expected_version)
    port_hash = body.port_hash or _hash("monitor-skipped")
    if not _is_sha256(port_hash):
        raise APIError(422, "MONITOR_PORT_HASH_REQUIRED", "Monitor requires a bounded port hash.", {})
    engine.monitor(run_id=run_id, port_hash=port_hash)


async def _run_verify(request: Request, run_id: str, body: RunAction) -> None:
    engine = _engine(request)
    _require_current(engine.machine, run_id, WorkflowState(body.expected_state), body.expected_version)
    engine.verify(run_id=run_id, workspace=await _project_path(request, _metadata(request.app.state.workflow_database, run_id)))


def _latest_artifact_ref(request: Request, run_id: str, artifact_type: str) -> str | None:
    with request.app.state.workflow_database.connect() as con:
        row = con.execute(
            "SELECT storage_reference FROM artifacts WHERE run_id=? AND artifact_type=? ORDER BY created_at DESC LIMIT 1",
            (run_id, artifact_type),
        ).fetchone()
    return str(row[0]) if row else None


def _latest_artifact_hash(request: Request, run_id: str, artifact_type: str) -> str | None:
    with request.app.state.workflow_database.connect() as con:
        row = con.execute(
            "SELECT content_hash FROM artifacts WHERE run_id=? AND artifact_type=? ORDER BY created_at DESC LIMIT 1",
            (run_id, artifact_type),
        ).fetchone()
    return str(row[0]) if row else None


def _require_current(machine: Any, run_id: str, expected_state: WorkflowState, expected_version: int) -> None:
    current = machine.get(run_id)
    if current.state is not expected_state or current.version != expected_version:
        raise StaleRunVersion("run state or version is stale")


async def _project_path(request: Request, meta: Mapping[str, Any]) -> Path:
    project = await resolve_project_from_request(request, str(meta["project_id"]))
    return Path(project.project_path)


def _fail_if_live(engine: AgentWorkflowEngine, run_id: str, message: str) -> None:
    current = engine.machine.get(run_id)
    if current.state in TERMINAL:
        return
    engine.machine.transition(
        run_id=run_id,
        expected_state=current.state,
        expected_version=current.version,
        idempotency_key=f"safe-failure-{current.version}",
        actor="forgex.workflow",
        policy_decision="agent_failed_safely",
        input_artifact_hashes=(),
        output_artifact_hashes=(_hash(message),),
        target_state=WorkflowState.FAILED,
        safe_message=message,
    )


def _apply_disabled_message(executors: Any) -> str:
    patch_apply = getattr(executors, "patch_apply_service", None)
    if patch_apply is not None and not patch_apply.patch_apply_feature_flag_enabled():
        return PATCH_APPLY_DISABLED_MESSAGE
    if patch_apply is not None and not patch_apply.rollback_restore_feature_flag_enabled():
        return PATCH_APPLY_REQUIRES_RESTORE_MESSAGE
    return "Apply executor is disabled."


def _is_sha256(value: str | None) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _run_async_blocking(factory: Any) -> Any:
    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(factory())
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner, name="forgex-agent-workflow-async", daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")
