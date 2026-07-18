import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.agent_runtime.agent_adapter import ApprovedBoundedContext
from backend.agent_runtime.workflow_engine import (
    ArtifactPersistenceFailed,
    FlashApprovalBinding,
    HardwareSafetyDenied,
    WorkflowEngineError,
)
from backend.state.domain_persistence import DomainDatabase, DomainRepository, StaleRunVersion
from backend.state.durable_workflow import AuthoritativeArtifact, DurableWorkflowStateMachine, WorkflowState
from tests.unit.test_agent_workflow_engine import BOARD, COMMAND, DEVICE, H, PORT, begin, setup


def _artifact(run_id: str = "run", *, digest: str = H, artifact_id: str = "artifact-1") -> AuthoritativeArtifact:
    return AuthoritativeArtifact(artifact_id, run_id, "review", digest, "review-1")


def _artifact_command(*, version: int = 0, key: str = "persist-review") -> dict[str, object]:
    return {
        "run_id": "run",
        "expected_state": WorkflowState.DRAFT,
        "expected_version": version,
        "idempotency_key": key,
        "actor": "forgex.workflow",
        "policy_decision": "proposal-validated",
        "input_artifact_hashes": (H,),
        "output_artifact_hashes": (H,),
        "target_state": WorkflowState.PREPARING_CONTEXT,
        "safe_message": "Review metadata persisted.",
    }


def _machine(tmp_path):
    machine = DurableWorkflowStateMachine(DomainDatabase(tmp_path / "atomic.db"))
    machine.create(run_id="run", idempotency_key="create-run", actor="user", policy_decision="accepted", input_artifact_hashes=(H,))
    return machine


def _counts(database: DomainDatabase) -> tuple[int, int, int, int, int]:
    with database.connect() as db:
        return tuple(
            db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("artifacts", "workflow_events", "workflow_event_outbox", "conversation_messages", "workflow_commands")
        )


def _event_count(database: DomainDatabase, target_state: str, *, outbox: bool = False) -> int:
    table = "workflow_event_outbox" if outbox else "workflow_events"
    with database.connect() as db:
        return db.execute(
            f"SELECT count(*) FROM {table} WHERE json_extract(payload_json,'$.to_state')=?",
            (target_state,),
        ).fetchone()[0]


def test_artifact_save_exception_rolls_back_state_event_outbox_and_command(tmp_path, monkeypatch):
    machine = _machine(tmp_path)

    def fail_save(db, artifact, now):
        del db, artifact, now
        raise RuntimeError("token=raw-secret C:/internal/database.sqlite")

    monkeypatch.setattr(machine, "_persist_authoritative_artifact", fail_save)
    with pytest.raises(ArtifactPersistenceFailed) as caught:
        machine.transition_with_artifact(artifact=_artifact(), **_artifact_command())

    assert caught.value.code == "ARTIFACT_PERSISTENCE_FAILED"
    assert str(caught.value) == "Authoritative workflow artifact could not be persisted."
    assert "secret" not in str(caught.value).casefold()
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert machine.get("run").state is WorkflowState.DRAFT
    assert _counts(machine.database) == (0, 0, 0, 0, 0)


def test_artifact_insert_is_rolled_back_when_transaction_fails_after_insert(tmp_path, monkeypatch):
    machine = _machine(tmp_path)
    original = machine._persist_authoritative_artifact

    def insert_then_fail(db, artifact, now):
        original(db, artifact, now)
        raise RuntimeError("injected failure after artifact insert")

    monkeypatch.setattr(machine, "_persist_authoritative_artifact", insert_then_fail)
    with pytest.raises(ArtifactPersistenceFailed):
        machine.transition_with_artifact(artifact=_artifact(), **_artifact_command())

    assert machine.get("run").state is WorkflowState.DRAFT
    assert _counts(machine.database) == (0, 0, 0, 0, 0)

