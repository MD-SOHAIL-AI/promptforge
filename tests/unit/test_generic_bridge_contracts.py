from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.bridges.generic import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    BridgeArtifactReference,
    BridgeArtifactType,
    BridgeCancellationDisposition,
    BridgeCancellationResult,
    BridgeCapabilities,
    BridgeDetectionResult,
    BridgeDomainError,
    BridgeErrorCode,
    BridgeEventType,
    BridgeProvider,
    BridgeProviderRegistry,
    BridgeRunContext,
    BridgeRunEvent,
    BridgeRunRecord,
    BridgeRunRequest,
    BridgeRunResult,
    BridgeRunStateMachine,
    BridgeRunStatus,
    BridgeRunStore,
    BridgeSandboxContext,
    BridgeValidationResult,
    normalize_bridge_error,
)
from backend.bridges.generic.validation import MAX_SAFE_MESSAGE_LENGTH


NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
INSTRUCTION = "Refactor the parser without changing its public API."


class FakeProvider:
    provider_id = "codex_bridge"
    environment_allowlist = frozenset({"PATH", "TEMP"})

    def capabilities(self) -> BridgeCapabilities:
        return BridgeCapabilities(provider_id=self.provider_id, display_name="Contract test provider")

    async def detect(self) -> BridgeDetectionResult:
        return BridgeDetectionResult(provider_id=self.provider_id, available=False)

    async def validate(self, request: BridgeRunRequest) -> BridgeValidationResult:
        return BridgeValidationResult(valid=request.provider_id == self.provider_id)

    async def start(self, context: BridgeRunContext) -> BridgeRunResult:
        assert context.sandbox.containment_verified
        return BridgeRunResult(succeeded=False, failure_code="provider_disabled")

    async def cancel(self, run_id: str, reason_code: str = "user_requested") -> BridgeCancellationResult:
        return BridgeCancellationResult(BridgeCancellationDisposition.ACCEPTED, run_id, NOW, reason_code)


def request(**overrides) -> BridgeRunRequest:
    values = {
        "run_id": "bridge-run-001",
        "provider_id": "codex_bridge",
        "project_id": "project-001",
        "sandbox_id": "sandbox-001",
        "instruction": INSTRUCTION,
        "created_at": NOW,
        "correlation_id": "correlation-001",
    }
    values.update(overrides)
    return BridgeRunRequest(**values)


def record(status: BridgeRunStatus = BridgeRunStatus.QUEUED, *, updated_offset: int = 0) -> BridgeRunRecord:
    updated = NOW + timedelta(seconds=updated_offset)
    return BridgeRunRecord(
        run_id="bridge-run-001",
        provider_id="codex_bridge",
        status=status,
        created_at=NOW,
        updated_at=updated,
        started_at=NOW if status not in {BridgeRunStatus.QUEUED, BridgeRunStatus.VALIDATING, BridgeRunStatus.PREPARING_SANDBOX} else None,
        finished_at=updated if status in TERMINAL_STATES else None,
        project_id="project-001",
        sandbox_id="sandbox-001",
        correlation_id="correlation-001",
        instruction_hash=hashlib.sha256(INSTRUCTION.encode()).hexdigest(),
        instruction_length=len(INSTRUCTION),
    )


def sandbox(tmp_path: Path, **overrides) -> BridgeSandboxContext:
    values = {
        "sandbox_id": "sandbox-001",
        "run_id": "bridge-run-001",
        "project_id": "project-001",
        "creation_status": "ready",
        "cleanup_policy": "retain_for_review",
        "containment_verified": True,
        "internal_sandbox_root": tmp_path / "managed-sandboxes" / "bridge-run-001",
        "managed_sandbox_root": tmp_path / "managed-sandboxes",
        "active_workspace_root": tmp_path / "active-projects" / "project-001",
    }
    values.update(overrides)
    return BridgeSandboxContext(**values)


def event(sequence: int, **overrides) -> BridgeRunEvent:
    values = {
        "event_id": f"event-{sequence:03d}",
        "run_id": "bridge-run-001",
        "sequence": sequence,
        "timestamp": NOW + timedelta(seconds=sequence),
        "event_type": BridgeEventType.PROGRESS,
        "safe_message": "Validated metadata.",
        "progress": 10,
    }
    values.update(overrides)
    return BridgeRunEvent(**values)


def test_provider_contract_is_runtime_checkable_and_async() -> None:
    provider = FakeProvider()
    assert isinstance(provider, BridgeProvider)
    assert asyncio.run(provider.detect()).available is False
    assert asyncio.run(provider.validate(request())).valid is True


def test_capability_defaults_are_conservative_and_sandbox_cannot_be_disabled() -> None:
    capabilities = FakeProvider().capabilities()
    assert capabilities.available is False
    assert capabilities.sandbox_required is True
    assert all(
        value is False
        for key, value in capabilities.to_public_dict().items()
        if key.startswith("supports_") or key == "non_interactive"
    )
    with pytest.raises(BridgeDomainError) as caught:
        BridgeCapabilities(provider_id="codex_bridge", display_name="Codex", sandbox_required=False)
    assert caught.value.code is BridgeErrorCode.SANDBOX_REQUIRED
    with pytest.raises(BridgeDomainError) as unavailable:
        BridgeCapabilities(provider_id="codex_bridge", display_name="Codex", available=True)
    assert unavailable.value.code is BridgeErrorCode.PROVIDER_DISABLED


