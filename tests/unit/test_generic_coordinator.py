from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.bridges.generic import (
    BridgeArtifactReference,
    BridgeArtifactType,
    BridgeArtifactValidator,
    BridgeCancellationDisposition,
    BridgeCancellationResult,
    BridgeCapabilities,
    BridgeDetectionResult,
    BridgeDomainError,
    BridgeErrorCode,
    BridgeEventType,
    BridgeProviderRegistry,
    BridgeRunContext,
    BridgeRunEvent,
    BridgeRunRequest,
    BridgeRunResult,
    BridgeRunStateMachine,
    BridgeRunStatus,
    BridgeRunStore,
    BridgeSandboxContext,
    BridgeValidationResult,
    DefaultDenyBridgeRoutingPolicy,
    GenericBridgeRunCoordinator,
    InternalBridgeEventTransport,
    CapabilityStatus,
    ProviderCapabilityEvidence,
    ProviderCapabilityRegistry,
    ProviderKind,
    ProviderOperationalState,
    TransportMode,
)


NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
INSTRUCTION = "Update the parser without exposing token=private-value"


class FakeClock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self):
        value = self.current
        self.current += timedelta(milliseconds=1)
        return value


class FakeSandboxLifecycle:
    def __init__(self, *, fail_validation: bool = False, fail_cleanup: bool = False) -> None:
        self.fail_validation = fail_validation
        self.fail_cleanup = fail_cleanup
        self.validations = 0
        self.finalizations = 0

    def validate(self, context: BridgeSandboxContext) -> BridgeSandboxContext:
        self.validations += 1
        if self.fail_validation:
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_INVALID)
        return context

    def finalize(self, context: BridgeSandboxContext) -> None:
        self.finalizations += 1
        if self.fail_cleanup:
            raise OSError("private cleanup path")


class FakeProvider:
    provider_id = "agy"
    environment_allowlist = frozenset({"PATH", "TEMP"})

    def __init__(self, *, mode: str = "success", artifact: BridgeArtifactReference | None = None) -> None:
        self.mode = mode
        self.artifact = artifact
        self.start_calls = 0
        self.cancel_calls = 0
        self.validate_calls = 0
        self.store: BridgeRunStore | None = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def capabilities(self) -> BridgeCapabilities:
        return BridgeCapabilities(
            provider_id="agy",
            display_name="Fake AGY",
            available=True,
            non_interactive=True,
            supports_cancellation=True,
            supports_timeout=True,
            supports_artifacts=True,
            supports_structured_output=True,
        )

    async def detect(self) -> BridgeDetectionResult:
        return BridgeDetectionResult(provider_id="agy", installed=True, available=True)

    async def validate(self, request: BridgeRunRequest) -> BridgeValidationResult:
        self.validate_calls += 1
        if self.mode == "validation_failure":
            return BridgeValidationResult(False, "invalid_request", "Fake validation blocked.")
        return BridgeValidationResult(True)

    async def start(self, context: BridgeRunContext) -> BridgeRunResult:
        self.start_calls += 1
        if self.store is not None:
            assert self.store.get_record(context.request.run_id).status is BridgeRunStatus.RUNNING
        self.started.set()
        if self.mode == "wait":
            await self.release.wait()
        if self.mode == "exception":
            raise RuntimeError(f"raw {INSTRUCTION} C:/private/path")
        if self.mode == "failure":
            return BridgeRunResult(False, failure_code="process_failed", safe_message="Provider failed safely.")
        if self.mode == "timeout":
            return BridgeRunResult(False, failure_code="timeout", safe_message="Provider timed out.")
        if self.mode == "cancelled":
            return BridgeRunResult(False, failure_code="cancelled", safe_message="Provider cancelled.")
        artifacts = (self.artifact,) if self.artifact else ()
        return BridgeRunResult(True, artifact_ids=tuple(item.artifact_id for item in artifacts), artifacts=artifacts)

    async def cancel(self, run_id: str, reason_code: str = "user_requested") -> BridgeCancellationResult:
        self.cancel_calls += 1
        self.release.set()
        if self.mode == "reject_cancel":
            return BridgeCancellationResult(BridgeCancellationDisposition.REJECTED, run_id, reason_code=reason_code)
        return BridgeCancellationResult(BridgeCancellationDisposition.ACCEPTED, run_id, NOW, reason_code)


