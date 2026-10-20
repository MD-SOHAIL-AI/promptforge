from __future__ import annotations

import asyncio
import hashlib
import json
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
from backend.agent_runtime.tool_contracts import RuntimeClassification, ToolCall, ToolName
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


def test_context_artifacts_are_incremental_verified_chains(tmp_path: Path) -> None:
    store = AgentOrchestrationStore(tmp_path / "agent.sqlite3")

    def digest(items: list[dict[str, object]]) -> str:
        return hashlib.sha256(
            json.dumps(
                items,
                ensure_ascii=True,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()

    first_items = [
        {"call_id": "one", "tool": "read_file", "status": "completed"},
        {"call_id": "two", "tool": "grep_search", "status": "completed"},
    ]
    first = store.save_context_artifact(
        "run-1",
        "session-1",
        observation_count=2,
        content_sha256=digest(first_items),
        observation_delta=first_items,
    )
    all_items = [
        *first_items,
        {"call_id": "three", "tool": "read_file", "status": "failed"},
    ]
    second = store.save_context_artifact(
        "run-1",
        "session-1",
        observation_count=3,
        content_sha256=digest(all_items),
        observation_delta=all_items[-1:],
    )

    assert second["parent_artifact_id"] == first["artifact_id"]
    loaded = store.context_artifact("run-1", str(second["artifact_id"]))
    assert loaded["verified"] is True
    assert loaded["observations"] == all_items

    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        store.save_context_artifact(
            "run-1",
            "session-1",
            observation_count=4,
            content_sha256="0" * 64,
            observation_delta=[{"call_id": "four"}],
        )

    with store._transaction() as connection:
        connection.execute(
            "UPDATE agent_context_artifacts SET payload_json='[]' WHERE artifact_id=?",
            (first["artifact_id"],),
        )
    with pytest.raises(ValueError, match="SEGMENT_CORRUPT"):
        store.context_artifact("run-1", str(second["artifact_id"]))


def test_store_renews_only_a_live_unfenced_lease(tmp_path: Path) -> None:
    store = AgentOrchestrationStore(tmp_path / "agent.sqlite3")
    grant = store.acquire_lease("session-1", "run-1")
    renewed = store.renew_lease(grant)
    assert renewed.lease_epoch == grant.lease_epoch
    assert renewed.owner_id == grant.owner_id

    with store._transaction() as connection:
        connection.execute(
            "UPDATE agent_session_leases SET expires_at=? WHERE session_id=?",
            ("2000-01-01T00:00:00Z", "session-1"),
        )
    with pytest.raises(ValueError, match="AGENT_SESSION_LEASE_EXPIRED"):
        store.renew_lease(grant)

    replacement = store.acquire_lease("session-1", "run-2")
    assert replacement.lease_epoch > grant.lease_epoch
    with pytest.raises(ValueError, match="AGENT_SESSION_LEASE_FENCED"):
        store.renew_lease(grant)


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
            assert current.change_set_id is None
            assert current.to_safe_dict()["assistant_message"] == "Compiler rejected main.cpp"
            assert orchestrator.graph(run.run_id)["nodes"][1]["message"] == "Compiler rejected main.cpp"
        finally:
            await orchestrator.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_code", ["TOOLPLAN_SCHEMA_INVALID", "MODEL_OUTPUT_TRUNCATED"])
def test_verified_build_with_invalid_final_response_saves_warning_draft_and_retries(tmp_path: Path, failure_code: str) -> None:
    attempts = 0

    async def executor(request: object) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        stage = Path(getattr(request, "active_workspace_root"))
        if attempts == 1:
            (stage / "main.cpp").write_text("repaired", encoding="utf-8")
            return {
                "success": False, "classification": failure_code,
                "message": "Agent response invalid", "build_verified": True,
                "build_result": {"success": True, "message": "Build passed"},
                "provider_diagnostics": {
                    "outbound_request_count": 3, "physical_request_count": 4,
                    "request_reached_provider": True, "last_turn": 8,
                    "response_mode": "native_tools",
                    "last_failure_code": failure_code,
                    "last_repair_outcome": "failed",
                },
            }
        assert (stage / "main.cpp").read_text(encoding="utf-8") == "repaired"
        return {
            "success": True, "classification": "AGENT_V3_COMPLETED",
            "message": "Revalidated", "build_verified": True,
            "build_result": {"success": True, "message": "Build passed"},
        }

    async def scenario() -> None:
        workspace = tmp_path / "autonomous-workspace"
        workspace.mkdir()
        (workspace / "main.cpp").write_text("before", encoding="utf-8")
        changes = _changes(tmp_path)
        orchestrator = AgentOrchestrator(
            change_service=changes, provider_registry=_providers(tmp_path),
            store=AgentOrchestrationStore(tmp_path / "draft.sqlite3"),
            run_factory=ProductAgentRun, autonomous_executor=executor,
        )
        try:
            first = await orchestrator.start_run(
                project_id="project-autonomous", active_workspace_root=workspace,
                instruction="repair and build firmware", provider_id="verified_template",
                autonomy="build_only", session_id="session-draft",
            )
            for _ in range(200):
                if orchestrator.get_run(first.run_id).status == "completed_with_warning":
                    break
                await asyncio.sleep(0.01)
            failed = orchestrator.get_run(first.run_id)
            assert failed.status == "completed_with_warning"
            assert failed.change_set_id
            assert failed.recoverable is True
            assert failed.build_result["success"] is True
            assert failed.to_safe_dict()["build_verified"] is True
            assert "build verified" in failed.to_safe_dict()["assistant_message"]
            assert any(event["event_type"] == "runtime.completed_with_warning" for event in orchestrator.events(first.run_id))
            assert failed.to_safe_dict()["provider_diagnostics"]["last_turn"] == 8
            draft = changes.get(failed.change_set_id)
            assert draft.status == "draft"
            assert (Path(draft.staging_root) / "main.cpp").is_file()
            assert not changes.staging_root.joinpath(f"workflow-{first.run_id}").exists()
            assert (workspace / "main.cpp").read_text(encoding="utf-8") == "before"
            replacement = await orchestrator.retry(first.run_id)
            for _ in range(200):
                if orchestrator.get_run(replacement.run_id).status in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.01)
            assert attempts == 2
            assert orchestrator.get_run(replacement.run_id).status == "completed"
        finally:
            await orchestrator.close()

    asyncio.run(scenario())


def test_failed_build_saves_unverified_draft_and_retry_error_context(tmp_path: Path) -> None:
    attempts = 0

    async def executor(request: object) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        stage = Path(getattr(request, "active_workspace_root"))
        if attempts == 1:
            (stage / "main.cpp").write_text("#include <DHT.h>\n", encoding="utf-8")
            return {
                "success": False, "classification": "BUILD_ERROR", "message": "Build failed",
                "build_verified": False,
                "build_result": {"success": False, "message": "DHT.h missing",
                    "diagnostics": "fatal error: DHT.h: No such file or directory",
                    "dependency_candidates": [{"query": "header:DHT.h", "package": "adafruit/DHT sensor library", "source": "platformio_registry"}]},
            }
        assert (stage / "main.cpp").read_text(encoding="utf-8") == "#include <DHT.h>\n"
        retry = getattr(request, "active_workspace")["agent_retry_context"]
        assert retry["classification"] == "BUILD_ERROR"
        assert retry["dependency_candidates"][0]["package"] == "adafruit/DHT sensor library"
        return {"success": True, "classification": "AGENT_V3_COMPLETED", "message": "Fixed", "build_verified": True,
                "build_result": {"success": True, "message": "Build passed"}}

    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "main.cpp").write_text("before", encoding="utf-8")
        changes = _changes(tmp_path)
        orchestrator = AgentOrchestrator(
            change_service=changes, provider_registry=_providers(tmp_path),
            store=AgentOrchestrationStore(tmp_path / "failed-build.sqlite3"),
            run_factory=ProductAgentRun, autonomous_executor=executor,
        )
        try:
            first = await orchestrator.start_run(
                project_id="project", active_workspace_root=workspace,
                instruction="build files", provider_id="verified_template",
                autonomy="build_only", session_id="session-failed-build",
            )
            for _ in range(200):
                if orchestrator.get_run(first.run_id).status == "failed":
                    break
                await asyncio.sleep(0.01)
            failed = orchestrator.get_run(first.run_id)
            assert failed.change_set_id and failed.recoverable
            assert failed.stage_statuses["build"] == "failed"
            assert changes.get(failed.change_set_id).status == "draft"
            assert (workspace / "main.cpp").read_text(encoding="utf-8") == "before"
            replacement = await orchestrator.retry(first.run_id)
            for _ in range(200):
                if orchestrator.get_run(replacement.run_id).status in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.01)
            assert attempts == 2
            assert orchestrator.get_run(replacement.run_id).status == "completed"
        finally:
            await orchestrator.close()

    asyncio.run(scenario())


