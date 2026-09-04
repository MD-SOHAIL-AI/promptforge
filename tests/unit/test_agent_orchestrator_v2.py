from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path

import pytest

from backend.agent_runtime.capabilities import ToolPolicyEngine
from backend.agent_runtime.approvals import ApprovalService
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.memory import ProjectMemoryService
from backend.agent_runtime.product_agent_service import ProductAgentRun, ProductAgentService
from backend.agent_runtime.product_provider_registry import ProductProviderRegistry
from backend.agent_runtime.risk import RiskAssessmentService
from backend.agent_runtime.tool_contracts import ToolCall, ToolName
from backend.agent_runtime.tool_policy import ToolPolicyError, product_agent_policy
from backend.changes import ChangeSetService
from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.registry import ProviderRegistry
from backend.model_router.storage import ProviderSettingsStorage


def _changes(tmp_path: Path) -> ChangeSetService:
    return ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "staging")


def _providers(tmp_path: Path) -> ProductProviderRegistry:
    models = ProviderRegistry(ProviderSettingsStorage(tmp_path / "models.json", credential_store=MemoryCredentialStore()))
    return ProductProviderRegistry(model_provider_registry=models, fake_enabled=True)


def test_tool_policy_engine_requires_scoped_live_grant(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    root.mkdir()
    engine = ToolPolicyEngine(default_ttl_seconds=60)
    grant = engine.issue_grant(
        run_id="run-1",
        node_id="implementation",
        workspace_root=root,
        allowed_tools=(ToolName.WRITE_FILE,),
        authorization_source="explicit_edit_request",
        max_calls=1,
    )
    call = ToolCall(ToolName.WRITE_FILE, {"path": "src/main.cpp", "content": "ok\n"})
    engine.validate_call(grant, call, root, policy=product_agent_policy(), consume=True)
    assert engine.remaining_calls(grant) == 0
    with pytest.raises(ToolPolicyError, match="capability_call_limit"):
        engine.validate_call(grant, call, root, policy=product_agent_policy(), consume=True)


def test_tool_policy_engine_rejects_forged_grants_and_gates_services(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    root.mkdir()
    engine = ToolPolicyEngine(default_ttl_seconds=60)
    issued = engine.issue_grant(
        run_id="run-1",
        node_id="build",
        workspace_root=root,
        allowed_services=("build",),
        authorization_source="explicit_action_request",
        max_calls=1,
        allow_build=True,
    )
    forged = replace(issued, allowed_services=frozenset({"build", "hardware"}), allow_hardware=True)
    with pytest.raises(ToolPolicyError, match="capability_not_issued"):
        engine.validate_service(forged, "hardware", root)
    engine.validate_service(issued, "build", root)
    with pytest.raises(ToolPolicyError, match="capability_call_limit"):
        engine.validate_service(issued, "build", root)


def test_risk_review_is_selected_from_final_diff(tmp_path: Path) -> None:
    service = _changes(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    baseline = service.inspect_workspace(workspace)
    stage = service.create_stage("risk")
    (stage / "platformio.ini").write_text("[env:test]\nboard = esp32dev\n", encoding="utf-8")
    change_set = service.create_from_stage(
        provider_id="test",
        workspace_root=workspace,
        stage_root=stage,
        baseline=baseline,
        authorized_paths={"platformio.ini"},
    )
    result = RiskAssessmentService().assess(change_set, explicit_edit_authorized=True)
    assert result.level in {"medium", "high"}
    assert result.reviewer_required is True
    assert result.scoped_auto_apply_allowed is False


def test_store_unifies_chat_and_graph_event_sequence(tmp_path: Path) -> None:
    store = AgentOrchestrationStore(tmp_path / "agent.sqlite3")
    first = store.append_event("session-1", {"event_type": "activity.updated", "activity": "thinking"})
    second = store.append_event("session-1", {"event_type": "node.started", "run_id": "run-1", "node_id": "planning"})
    assert (first["sequence"], second["sequence"]) == (1, 2)
    assert [item["event_type"] for item in store.events("session-1")] == ["activity.updated", "node.started"]


def test_memory_provenance_is_invalidated_when_workspace_revision_changes(tmp_path: Path) -> None:
    store = AgentOrchestrationStore(tmp_path / "agent.sqlite3")
    memory = ProjectMemoryService(store)
    entry = memory.add_verified(
        project_id="project-1",
        kind="verified_build",
        value="Build passed",
        source_type="build_result",
        source_id="run-1",
        source_payload='{"success":true}',
        workspace_revision="workspace:one",
        affected_paths=("src/main.cpp",),
    )
    assert memory.active("project-1", current_workspace_revision="workspace:one")[0]["source_hash"] == entry.source_hash
    assert memory.active("project-1", current_workspace_revision="workspace:two") == ()
    invalidated = store.memories("project-1", include_invalidated=True)[0]
    assert invalidated["status"] == "invalidated"
    assert invalidated["invalidation_reason"] == "workspace_revision_changed"


def test_hardware_approval_is_one_time_and_fingerprint_bound(tmp_path: Path) -> None:
    approvals = ApprovalService(AgentOrchestrationStore(tmp_path / "agent.sqlite3"))
    request = approvals.issue(
        run_id="run-1",
        action_type="flash",
        binding={"artifact_hash": "abc", "port": "COM7"},
        summary="Flash once",
    )
    with pytest.raises(ValueError, match="FINGERPRINT_CHANGED"):
        approvals.consume(
            request.approval_id,
            action_type="flash",
            binding={"artifact_hash": "changed", "port": "COM7"},
        )
    consumed = approvals.consume(
        request.approval_id,
        action_type="flash",
        binding={"artifact_hash": "abc", "port": "COM7"},
    )
    assert consumed["status"] == "consumed"
    with pytest.raises(ValueError, match="ALREADY_CONSUMED"):
        approvals.consume(
            request.approval_id,
            action_type="flash",
            binding={"artifact_hash": "abc", "port": "COM7"},
        )


def test_orchestrator_owns_action_run_and_auto_applies_low_risk_explicit_edit(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        changes = _changes(tmp_path)
        providers = _providers(tmp_path)
        store = AgentOrchestrationStore(tmp_path / "agent.sqlite3")
        orchestrator = AgentOrchestrator(
            change_service=changes,
            provider_registry=providers,
            store=store,
            run_factory=ProductAgentRun,
        )
        service = ProductAgentService(change_service=changes, provider_registry=providers, enabled=True)
        service.attach_orchestrator(orchestrator)
        try:
            run = await service.start_run(
                project_id="project-1",
                active_workspace_root=workspace,
                instruction="create a safe test file",
                provider_id="fake_planner",
                session_id="session-1",
                explicit_edit_authorized=True,
            )
            for _ in range(200):
                current = service.get_run(run.run_id)
                if current.status in {"completed", "failed", "blocked", "cancelled", "timed_out"}:
                    break
                await asyncio.sleep(0.01)
            current = service.get_run(run.run_id)
            assert current.status == "completed"
            assert (workspace / "FORGEX_AGENT_RUNTIME_SMOKE.txt").is_file()
            assert changes.get(current.change_set_id).status == "applied"
            graph = orchestrator.graph(run.run_id)
            assert graph["nodes"][0]["role"] == "supervisor_planner"
            assert any(item["event_type"] == "node.started" for item in orchestrator.session_events("session-1"))

            restored = AgentOrchestrator(
                change_service=changes,
                provider_registry=providers,
                store=AgentOrchestrationStore(tmp_path / "agent.sqlite3"),
                run_factory=ProductAgentRun,
            )
            assert restored.get_run(run.run_id).status == "completed"
            assert len(restored.graph(run.run_id)["nodes"]) == 6
        finally:
            await service.close()

    asyncio.run(scenario())


def test_autonomous_progress_updates_stages_and_preserves_failure_message(tmp_path: Path) -> None:
    async def failing_executor(request: object) -> dict[str, object]:
        callback = getattr(request, "progress_callback")
        await callback("TASK_CREATED", {"message": "Task created"})
        await callback("PLAN_GENERATED", {"message": "Plan ready"})
        await callback("GENERATION_STARTED", {"message": "Generating files"})
        await callback("GENERATION_COMPLETED", {"message": "Files generated"})
        await callback("CODE_GENERATION_COMPLETED", {"message": "Firmware project validated"})
        await callback("BUILD_STARTED", {"message": "Building firmware"})
        await callback("BUILD_FAILED", {"message": "Compiler rejected main.cpp"})
        return {
            "success": False,
            "classification": "BUILD_ERROR",
            "message": "Compiler rejected main.cpp",
            "actual_provider_id": "openrouter",
            "model_id": "openrouter/free",
            "fallback_reason": "API_TOOLPLAN_INVALID",
            "provider_diagnostics": {
                "model_id": "openrouter/free",
                "outbound_request_count": 3,
                "request_reached_provider": True,
                "http_status": 429,
                "provider_request_id": "req-safe",
            },
        }

    async def scenario() -> None:
        workspace = tmp_path / "autonomous-workspace"
        workspace.mkdir()
        changes = _changes(tmp_path)
        providers = _providers(tmp_path)
        orchestrator = AgentOrchestrator(
            change_service=changes,
            provider_registry=providers,
            store=AgentOrchestrationStore(tmp_path / "autonomous.sqlite3"),
            run_factory=ProductAgentRun,
            autonomous_executor=failing_executor,
        )
        try:
            run = await orchestrator.start_run(
                project_id="project-autonomous",
                active_workspace_root=workspace,
                instruction="generate and build firmware",
                provider_id="verified_template",
                autonomy="build_only",
                session_id="session-autonomous",
            )
            for _ in range(200):
                if orchestrator.get_run(run.run_id).status == "failed":
                    break
                await asyncio.sleep(0.01)
            current = orchestrator.get_run(run.run_id)
            assert current.stage_statuses["planning"] == "completed"
            assert current.stage_statuses["generation"] == "completed"
            assert current.stage_statuses["build"] == "failed"
            assert current.classification == "BUILD_ERROR"
            assert current.actual_provider_id == "openrouter"
            assert current.model_id == "openrouter/free"
            assert current.fallback_reason == "API_TOOLPLAN_INVALID"
            assert current.outbound_request_count == 3
            assert current.request_reached_provider is True
            assert current.http_status == 429
            assert current.provider_request_id == "req-safe"
            assert current.to_safe_dict()["assistant_message"] == "Compiler rejected main.cpp"
            assert orchestrator.graph(run.run_id)["nodes"][1]["message"] == "Compiler rejected main.cpp"
        finally:
            await orchestrator.close()

    asyncio.run(scenario())


def test_validation_decision_blocks_run_and_skips_unreachable_nodes(tmp_path: Path) -> None:
    async def blocked_executor(request: object) -> dict[str, object]:
        callback = getattr(request, "progress_callback")
        await callback("TASK_CREATED", {"message": "Task created"})
        await callback("PLAN_GENERATED", {"message": "Plan ready"})
        await callback("GENERATION_STARTED", {"message": "Generating"})
        await callback("PROJECT_VALIDATION_STARTED", {"message": "Validating"})
        await callback("PROJECT_VALIDATION_FAILED", {"message": "WiFi is unsupported by the selected board"})
        return {
            "success": False,
            "blocked": True,
            "classification": "HARDWARE_CONSTRAINT_DECISION_REQUIRED",
            "message": "WiFi is unsupported by the selected board",
            "pending_decision": True,
            "suggested_alternatives": ["ESP32 DevKit V1"],
            "validation_report": {"valid": False, "requires_user_decision": True},
        }

    async def scenario() -> None:
        workspace = tmp_path / "blocked-workspace"
        workspace.mkdir()
        orchestrator = AgentOrchestrator(
            change_service=_changes(tmp_path),
            provider_registry=_providers(tmp_path),
            store=AgentOrchestrationStore(tmp_path / "blocked.sqlite3"),
            run_factory=ProductAgentRun,
            autonomous_executor=blocked_executor,
        )
        try:
            run = await orchestrator.start_run(
                project_id="project-blocked",
                active_workspace_root=workspace,
                instruction="generate WiFi firmware",
                provider_id="verified_template",
                autonomy="build_only",
                session_id="session-blocked",
            )
            for _ in range(200):
                if orchestrator.get_run(run.run_id).status == "blocked":
                    break
                await asyncio.sleep(0.01)
            current = orchestrator.get_run(run.run_id)
            graph = orchestrator.graph(run.run_id)["nodes"]
            assert current.status == "blocked"
            assert current.pending_decision is True
            assert current.suggested_alternatives == ["ESP32 DevKit V1"]
            assert graph[1]["status"] == "completed"
            assert graph[2]["status"] == "failed"
            assert all(node["status"] == "skipped" for node in graph[3:])
            assert "ESP32 DevKit V1" in current.to_safe_dict()["assistant_message"]
        finally:
            await orchestrator.close()

    asyncio.run(scenario())


def test_explicit_approval_applies_successfully_built_staged_project(tmp_path: Path) -> None:
    async def successful_executor(request: object) -> dict[str, object]:
        stage = Path(getattr(request, "active_workspace_root"))
        (stage / "src").mkdir(parents=True, exist_ok=True)
        (stage / "platformio.ini").write_text(
            "[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n",
            encoding="utf-8",
        )
        (stage / "src/main.cpp").write_text(
            "#include <Arduino.h>\nconst int LED_PIN = 2;\nvoid setup(){}\nvoid loop(){}\n",
            encoding="utf-8",
        )
        callback = getattr(request, "progress_callback")
        await callback("PROJECT_VALIDATION_STARTED", {"message": "Validating"})
        await callback("PROJECT_VALIDATION_COMPLETED", {"message": "Validated"})
        await callback("BUILD_STARTED", {"message": "Building"})
        await callback("BUILD_COMPLETED", {"message": "Build passed"})
        return {"success": True, "classification": "COMPLETED", "message": "Build passed"}

    async def reviewer(_: object) -> dict[str, object]:
        return {"verdict": "request_changes", "summary": "Manual approval required"}

    async def scenario() -> None:
        workspace = tmp_path / "approval-workspace"
        workspace.mkdir()
        orchestrator = AgentOrchestrator(
            change_service=_changes(tmp_path),
            provider_registry=_providers(tmp_path),
            store=AgentOrchestrationStore(tmp_path / "approval.sqlite3"),
            run_factory=ProductAgentRun,
            autonomous_executor=successful_executor,
            reviewer=reviewer,  # type: ignore[arg-type]
        )
        try:
            run = await orchestrator.start_run(
                project_id="project-approval",
                active_workspace_root=workspace,
                instruction="Generate the complete ESP32 project directly",
                provider_id="verified_template",
                autonomy="build_only",
                session_id="session-approval",
                explicit_edit_authorized=True,
            )
            for _ in range(200):
                if orchestrator.get_run(run.run_id).status == "completed":
                    break
                await asyncio.sleep(0.01)

            staged = orchestrator.get_run(run.run_id)
            assert staged.classification == "CHANGESET_REVIEW_REQUIRED"
            assert staged.active_workspace_unchanged is True
            assert not (workspace / "platformio.ini").exists()

            applied = orchestrator.apply_pending_changes(run.run_id)

            assert applied.classification == "CHANGESET_APPLIED"
            assert applied.active_workspace_unchanged is False
            assert (workspace / "platformio.ini").is_file()
            assert (workspace / "src/main.cpp").is_file()
            graph = orchestrator.graph(run.run_id)["nodes"]
            assert next(node for node in graph if node["node_id"] == "apply")["status"] == "completed"
        finally:
            await orchestrator.close()

    asyncio.run(scenario())