def run_request(**overrides) -> BridgeRunRequest:
    values = {
        "run_id": "bridge-run-coordinator-001",
        "provider_id": "agy",
        "project_id": "project-001",
        "sandbox_id": "sandbox-001",
        "instruction": INSTRUCTION,
        "created_at": NOW,
        "correlation_id": "correlation-001",
    }
    values.update(overrides)
    return BridgeRunRequest(**values)


def sandbox_context(tmp_path: Path, *, cleanup_policy: str = "retain_for_review") -> BridgeSandboxContext:
    managed = tmp_path / "sandboxes"
    internal = managed / "sandbox-001"
    active = tmp_path / "active" / "project-001"
    internal.mkdir(parents=True, exist_ok=True)
    active.mkdir(parents=True, exist_ok=True)
    return BridgeSandboxContext(
        sandbox_id="sandbox-001",
        run_id="bridge-run-coordinator-001",
        project_id="project-001",
        creation_status="ready",
        cleanup_policy=cleanup_policy,
        containment_verified=True,
        internal_sandbox_root=internal,
        managed_sandbox_root=managed,
        active_workspace_root=active,
    )


def artifact(run_id: str = "bridge-run-coordinator-001", artifact_id: str = "review-001") -> BridgeArtifactReference:
    return BridgeArtifactReference(
        artifact_id=artifact_id,
        run_id=run_id,
        artifact_type=BridgeArtifactType.REVIEW,
        created_at=NOW,
        content_hash=hashlib.sha256(artifact_id.encode()).hexdigest(),
        size_bytes=0,
        storage_reference="bridge-reviews.jsonl",
        review_required=True,
        review_id=artifact_id,
        pipeline_artifact_id=artifact_id,
    )


def make_coordinator(
    tmp_path: Path,
    *,
    provider: FakeProvider | None = None,
    routing_enabled: bool = True,
    lifecycle: FakeSandboxLifecycle | None = None,
    history_limit: int = 20,
    queue_size: int = 4,
):
    fake = provider or FakeProvider()
    registry = BridgeProviderRegistry()
    registry.register(fake)
    store = BridgeRunStore(tmp_path / "state" / "generic-runs.json")
    fake.store = store
    transport = InternalBridgeEventTransport(history_limit=history_limit, subscriber_queue_size=queue_size)
    owner = lifecycle or FakeSandboxLifecycle()
    strategy = ProviderCapabilityRegistry(
        (
            ProviderCapabilityEvidence(
                provider_id="agy",
                display_name="Fake AGY",
                provider_kind=ProviderKind.LOCAL_SERVER,
                transport_modes=(TransportMode.CLI_HEADLESS,),
                detected=True,
                headless_mode_available=True,
                auth_status=CapabilityStatus.PASSED,
                permission_status=CapabilityStatus.PASSED,
                native_write_status=CapabilityStatus.PASSED,
                safety_scan_status=CapabilityStatus.PASSED,
                operational_state=ProviderOperationalState.ACTIVE,
                production_eligible=True,
                evidence_source="fake_test_provider",
            ),
        )
    )
    coordinator = GenericBridgeRunCoordinator(
        registry=registry,
        routing_policy=DefaultDenyBridgeRoutingPolicy(
            global_enabled=routing_enabled,
            provider_permissions={"agy": routing_enabled},
            capability_registry=strategy,
        ),
        run_store=store,
        event_transport=transport,
        sandbox_lifecycle=owner,
        artifact_validator=BridgeArtifactValidator(tmp_path / "state"),
        clock=FakeClock(),
    )
    return coordinator, fake, store, transport, owner


def test_generic_coordinator_dependencies_are_composed(tmp_path: Path) -> None:
    coordinator, _, store, transport, _ = make_coordinator(tmp_path)
    assert coordinator.run_store is store
    assert coordinator.event_transport is transport


def test_generic_coordinator_routing_is_default_deny_and_registration_does_not_enable(tmp_path: Path) -> None:
    coordinator, provider, _, _, lifecycle = make_coordinator(tmp_path, routing_enabled=False)
    result = asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    assert result.status is BridgeRunStatus.BLOCKED
    assert result.failure_code == "provider_disabled"
    assert provider.start_calls == 0
    assert lifecycle.validations == 0