def test_sqlite_constraint_conflict_fails_closed_and_preserves_cause(tmp_path):
    machine = _machine(tmp_path)
    DomainRepository(machine.database).insert(
        "artifacts",
        {
            "artifact_id": "artifact-1",
            "run_id": "run",
            "step_id": None,
            "artifact_type": "review",
            "content_hash": "b" * 64,
            "size_bytes": 0,
            "storage_reference": "different-review",
        },
    )

    with pytest.raises(ArtifactPersistenceFailed) as caught:
        machine.transition_with_artifact(artifact=_artifact(), **_artifact_command())

    assert caught.value.__cause__ is not None
    assert machine.get("run").state is WorkflowState.DRAFT
    assert _counts(machine.database) == (1, 0, 0, 0, 0)


def test_stale_version_never_inserts_artifact_or_transition_records(tmp_path):
    machine = _machine(tmp_path)
    with pytest.raises(StaleRunVersion):
        machine.transition_with_artifact(artifact=_artifact(), **_artifact_command(version=1))
    assert machine.get("run").state is WorkflowState.DRAFT
    assert _counts(machine.database) == (0, 0, 0, 0, 0)


def test_duplicate_artifact_command_is_idempotent_and_hash_is_unchanged(tmp_path):
    machine = _machine(tmp_path)
    first = machine.transition_with_artifact(artifact=_artifact(), **_artifact_command())
    duplicate = machine.transition_with_artifact(artifact=_artifact(), **_artifact_command())

    assert first.version == duplicate.version == 1
    assert duplicate.duplicate is True
    assert _counts(machine.database) == (1, 1, 1, 1, 1)
    with machine.database.connect() as db:
        row = db.execute("SELECT content_hash,storage_reference FROM artifacts").fetchone()
    assert tuple(row) == (H, "review-1")


def test_duplicate_command_never_recreates_a_missing_committed_artifact(tmp_path):
    machine = _machine(tmp_path)
    machine.transition_with_artifact(artifact=_artifact(), **_artifact_command())
    with machine.database.transaction() as db:
        db.execute("DELETE FROM artifacts WHERE artifact_id='artifact-1'")

    with pytest.raises(ArtifactPersistenceFailed):
        machine.transition_with_artifact(artifact=_artifact(), **_artifact_command())

    with machine.database.connect() as db:
        assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM workflow_events").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM workflow_commands").fetchone()[0] == 1

def test_retry_after_transient_artifact_failure_commits_exactly_once(tmp_path, monkeypatch):
    machine = _machine(tmp_path)
    original = machine._persist_authoritative_artifact
    attempts = 0

    def flaky_save(db, artifact, now):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("database is temporarily locked")
        return original(db, artifact, now)

    monkeypatch.setattr(machine, "_persist_authoritative_artifact", flaky_save)
    with pytest.raises(ArtifactPersistenceFailed):
        machine.transition_with_artifact(artifact=_artifact(), **_artifact_command())
    result = machine.transition_with_artifact(artifact=_artifact(), **_artifact_command())

    assert result.state is WorkflowState.PREPARING_CONTEXT
    assert attempts == 2
    assert _counts(machine.database) == (1, 1, 1, 1, 1)


def test_artifact_for_another_run_is_rejected_before_transition(tmp_path):
    machine = _machine(tmp_path)
    with pytest.raises(ArtifactPersistenceFailed) as caught:
        machine.transition_with_artifact(artifact=_artifact("other-run"), **_artifact_command())
    assert caught.value.__cause__ is not None
    assert machine.get("run").state is WorkflowState.DRAFT
    assert _counts(machine.database) == (0, 0, 0, 0, 0)


async def _review_ready(engine, workspace, adapter):
    begin(engine)
    return await engine.generate_review(
        run_id="run",
        adapter=adapter,
        workspace=workspace,
        context=ApprovedBoundedContext("safe"),
        timeout_seconds=2,
    )