def test_unexpected_autonomous_failure_retains_safe_unverified_files(tmp_path: Path) -> None:
    async def executor(request: object) -> dict[str, object]:
        stage = Path(getattr(request, "active_workspace_root"))
        (stage / "main.cpp").write_text("new source", encoding="utf-8")
        raise RuntimeError("finalization failed")

    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "main.cpp").write_text("old source", encoding="utf-8")
        changes = _changes(tmp_path)
        orchestrator = AgentOrchestrator(
            change_service=changes, provider_registry=_providers(tmp_path),
            store=AgentOrchestrationStore(tmp_path / "unexpected.sqlite3"),
            run_factory=ProductAgentRun, autonomous_executor=executor,
        )
        try:
            started = await orchestrator.start_run(
                project_id="project", active_workspace_root=workspace,
                instruction="edit source", provider_id="verified_template",
                autonomy="build_only", session_id="session-unexpected",
            )
            for _ in range(200):
                if orchestrator.get_run(started.run_id).status == "failed":
                    break
                await asyncio.sleep(0.01)
            failed = orchestrator.get_run(started.run_id)
            assert failed.classification == "AGENT_RUNTIME_FINALIZATION_FAILED"
            assert failed.change_set_id and failed.recoverable
            assert changes.get(failed.change_set_id).status == "draft"
            assert (workspace / "main.cpp").read_text(encoding="utf-8") == "old source"
        finally:
            await orchestrator.close()

    asyncio.run(scenario())