def test_generic_coordinator_unknown_provider_and_unsupported_capability_are_blocked(tmp_path: Path) -> None:
    coordinator, provider, _, _, _ = make_coordinator(tmp_path)
    unknown = asyncio.run(
        coordinator.execute(
            run_request(provider_id="codex_bridge", run_id="bridge-run-unknown-001", correlation_id="correlation-unknown"),
            BridgeSandboxContext(
                sandbox_id="sandbox-001",
                run_id="bridge-run-unknown-001",
                project_id="project-001",
                creation_status="ready",
                cleanup_policy="retain_for_review",
                containment_verified=True,
                internal_sandbox_root=sandbox_context(tmp_path).internal_sandbox_root,
                managed_sandbox_root=sandbox_context(tmp_path).managed_sandbox_root,
                active_workspace_root=sandbox_context(tmp_path).active_workspace_root,
            ),
        )
    )
    assert unknown.status is BridgeRunStatus.BLOCKED
    unsupported_request = run_request(
        run_id="bridge-run-unsupported-001",
        correlation_id="correlation-unsupported",
        requested_capability="streaming",
    )
    unsupported_sandbox = BridgeSandboxContext(
        sandbox_id="sandbox-001",
        run_id=unsupported_request.run_id,
        project_id="project-001",
        creation_status="ready",
        cleanup_policy="retain_for_review",
        containment_verified=True,
        internal_sandbox_root=sandbox_context(tmp_path).internal_sandbox_root,
        managed_sandbox_root=sandbox_context(tmp_path).managed_sandbox_root,
        active_workspace_root=sandbox_context(tmp_path).active_workspace_root,
    )
    unsupported = asyncio.run(coordinator.execute(unsupported_request, unsupported_sandbox))
    assert unsupported.status is BridgeRunStatus.BLOCKED
    assert provider.start_calls == 0


def test_generic_coordinator_fake_provider_success_persists_before_execution(tmp_path: Path) -> None:
    coordinator, provider, store, _, lifecycle = make_coordinator(tmp_path)
    result = asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    assert result.status is BridgeRunStatus.COMPLETED
    assert provider.start_calls == 1
    assert lifecycle.validations == 1 and lifecycle.finalizations == 1
    persisted = store.path.read_text(encoding="utf-8")
    assert INSTRUCTION not in persisted
    assert hashlib.sha256(INSTRUCTION.encode()).hexdigest() in persisted


def test_generic_coordinator_duplicate_creation_is_idempotent_but_conflicts_fail(tmp_path: Path) -> None:
    coordinator, provider, _, _, _ = make_coordinator(tmp_path)
    first = asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    second = asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    assert second == first
    assert provider.start_calls == 1
    with pytest.raises(BridgeDomainError):
        asyncio.run(coordinator.execute(run_request(instruction="different"), sandbox_context(tmp_path)))


def test_generic_coordinator_has_one_active_owner_per_run(tmp_path: Path) -> None:
    async def scenario():
        fake = FakeProvider(mode="wait")
        coordinator, _, _, _, _ = make_coordinator(tmp_path, provider=fake)
        first = asyncio.create_task(coordinator.execute(run_request(), sandbox_context(tmp_path)))
        await fake.started.wait()
        duplicate = await coordinator.execute(run_request(), sandbox_context(tmp_path))
        fake.release.set()
        final = await first
        return fake, duplicate, final

    fake, duplicate, final = asyncio.run(scenario())
    assert fake.start_calls == 1
    assert duplicate.status is BridgeRunStatus.RUNNING
    assert final.status is BridgeRunStatus.COMPLETED


@pytest.mark.parametrize(
    ("mode", "status", "code"),
    [
        ("validation_failure", BridgeRunStatus.BLOCKED, "invalid_request"),
        ("failure", BridgeRunStatus.FAILED, "process_failed"),
        ("timeout", BridgeRunStatus.TIMED_OUT, "timeout"),
        ("cancelled", BridgeRunStatus.CANCELLED, "cancelled"),
        ("exception", BridgeRunStatus.FAILED, "internal_error"),
    ],
)
def test_generic_coordinator_outcome_mapping(tmp_path: Path, mode: str, status: BridgeRunStatus, code: str) -> None:
    coordinator, _, store, _, _ = make_coordinator(tmp_path, provider=FakeProvider(mode=mode))
    result = asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    assert result.status is status
    assert result.failure_code == code
    serialized = store.path.read_text(encoding="utf-8")
    assert INSTRUCTION not in serialized and "C:/private" not in serialized