@pytest.mark.parametrize("provider_id", ["", "Codex", "codex-bridge", "unknown_bridge", "../../codex"])
def test_provider_id_uses_an_explicit_allowlist(provider_id: str) -> None:
    with pytest.raises(BridgeDomainError) as caught:
        request(provider_id=provider_id)
    assert caught.value.code is BridgeErrorCode.PROVIDER_UNSUPPORTED


def test_registry_rejects_unknown_and_duplicate_providers() -> None:
    registry = BridgeProviderRegistry()
    with pytest.raises(BridgeDomainError) as unknown:
        registry.get("opencode_bridge")
    assert unknown.value.code is BridgeErrorCode.PROVIDER_UNAVAILABLE
    registry.register(FakeProvider())
    with pytest.raises(BridgeDomainError):
        registry.register(FakeProvider())


def test_registry_registration_never_enables_routing() -> None:
    registry = BridgeProviderRegistry()
    registry.register(FakeProvider())
    assert registry.routing_enabled is False
    assert registry.is_routable("codex_bridge") is False


def test_raw_instruction_is_excluded_from_repr_and_serialization() -> None:
    run_request = request(instruction="secret runtime instruction")
    assert "secret runtime instruction" not in repr(run_request)
    serialized = json.dumps(run_request.to_safe_metadata())
    assert "secret runtime instruction" not in serialized
    assert not hasattr(run_request, "command")
    assert not hasattr(run_request, "environment")


def test_instruction_hash_and_length_are_stable() -> None:
    run_request = request()
    assert run_request.instruction_hash == hashlib.sha256(INSTRUCTION.encode("utf-8")).hexdigest()
    assert run_request.instruction_length == len(INSTRUCTION)


@pytest.mark.parametrize("timeout", [9, 901, 0, -1])
def test_timeout_has_safe_bounds(timeout: int) -> None:
    with pytest.raises(BridgeDomainError):
        request(timeout_seconds=timeout)


LEGAL_TRANSITIONS = [
    (current, next_status)
    for current, next_statuses in ALLOWED_TRANSITIONS.items()
    for next_status in next_statuses
]


@pytest.mark.parametrize(("current", "next_status"), LEGAL_TRANSITIONS)
def test_every_declared_state_transition_is_legal(current: BridgeRunStatus, next_status: BridgeRunStatus) -> None:
    updated = BridgeRunStateMachine().transition(record(current), next_status, at=NOW + timedelta(seconds=1))
    assert updated.status is next_status


def test_illegal_transition_and_non_monotonic_timestamp_are_rejected() -> None:
    machine = BridgeRunStateMachine()
    with pytest.raises(BridgeDomainError) as illegal:
        machine.transition(record(), BridgeRunStatus.COMPLETED, at=NOW + timedelta(seconds=1))
    assert illegal.value.code is BridgeErrorCode.INVALID_TRANSITION
    with pytest.raises(BridgeDomainError):
        machine.transition(record(), BridgeRunStatus.VALIDATING, at=NOW - timedelta(seconds=1))


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES, key=lambda item: item.value))
def test_terminal_states_are_immutable(terminal: BridgeRunStatus) -> None:
    with pytest.raises(BridgeDomainError):
        BridgeRunStateMachine().transition(record(terminal), BridgeRunStatus.RUNNING, at=NOW + timedelta(seconds=1))


def test_cancellation_is_idempotent_and_terminal_runs_do_not_restart() -> None:
    machine = BridgeRunStateMachine()
    first, accepted = machine.request_cancellation(record(), at=NOW + timedelta(seconds=1))
    second, repeated = machine.request_cancellation(first, at=NOW + timedelta(seconds=2))
    assert accepted.disposition is BridgeCancellationDisposition.ACCEPTED
    assert repeated.disposition is BridgeCancellationDisposition.ALREADY_REQUESTED
    assert second == first
    cancelled = machine.transition(second, BridgeRunStatus.CANCELLED, at=NOW + timedelta(seconds=3))
    unchanged, terminal = machine.request_cancellation(cancelled)
    assert terminal.disposition is BridgeCancellationDisposition.ALREADY_TERMINAL
    assert unchanged == cancelled


def test_store_enforces_event_sequence_and_updates_count(tmp_path: Path) -> None:
    store = BridgeRunStore(tmp_path / "state" / "runs.json")
    store.create(request())
    store.append_event(event(1))
    assert store.get_record("bridge-run-001").event_count == 1
    with pytest.raises(BridgeDomainError) as caught:
        store.append_event(event(3))
    assert caught.value.code is BridgeErrorCode.PERSISTENCE_FAILED