async def _awaiting_build(engine, workspace, adapter):
    review = await _review_ready(engine, workspace, adapter)
    engine.submit_review(run_id="run", review_id=review.review_id, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    engine.approve_apply(run_id="run", review_id=review.review_id, workspace=workspace)
    return review


@pytest.mark.asyncio
async def test_review_persistence_failure_never_exposes_review_or_approval(tmp_path, monkeypatch):
    database, engine, executors, workspace, adapter = setup(tmp_path)
    begin(engine)

    def fail_save(db, artifact, now):
        del db, artifact, now
        raise RuntimeError("raw provider response and internal path")

    monkeypatch.setattr(engine.machine, "_persist_authoritative_artifact", fail_save)
    with pytest.raises(ArtifactPersistenceFailed) as caught:
        await engine.generate_review(
            run_id="run",
            adapter=adapter,
            workspace=workspace,
            context=ApprovedBoundedContext("safe"),
            timeout_seconds=2,
        )

    assert caught.value.code == "ARTIFACT_PERSISTENCE_FAILED"
    assert engine.machine.get("run").state is WorkflowState.VALIDATING
    assert engine.report("run").review_ids == ()
    assert _event_count(database, "awaiting_review") == 0
    assert _event_count(database, "awaiting_review", outbox=True) == 0
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    with pytest.raises(WorkflowEngineError):
        engine.submit_review(run_id="run", review_id="unpersisted-review", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    assert executors.calls == []


@pytest.mark.asyncio
async def test_missing_persisted_review_blocks_approval_and_apply(tmp_path):
    database, engine, executors, workspace, adapter = setup(tmp_path)
    review = await _review_ready(engine, workspace, adapter)
    with database.transaction() as db:
        db.execute("DELETE FROM artifacts WHERE run_id='run' AND artifact_type='review'")

    with pytest.raises(ArtifactPersistenceFailed):
        engine.submit_review(run_id="run", review_id=review.review_id, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    assert "preflight_rollback_apply" not in executors.calls


@pytest.mark.asyncio
async def test_deleted_review_after_approval_blocks_apply_without_consuming_approval(tmp_path):
    database, engine, executors, workspace, adapter = setup(tmp_path)
    review = await _review_ready(engine, workspace, adapter)
    engine.submit_review(run_id="run", review_id=review.review_id, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    with database.transaction() as db:
        db.execute("DELETE FROM artifacts WHERE run_id='run' AND artifact_type='review'")

    with pytest.raises(ArtifactPersistenceFailed):
        engine.approve_apply(run_id="run", review_id=review.review_id, workspace=workspace)
    with database.connect() as db:
        assert db.execute("SELECT status FROM approvals WHERE action_code='apply'").fetchone()[0] == "pending"
    assert "preflight_rollback_apply" not in executors.calls


@pytest.mark.asyncio
async def test_applied_artifact_persistence_failure_blocks_build_and_success_delivery(tmp_path, monkeypatch):
    database, engine, executors, workspace, adapter = setup(tmp_path)
    review = await _review_ready(engine, workspace, adapter)
    engine.submit_review(run_id="run", review_id=review.review_id, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    original = engine.machine._persist_authoritative_artifact

    def fail_applied(db, artifact, now):
        if artifact.artifact_type == "applied":
            raise sqlite3.IntegrityError("injected apply artifact failure")
        return original(db, artifact, now)

    monkeypatch.setattr(engine.machine, "_persist_authoritative_artifact", fail_applied)
    with pytest.raises(ArtifactPersistenceFailed):
        engine.approve_apply(run_id="run", review_id=review.review_id, workspace=workspace)

    assert engine.machine.get("run").state is WorkflowState.APPLYING
    assert _event_count(database, "awaiting_build") == 0
    assert _event_count(database, "awaiting_build", outbox=True) == 0
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM artifacts WHERE artifact_type='applied'").fetchone()[0] == 0
    with pytest.raises(ArtifactPersistenceFailed):
        engine.approve_apply(run_id="run", review_id=review.review_id, workspace=workspace)
    with pytest.raises(WorkflowEngineError):
        engine.build(run_id="run", workspace=workspace)
    assert executors.calls == ["preflight_rollback_apply"]


@pytest.mark.asyncio
async def test_missing_applied_artifact_blocks_build_before_executor(tmp_path):
    database, engine, executors, workspace, adapter = setup(tmp_path)
    await _awaiting_build(engine, workspace, adapter)
    with database.transaction() as db:
        db.execute("DELETE FROM artifacts WHERE run_id='run' AND artifact_type='applied'")

    with pytest.raises(ArtifactPersistenceFailed):
        engine.build(run_id="run", workspace=workspace)
    assert executors.calls == ["preflight_rollback_apply"]


@pytest.mark.asyncio
async def test_build_artifact_failure_blocks_flash_approval_and_success_delivery(tmp_path, monkeypatch):
    database, engine, executors, workspace, adapter = setup(tmp_path)
    await _awaiting_build(engine, workspace, adapter)
    original = engine.machine._persist_authoritative_artifact

    def fail_build(db, artifact, now):
        if artifact.artifact_type == "build":
            raise sqlite3.IntegrityError("injected build artifact failure")
        return original(db, artifact, now)

    monkeypatch.setattr(engine.machine, "_persist_authoritative_artifact", fail_build)
    with pytest.raises(ArtifactPersistenceFailed):
        engine.build(run_id="run", workspace=workspace)

    assert engine.machine.get("run").state is WorkflowState.BUILDING
    assert _event_count(database, "awaiting_flash_approval") == 0
    assert _event_count(database, "awaiting_flash_approval", outbox=True) == 0
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM artifacts WHERE artifact_type='build'").fetchone()[0] == 0
    binding = FlashApprovalBinding("2" * 64, DEVICE, PORT, BOARD, COMMAND)
    with pytest.raises(WorkflowEngineError):
        engine.approve_flash(run_id="run", binding=binding, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM approvals WHERE action_code='flash'").fetchone()[0] == 0
    with pytest.raises(ArtifactPersistenceFailed):
        engine.build(run_id="run", workspace=workspace)
    assert executors.calls.count("build") == 1
    assert "flash" not in executors.calls


@pytest.mark.asyncio
async def test_removed_build_artifact_cannot_receive_flash_approval(tmp_path):
    database, engine, executors, workspace, adapter = setup(tmp_path)
    await _awaiting_build(engine, workspace, adapter)
    build = engine.build(run_id="run", workspace=workspace)
    binding = FlashApprovalBinding(build.artifact_hash, DEVICE, PORT, BOARD, COMMAND)
    with database.transaction() as db:
        db.execute("DELETE FROM artifacts WHERE run_id='run' AND artifact_type='build'")

    with pytest.raises(ArtifactPersistenceFailed):
        engine.approve_flash(run_id="run", binding=binding, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM approvals WHERE action_code='flash'").fetchone()[0] == 0
    assert "flash" not in executors.calls

@pytest.mark.asyncio
async def test_unpersisted_or_removed_build_cannot_be_approved_or_flashed(tmp_path):
    database, engine, executors, workspace, adapter = setup(tmp_path)
    await _awaiting_build(engine, workspace, adapter)
    build = engine.build(run_id="run", workspace=workspace)
    binding = FlashApprovalBinding(build.artifact_hash, DEVICE, PORT, BOARD, COMMAND)
    engine.approve_flash(run_id="run", binding=binding, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    with database.transaction() as db:
        db.execute("DELETE FROM artifacts WHERE run_id='run' AND artifact_type='build'")

    with pytest.raises(HardwareSafetyDenied):
        engine.flash(run_id="run", workspace=workspace, binding=binding)
    with database.connect() as db:
        assert db.execute("SELECT status FROM approvals WHERE action_code='flash'").fetchone()[0] == "pending"
    assert "flash" not in executors.calls


def test_flash_binding_still_covers_artifact_device_port_board_and_command():
    original = FlashApprovalBinding(H, DEVICE, PORT, BOARD, COMMAND)
    digest = original.digest()
    assert len(digest) == 64
    for field, replacement in (
        ("artifact_hash", "f" * 64),
        ("device_hash", "f" * 64),
        ("port_hash", "f" * 64),
        ("board_hash", "f" * 64),
        ("command_hash", "f" * 64),
    ):
        values = {name: getattr(original, name) for name in original.__slots__}
        values[field] = replacement
        assert FlashApprovalBinding(**values).digest() != digest