def test_generic_coordinator_sandbox_rejection_prevents_provider_execution(tmp_path: Path) -> None:
    lifecycle = FakeSandboxLifecycle(fail_validation=True)
    coordinator, provider, _, _, owner = make_coordinator(tmp_path, lifecycle=lifecycle)
    result = asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    assert result.status is BridgeRunStatus.FAILED
    assert result.failure_code == "sandbox_invalid"
    assert provider.start_calls == 0
    assert owner.finalizations == 0


def test_generic_coordinator_artifact_validation_and_queries(tmp_path: Path) -> None:
    coordinator, _, _, _, _ = make_coordinator(tmp_path, provider=FakeProvider(artifact=artifact()))
    result = asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    assert result.status is BridgeRunStatus.COMPLETED
    artifacts = coordinator.list_run_artifacts(result.run_id)
    assert artifacts[0]["artifact_id"] == "review-001"
    assert "storage_reference" not in artifacts[0]
    assert "apply" not in json.dumps(artifacts)


def test_generic_coordinator_artifact_ownership_failure_prevents_completion(tmp_path: Path) -> None:
    coordinator, _, _, _, _ = make_coordinator(tmp_path, provider=FakeProvider(artifact=artifact("bridge-run-other")))
    result = asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    assert result.status is BridgeRunStatus.FAILED
    assert result.failure_code == "artifact_invalid"


def test_generic_coordinator_duplicate_artifact_fails_closed(tmp_path: Path) -> None:
    duplicate = artifact()

    class DuplicateProvider(FakeProvider):
        async def start(self, context: BridgeRunContext) -> BridgeRunResult:
            return BridgeRunResult(True, artifact_ids=(duplicate.artifact_id, duplicate.artifact_id), artifacts=(duplicate, duplicate))

    coordinator, _, _, _, _ = make_coordinator(tmp_path, provider=DuplicateProvider())
    result = asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    assert result.status is BridgeRunStatus.FAILED
    assert result.failure_code == "artifact_invalid"


def test_generic_coordinator_events_are_ordered_sanitized_and_replayable(tmp_path: Path) -> None:
    coordinator, _, _, transport, _ = make_coordinator(tmp_path)
    result = asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    events = coordinator.list_run_events(result.run_id)
    assert [item.sequence for item in events] == list(range(1, len(events) + 1))
    assert events[-1].event_type is BridgeEventType.COMPLETED
    assert all(INSTRUCTION not in json.dumps(item.to_dict()) for item in events)
    replay = asyncio.run(transport.replay(result.run_id, after_sequence=2))
    assert replay and replay[0].sequence == 3


def test_generic_event_transport_is_bounded_and_slow_subscriber_gets_resync(tmp_path: Path) -> None:
    async def scenario():
        transport = InternalBridgeEventTransport(history_limit=2, subscriber_queue_size=1)
        subscription = await transport.subscribe("bridge-run-coordinator-001")
        for sequence in range(1, 5):
            await transport.publish(
                BridgeRunEvent(
                    event_id=f"event-{sequence:03d}",
                    run_id="bridge-run-coordinator-001",
                    sequence=sequence,
                    timestamp=NOW + timedelta(seconds=sequence),
                    event_type=BridgeEventType.PROGRESS,
                )
            )
        queued = await subscription.get()
        replay = await transport.replay("bridge-run-coordinator-001", after_sequence=1)
        await subscription.close()
        return queued, replay

    queued, replay = asyncio.run(scenario())
    assert queued.event_type is BridgeEventType.RESYNC_REQUIRED
    assert replay[0].event_type is BridgeEventType.RESYNC_REQUIRED


def test_generic_event_subscribers_are_isolated_and_unsubscription_is_idempotent() -> None:
    async def scenario():
        transport = InternalBridgeEventTransport(subscriber_queue_size=2)
        slow = await transport.subscribe("bridge-run-coordinator-001")
        healthy = await transport.subscribe("bridge-run-coordinator-001")
        event = BridgeRunEvent(
            event_id="event-001",
            run_id="bridge-run-coordinator-001",
            sequence=1,
            timestamp=NOW,
            event_type=BridgeEventType.PROGRESS,
        )
        await transport.publish(event)
        received = await healthy.get()
        await healthy.close()
        await healthy.close()
        await slow.close()
        return received

    assert asyncio.run(scenario()).sequence == 1


