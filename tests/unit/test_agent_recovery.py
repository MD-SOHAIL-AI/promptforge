from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.agent_runtime.approvals import ApprovalService
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.product_agent_service import ProductAgentRun
from backend.agent_runtime.product_provider_registry import ProductProviderRegistry
from backend.agent_runtime.session_store import AgentSessionStore
from backend.changes import ChangeSetService
from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.registry import ProviderRegistry
from backend.model_router.storage import ProviderSettingsStorage


def _providers(tmp_path: Path) -> ProductProviderRegistry:
    registry = ProviderRegistry(
        ProviderSettingsStorage(
            tmp_path / "models.json",
            credential_store=MemoryCredentialStore(),
        )
    )
    return ProductProviderRegistry(model_provider_registry=registry, fake_enabled=True)


def test_session_store_migrates_legacy_json_once_into_sqlite(tmp_path: Path) -> None:
    legacy = tmp_path / "agent-sessions.json"
    legacy.write_text(
        json.dumps(
            {
                "agent-session-old": {
                    "session_id": "agent-session-old",
                    "project_id": "project-1",
                    "title": "Existing conversation",
                    "status": "idle",
                    "messages": [],
                }
            }
        ),
        encoding="utf-8",
    )

    first = AgentSessionStore(tmp_path / "agent.sqlite3", legacy_json_path=legacy)
    assert first.get("agent-session-old").title == "Existing conversation"
    assert not legacy.exists()
    assert legacy.with_name("agent-sessions.json.migrated.bak").is_file()

    second = AgentSessionStore(tmp_path / "agent.sqlite3", legacy_json_path=legacy)
    assert [item.session_id for item in second.list()] == ["agent-session-old"]


def test_restart_invalidates_approval_and_retry_is_idempotent(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        store = AgentOrchestrationStore(tmp_path / "agent.sqlite3")
        run = ProductAgentRun(
            "agent-run-interrupted",
            "project-1",
            "fake_planner",
            status="awaiting_flash_confirmation",
            autonomy="staged_changes",
        )
        approvals = ApprovalService(store)
        approval = approvals.issue(
            run_id=run.run_id,
            action_type="flash",
            binding={"artifact_hash": "abc", "port": "COM7"},
            summary="Flash once",
        )
        run.approval_id = approval.approval_id
        payload = run.to_safe_dict()
        payload.update(
            {
                "session_id": "agent-session-1",
                "_recovery_context": {
                    "workspace_root": str(workspace),
                    "instruction": "Inspect the project without changing it",
                    "fallback_provider_id": None,
                    "explicit_edit_authorized": False,
                    "active_workspace": {},
                },
            }
        )
        store.save_run(run.run_id, "agent-session-1", run.project_id, run.status, payload)
        settled = store.append_event(
            "agent-session-1",
            {
                "event_type": "turn.settled",
                "run_id": run.run_id,
                "turn": 2,
                "step": 2,
                "attempt_id": "attempt-2",
                "outcome": "continue",
            },
        )
        checkpoint = store.save_run_checkpoint(
            run.run_id,
            "agent-session-1",
            turn=2,
            session_seq=int(settled["sequence"]),
            payload={"tool_count": 3},
        )
        assert checkpoint["effect_replay_allowed"] is False
        assert checkpoint["resume_mode"] == "replacement_run_only"

        orchestrator = AgentOrchestrator(
            change_service=ChangeSetService(
                state_root=tmp_path / "changes",
                staging_root=tmp_path / "staging",
            ),
            provider_registry=_providers(tmp_path),
            store=store,
            run_factory=ProductAgentRun,
        )
        try:
            interrupted = orchestrator.get_run(run.run_id)
            assert interrupted.status == "failed"
            assert interrupted.classification == "AGENT_RUNTIME_INTERRUPTED"
            assert interrupted.recoverable is True
            assert interrupted.recovery_actions == ["retry"]
            restored_checkpoint = store.latest_run_checkpoint(run.run_id)
            assert restored_checkpoint is not None
            assert restored_checkpoint["turn"] == 2
            interrupted_events = store.events(
                "agent-session-1", run_id=run.run_id, limit=100
            )
            restart_event = next(
                item for item in interrupted_events
                if item["event_type"] == "runtime.interrupted"
            )
            assert restart_event["checkpoint_turn"] == 2
            assert restart_event["recovery_mode"] == "replacement_run_only"
            assert restart_event["effect_replay_allowed"] is False
            assert interrupted.approval_id is None
            with pytest.raises(ValueError, match="ALREADY_CONSUMED"):
                approvals.consume(
                    approval.approval_id,
                    action_type="flash",
                    binding={"artifact_hash": "abc", "port": "COM7"},
                )

            replacement = await orchestrator.retry(run.run_id)
            duplicate = await orchestrator.retry(run.run_id)
            assert duplicate.run_id == replacement.run_id
            assert replacement.retry_of_run_id == run.run_id
            assert interrupted.recovered_by_run_id == replacement.run_id
        finally:
            await orchestrator.close()

    asyncio.run(scenario())


def test_checkpoint_rejects_non_settled_event_boundary(tmp_path: Path) -> None:
    store = AgentOrchestrationStore(tmp_path / "agent.sqlite3")
    event = store.append_event(
        "session-1",
        {"event_type": "tool.execution.started", "run_id": "run-1"},
    )
    with pytest.raises(ValueError, match="AGENT_CHECKPOINT_BOUNDARY_INVALID"):
        store.save_run_checkpoint(
            "run-1",
            "session-1",
            turn=1,
            session_seq=int(event["sequence"]),
            payload={},
        )


def test_model_unavailable_pause_survives_restart_and_can_retry(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        store = AgentOrchestrationStore(tmp_path / "agent.sqlite3")
        run = ProductAgentRun(
            "agent-run-paused",
            "project-1",
            "fake_planner",
            status="paused_model_unavailable",
            classification="PROVIDER_UNAVAILABLE",
            recoverable=True,
            recovery_actions=["retry", "change_model"],
            outbound_request_count=3,
            physical_request_count=5,
            request_reached_provider=True,
            http_status=503,
        )
        payload = run.to_safe_dict()
        payload.update(
            {
                "session_id": "agent-session-paused",
                "_recovery_context": {
                    "workspace_root": str(workspace),
                    "instruction": "Inspect the project without changing it",
                    "explicit_edit_authorized": False,
                    "active_workspace": {},
                },
            }
        )
        store.save_run(run.run_id, "agent-session-paused", run.project_id, run.status, payload)
        orchestrator = AgentOrchestrator(
            change_service=ChangeSetService(
                state_root=tmp_path / "changes-paused",
                staging_root=tmp_path / "staging-paused",
            ),
            provider_registry=_providers(tmp_path),
            store=store,
            run_factory=ProductAgentRun,
        )
        try:
            restored = orchestrator.get_run(run.run_id)
            assert restored.status == "paused_model_unavailable"
            assert restored.classification == "PROVIDER_UNAVAILABLE"
            assert restored.outbound_request_count == 3
            assert restored.physical_request_count == 5
            assert restored.http_status == 503
            replacement = await orchestrator.retry(run.run_id)
            assert replacement.retry_of_run_id == run.run_id
            assert restored.recovered_by_run_id == replacement.run_id
        finally:
            await orchestrator.close()

    asyncio.run(scenario())