def test_autonomous_context_checkpoint_is_durable_and_preserves_payload(tmp_path: Path) -> None:
    artifact_observations = [
        {
            "call_id": "read-2",
            "tool": "read_file",
            "status": "failed",
            "error": "stale read",
        }
    ]
    observation_digest = hashlib.sha256(
        json.dumps(
            artifact_observations,
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    checkpoint = {
        "checkpoint_version": "forgex.context-checkpoint.v1",
        "compacted_observations": 1,
        "observation_digest": observation_digest,
        "failure_count": 1,
        "recent_failures": [
            {
                "call_id": "read-2",
                "tool": "read_file",
                "status": "failed",
                "error": "stale read",
            }
        ],
        "retrieval": "session_event_search",
    }

    async def checkpointing_executor(request: object) -> dict[str, object]:
        callback = getattr(request, "progress_callback")
        await callback(
            "CONTEXT_CHECKPOINT_CREATED",
            {
                "message": "Older observations compacted",
                "checkpoint": checkpoint,
                "_context_artifact_delta": artifact_observations,
            },
        )
        return {
            "success": False,
            "classification": "EXPECTED_TEST_FAILURE",
            "message": "Stopped after checkpoint verification",
        }

    async def scenario() -> None:
        workspace = tmp_path / "checkpoint-workspace"
        workspace.mkdir()
        orchestrator = AgentOrchestrator(
            change_service=_changes(tmp_path),
            provider_registry=_providers(tmp_path),
            store=AgentOrchestrationStore(tmp_path / "checkpoint.sqlite3"),
            run_factory=ProductAgentRun,
            autonomous_executor=checkpointing_executor,
        )
        try:
            run = await orchestrator.start_run(
                project_id="project-checkpoint",
                active_workspace_root=workspace,
                instruction="exercise durable context compaction",
                provider_id="verified_template",
                autonomy="build_only",
                session_id="session-checkpoint",
            )
            await orchestrator._tasks[run.run_id]
            events = orchestrator.session_events("session-checkpoint")
            persisted = next(
                event
                for event in events
                if event["event_type"] == "context_checkpoint_created"
            )
            assert persisted["run_id"] == run.run_id
            assert persisted["checkpoint"]["artifact_status"] == "ready"
            assert persisted["checkpoint"]["observation_digest"] == observation_digest
            assert persisted["checkpoint"]["recent_failures"][0]["call_id"] == "read-2"
            assert isinstance(persisted["sequence"], int)
            assert "_context_artifact_delta" not in persisted
            artifact = orchestrator.context_artifact(
                run.run_id,
                persisted["checkpoint"]["artifact_id"],
            )
            assert artifact["verified"] is True
            assert artifact["observations"] == artifact_observations
        finally:
            await orchestrator.close()

    asyncio.run(scenario())


def test_orchestrator_heartbeats_session_lease_during_long_workflow(tmp_path: Path) -> None:
    heartbeat_observed = None
    async def slow_executor(request: object) -> dict[str, object]:
        await asyncio.wait_for(heartbeat_observed.wait(), timeout=5)
        return {
            "success": False,
            "classification": "EXPECTED_TEST_FAILURE",
            "message": "Test workflow stopped after the heartbeat window",
        }

    async def scenario() -> None:
        nonlocal heartbeat_observed
        heartbeat_observed = asyncio.Event()
        workspace = tmp_path / "heartbeat-workspace"
        workspace.mkdir()
        store = AgentOrchestrationStore(tmp_path / "heartbeat.sqlite3")
        original_renew = store.renew_lease
        renewals = 0

        def tracking_renew(grant: object, *, ttl_seconds: int = 20):
            nonlocal renewals
            renewals += 1
            if renewals >= 2:
                heartbeat_observed.set()
            return original_renew(grant, ttl_seconds=ttl_seconds)  # type: ignore[arg-type]

        store.renew_lease = tracking_renew  # type: ignore[method-assign]
        orchestrator = AgentOrchestrator(
            change_service=_changes(tmp_path),
            provider_registry=_providers(tmp_path),
            store=store,
            run_factory=ProductAgentRun,
            autonomous_executor=slow_executor,
            lease_heartbeat_interval_seconds=0.01,
        )
        try:
            run = await orchestrator.start_run(
                project_id="project-heartbeat",
                active_workspace_root=workspace,
                instruction="exercise a long provider turn",
                provider_id="verified_template",
                autonomy="build_only",
                session_id="session-heartbeat",
            )
            await orchestrator._tasks[run.run_id]
            assert renewals >= 2
            assert orchestrator.get_run(run.run_id).classification == "EXPECTED_TEST_FAILURE"
            assert any(
                event["event_type"] == "runtime.failed"
                for event in orchestrator.session_events("session-heartbeat")
            )
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
            assert staged.requirement_report["risk_review"]["verdict"] == "request_changes"
            review_node = next(node for node in orchestrator.graph(run.run_id)["nodes"] if node["node_id"] == "review")
            assert review_node["status"] == "failed"
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


def test_tool_policy_engine_rejects_workspace_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "workspace-a"
    other = tmp_path / "workspace-b"
    root.mkdir()
    other.mkdir()
    engine = ToolPolicyEngine(default_ttl_seconds=60)
    grant = engine.issue_grant(
        run_id="run-workspace",
        node_id="implementation",
        workspace_root=root,
        allowed_tools=(ToolName.READ_FILE,),
        authorization_source="explicit_action_request",
    )
    call = ToolCall(ToolName.READ_FILE, {"path": "main.cpp"})
    with pytest.raises(ToolPolicyError, match="capability_workspace_mismatch") as exc:
        engine.validate_call(grant, call, other, policy=product_agent_policy())
    assert exc.value.classification is RuntimeClassification.PATH_UNSAFE