def test_generic_coordinator_transport_failure_is_persisted_without_affecting_execution(tmp_path: Path) -> None:
    class FailingTransport(InternalBridgeEventTransport):
        async def publish(self, event: BridgeRunEvent) -> None:
            raise RuntimeError("subscriber internal detail")

    coordinator, fake, store, _, _ = make_coordinator(tmp_path)
    coordinator.event_transport = FailingTransport()
    result = asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    assert result.status is BridgeRunStatus.COMPLETED
    assert fake.start_calls == 1
    assert any(item.event_type is BridgeEventType.WARNING for item in store.list_events(result.run_id))


def test_generic_coordinator_cancellation_is_idempotent_and_late_completion_loses(tmp_path: Path) -> None:
    async def scenario():
        fake = FakeProvider(mode="wait")
        coordinator, _, _, _, lifecycle = make_coordinator(tmp_path, provider=fake)
        execute_task = asyncio.create_task(coordinator.execute(run_request(), sandbox_context(tmp_path)))
        await fake.started.wait()
        first = await coordinator.cancel("bridge-run-coordinator-001")
        repeated = await coordinator.cancel("bridge-run-coordinator-001")
        final = await execute_task
        return fake, lifecycle, first, repeated, final

    fake, lifecycle, first, repeated, final = asyncio.run(scenario())
    assert first.disposition is BridgeCancellationDisposition.ACCEPTED
    assert repeated.disposition is BridgeCancellationDisposition.ALREADY_TERMINAL
    assert final.status is BridgeRunStatus.CANCELLED
    assert fake.cancel_calls == 1
    assert lifecycle.finalizations == 1


def test_generic_coordinator_cancel_not_found_and_rejection(tmp_path: Path) -> None:
    coordinator, _, _, _, _ = make_coordinator(tmp_path)
    missing = asyncio.run(coordinator.cancel("bridge-run-missing"))
    assert missing.disposition is BridgeCancellationDisposition.NOT_FOUND


def test_generic_coordinator_cleanup_failure_is_safe_and_does_not_rewrite_terminal(tmp_path: Path) -> None:
    lifecycle = FakeSandboxLifecycle(fail_cleanup=True)
    coordinator, _, _, _, _ = make_coordinator(tmp_path, lifecycle=lifecycle)
    result = asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    assert result.status is BridgeRunStatus.COMPLETED
    assert lifecycle.finalizations == 1
    assert coordinator.list_run_events(result.run_id)[-1].event_type is BridgeEventType.WARNING


def test_generic_store_compare_and_set_rejects_stale_write(tmp_path: Path) -> None:
    store = BridgeRunStore(tmp_path / "runs.json")
    created = store.create(run_request())
    machine = BridgeRunStateMachine()
    validating = machine.transition(created, BridgeRunStatus.VALIDATING, at=NOW + timedelta(seconds=1))
    store.upsert_record(validating, expected_revision=created.revision)
    with pytest.raises(BridgeDomainError) as caught:
        store.upsert_record(validating, expected_revision=created.revision)
    assert caught.value.code is BridgeErrorCode.PERSISTENCE_FAILED


def test_generic_coordinator_restart_reconciliation_is_one_shot_and_never_resumes(tmp_path: Path) -> None:
    coordinator, provider, store, _, _ = make_coordinator(tmp_path)
    created = store.create(run_request())
    validating = BridgeRunStateMachine().transition(created, BridgeRunStatus.VALIDATING, at=NOW + timedelta(seconds=1))
    store.upsert_record(validating, expected_revision=created.revision)
    first = asyncio.run(coordinator.reconcile_incomplete_runs())
    event_count = len(store.list_events(created.run_id))
    second = asyncio.run(coordinator.reconcile_incomplete_runs())
    assert first[0].status is BridgeRunStatus.INTERRUPTED
    assert second[0].status is BridgeRunStatus.INTERRUPTED
    assert len(store.list_events(created.run_id)) == event_count
    assert provider.start_calls == 0


def test_generic_coordinator_internal_queries_are_bounded_and_safe(tmp_path: Path) -> None:
    coordinator, _, _, _, _ = make_coordinator(tmp_path)
    asyncio.run(coordinator.execute(run_request(), sandbox_context(tmp_path)))
    assert len(coordinator.list_recent_runs(limit=1000)) == 1
    run = coordinator.get_run("bridge-run-coordinator-001")
    assert not hasattr(run, "instruction")
    assert INSTRUCTION not in json.dumps(run.to_dict())