def test_events_are_bounded_sanitized_and_reject_patch_content() -> None:
    unsafe = "token=abc123 at C:\\Users\\name\\secret " + ("x" * 1000)
    cleaned = event(1, safe_message=unsafe).safe_message
    assert cleaned is not None
    assert "abc123" not in cleaned and "C:\\Users" not in cleaned
    assert len(cleaned) <= MAX_SAFE_MESSAGE_LENGTH
    with pytest.raises(BridgeDomainError):
        event(1, safe_message="diff --git a/a.py b/a.py")


def test_run_record_round_trip_is_deterministic_and_has_no_instruction(tmp_path: Path) -> None:
    store = BridgeRunStore(tmp_path / "runs.json")
    created = store.create(request(instruction="do not persist this prompt"))
    loaded = store.get_record(created.run_id)
    assert loaded == created
    contents = store.path.read_text(encoding="utf-8")
    assert "do not persist this prompt" not in contents
    assert contents == store.path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "contents",
    ["not json", '{"schema_version":99,"records":[],"events":[],"artifacts":[]}', '{"schema_version":1,"records":{}}'],
)
def test_corrupt_and_unsupported_store_data_fails_closed(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "runs.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(BridgeDomainError) as caught:
        BridgeRunStore(path).list_records()
    assert caught.value.code is BridgeErrorCode.RECORD_CORRUPT


def test_restart_reconciliation_marks_incomplete_runs_interrupted(tmp_path: Path) -> None:
    store = BridgeRunStore(tmp_path / "runs.json")
    created = store.create(request())
    validating = BridgeRunStateMachine().transition(created, BridgeRunStatus.VALIDATING, at=NOW + timedelta(seconds=1))
    store.upsert_record(validating)
    reconciled = store.reconcile_incomplete_runs(at=NOW + timedelta(seconds=2))[0]
    assert reconciled.status is BridgeRunStatus.INTERRUPTED
    assert reconciled.failure_code == "unexpected_restart"


def test_artifacts_are_contained_references_and_patch_requires_review(tmp_path: Path) -> None:
    managed = tmp_path / "patches"
    artifact = BridgeArtifactReference(
        artifact_id="artifact-001",
        run_id="bridge-run-001",
        artifact_type=BridgeArtifactType.PATCH,
        created_at=NOW,
        content_hash="a" * 64,
        size_bytes=100,
        storage_reference="review-001/patch-001.patch",
        review_required=True,
        review_id="review-001",
        pipeline_artifact_id="patch-001",
    )
    assert artifact.resolve_internal(managed).is_relative_to(managed.resolve())
    assert "storage_reference" not in artifact.to_public_dict()
    with pytest.raises(BridgeDomainError):
        replace(artifact, storage_reference="../escape.patch")
    with pytest.raises(BridgeDomainError):
        replace(artifact, storage_reference="C:/escape.patch")
    with pytest.raises(BridgeDomainError):
        replace(artifact, review_id=None)


def test_artifact_size_limit_is_enforced() -> None:
    with pytest.raises(BridgeDomainError):
        BridgeArtifactReference(
            artifact_id="artifact-001",
            run_id="bridge-run-001",
            artifact_type=BridgeArtifactType.DIAGNOSTIC,
            created_at=NOW,
            content_hash="a" * 64,
            size_bytes=11,
            max_size_bytes=10,
            storage_reference="diagnostics/summary.json",
        )


def test_sandbox_is_required_separate_and_public_paths_are_sanitized(tmp_path: Path) -> None:
    context = sandbox(tmp_path)
    public = json.dumps(context.to_public_dict())
    assert str(context.internal_sandbox_root) not in public
    assert str(context.active_workspace_root) not in public
    with pytest.raises(BridgeDomainError) as unverified:
        sandbox(tmp_path, containment_verified=False)
    assert unverified.value.code is BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED
    with pytest.raises(BridgeDomainError):
        sandbox(tmp_path, internal_sandbox_root=tmp_path / "active-projects" / "project-001" / "nested")
    with pytest.raises(BridgeDomainError):
        sandbox(tmp_path, internal_sandbox_root=tmp_path / "unmanaged" / "bridge-run-001")


def test_run_context_requires_matching_verified_sandbox(tmp_path: Path) -> None:
    BridgeRunContext(request(), sandbox(tmp_path))
    with pytest.raises(BridgeDomainError):
        BridgeRunContext(request(), sandbox(tmp_path, run_id="bridge-run-999"))


def test_error_normalization_is_stable_and_does_not_leak_internal_text() -> None:
    normalized = normalize_bridge_error(RuntimeError("token=secret C:\\private\\file"))
    assert normalized.code is BridgeErrorCode.INTERNAL_ERROR
    assert "secret" not in normalized.to_public_dict()["message"]
    assert normalized.internal_details == {"exception_type": "RuntimeError"}


def test_future_providers_remain_unavailable_and_not_routable() -> None:
    registry = BridgeProviderRegistry()
    for provider_id in ("codex_bridge", "claude_code_bridge", "opencode_bridge"):
        assert registry.is_routable(provider_id) is False
        with pytest.raises(BridgeDomainError) as caught:
            registry.get(provider_id)
        assert caught.value.code is BridgeErrorCode.PROVIDER_UNAVAILABLE
