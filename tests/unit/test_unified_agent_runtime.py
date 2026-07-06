from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.agent_runtime.approval_broker import AgentApprovalBroker
from backend.agent_runtime.product_agent_service import ProductAgentService
from backend.agent_runtime.product_provider_registry import ProductProviderRegistry
from backend.agent_runtime.unified_agent_service import UnifiedAgentService
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.codex_status import CodexAlignedStatus
from backend.bridges.generic.coordinator import (
    BridgeArtifactValidator,
    ExistingBridgeSandboxLifecycle,
    GenericBridgeRunCoordinator,
)
from backend.bridges.generic.event_transport import InternalBridgeEventTransport
from backend.bridges.generic.models import (
    BridgeCancellationDisposition,
    BridgeCancellationResult,
    BridgeCapabilities,
    BridgeDetectionResult,
    BridgeRunContext,
    BridgeRunRequest,
    BridgeRunResult,
    BridgeValidationResult,
)
from backend.bridges.generic.persistence import BridgeRunStore
from backend.bridges.generic.provider_capabilities import (
    CapabilityStatus,
    ProviderCapabilityEvidence,
    ProviderCapabilityRegistry,
    ProviderKind,
    ProviderOperationalState,
    TransportMode,
)
from backend.bridges.generic.registry import BridgeProviderRegistry
from backend.bridges.generic.routing import DefaultDenyBridgeRoutingPolicy
from backend.bridges.providers.sandbox_agent_artifacts import create_review_artifact
from backend.bridges.providers.codex_app_server import CodexAppServerProvider
from backend.bridges.sandbox_service import BridgeSandboxService


class FakeCodexProvider:
    provider_id = "codex"
    environment_allowlist = frozenset({"PATH"})
    execution_enabled = True

    def __init__(self, review_service: BridgeDiffService, managed_root: Path) -> None:
        self.review_service = review_service
        self.managed_root = managed_root

    def capabilities(self) -> BridgeCapabilities:
        return BridgeCapabilities(
            provider_id="codex",
            display_name="Fake Codex",
            available=True,
            non_interactive=True,
            supports_cancellation=True,
            supports_timeout=True,
            supports_artifacts=True,
        )

    async def detect(self) -> BridgeDetectionResult:
        return BridgeDetectionResult(
            provider_id="codex",
            installed=True,
            available=True,
            authentication_status="authenticated",
            capabilities=self.capabilities(),
        )

    async def validate(self, request: BridgeRunRequest) -> BridgeValidationResult:
        return BridgeValidationResult(request.provider_id == "codex")

    async def start(self, context: BridgeRunContext) -> BridgeRunResult:
        sandbox = context.sandbox.internal_sandbox_root
        baseline = self.review_service.inspect_workspace(sandbox)
        (sandbox / "provider.txt").write_text("sandbox only\n", encoding="utf-8")
        artifact = create_review_artifact(
            provider_id="codex",
            run_id=context.request.run_id,
            sandbox_root=sandbox,
            managed_root=self.managed_root,
            baseline=baseline,
            review_service=self.review_service,
            artifact_source="fake_codex",
        )
        assert artifact is not None
        return BridgeRunResult(True, artifact_ids=(artifact.artifact_id,), artifacts=(artifact,), review_id=artifact.review_id)

    async def cancel(self, run_id: str, reason_code: str = "user_requested") -> BridgeCancellationResult:
        return BridgeCancellationResult(BridgeCancellationDisposition.ACCEPTED, run_id, reason_code=reason_code)


@pytest.mark.asyncio
async def test_unified_runtime_routes_local_provider_through_persistent_coordinator(tmp_path: Path) -> None:
    active = tmp_path / "active"
    active.mkdir()
    (active / "README.md").write_text("unchanged\n", encoding="utf-8")
    state = tmp_path / "state"
    review_service = BridgeDiffService()
    sandbox_service = BridgeSandboxService(state / "bridge-sandboxes")
    provider = FakeCodexProvider(review_service, state)
    registry = BridgeProviderRegistry()
    registry.register(provider)
    evidence = ProviderCapabilityRegistry((
        ProviderCapabilityEvidence(
            provider_id="codex",
            display_name="Fake Codex",
            provider_kind=ProviderKind.LOCAL_SERVER,
            transport_modes=(TransportMode.APP_SERVER,),
            headless_mode_available=True,
            permission_status=CapabilityStatus.PASSED,
            operational_state=ProviderOperationalState.EXPERIMENTAL,
            execution_allowed=True,
            routing_allowed=True,
        ),
    ))
    transport = InternalBridgeEventTransport()
    coordinator = GenericBridgeRunCoordinator(
        registry=registry,
        routing_policy=DefaultDenyBridgeRoutingPolicy(
            global_enabled=True,
            provider_permissions={"codex": True},
            capability_registry=evidence,
            allow_experimental=True,
        ),
        run_store=BridgeRunStore(state / "runs.json"),
        event_transport=transport,
        sandbox_lifecycle=ExistingBridgeSandboxLifecycle(sandbox_service),
        artifact_validator=BridgeArtifactValidator(state),
    )
    product = ProductAgentService(
        managed_sandbox_root=state / "tool-sandboxes",
        review_service=review_service,
        provider_registry=ProductProviderRegistry(),
        enabled=False,
    )
    service = UnifiedAgentService(
        product_service=product,
        bridge_registry=registry,
        bridge_coordinator=coordinator,
        sandbox_service=sandbox_service,
        approval_broker=AgentApprovalBroker(timeout_seconds=1),
        enabled=True,
    )
    statuses = await service.provider_statuses()
    codex_status = next(item for item in statuses if item["provider_id"] == "codex")
    assert codex_status["routeable"] is True
    assert codex_status["execution_mode"] == "sandbox_agent"

    started = await service.start_run(
        project_id="project-001",
        active_workspace_root=active,
        instruction="Create provider.txt",
        provider_id="codex",
        idempotency_key="request-unified-001",
    )
    for _ in range(100):
        run = service.get_run(str(started["run_id"]))
        if run["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("unified run did not complete")

    assert run["execution_mode"] == "sandbox_agent"
    assert run["review_id"]
    assert (active / "README.md").read_text(encoding="utf-8") == "unchanged\n"
    assert not (active / "provider.txt").exists()
    assert [item["sequence"] for item in service.events(str(run["run_id"]))] == list(range(1, 8))
    reused = await service.start_run(
        project_id="project-001",
        active_workspace_root=active,
        instruction="Create provider.txt",
        provider_id="codex",
        idempotency_key="request-unified-001",
    )
    assert reused["run_id"] == run["run_id"]
    await service.close()
    await transport.close()


@pytest.mark.asyncio
async def test_approval_broker_resolves_once_and_hides_completed_request() -> None:
    broker = AgentApprovalBroker(timeout_seconds=1)
    pending = asyncio.create_task(broker.request(run_id="agent-run-001", kind="command", safe_message="Run tests"))
    await asyncio.sleep(0)
    approval = broker.list_for_run("agent-run-001")[0]
    result = broker.resolve(
        run_id="agent-run-001",
        approval_id=approval["approval_id"],
        decision="approve_once",
    )
    assert result["decision"] == "approve_once"
    assert await pending == "approve_once"
    assert broker.list_for_run("agent-run-001") == ()


def test_product_registry_has_one_canonical_identity_per_local_provider() -> None:
    provider_ids = [item.provider_id for item in ProductProviderRegistry().list()]
    assert provider_ids.count("agy") == 1
    assert provider_ids.count("codex") == 1
    assert "agy_local_cli_paused" not in provider_ids
    assert "codex_local_cli_paused" not in provider_ids


@pytest.mark.asyncio
async def test_codex_app_server_protocol_uses_version_matched_sandbox_shape(tmp_path: Path) -> None:
    class FakeStdin:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def write(self, value: bytes) -> None:
            self.payloads.append(json.loads(value))

        async def drain(self) -> None:
            return None

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.stdout = asyncio.StreamReader()
            for value in (
                {"id": 1, "result": {}},
                {"id": 2, "result": {"thread": {"id": "thread-001"}}},
                {"id": 3, "result": {"turn": {"id": "turn-001"}}},
                {"method": "item/agentMessage/delta", "params": {"itemId": "message-001", "delta": "Sandbox ", "threadId": "thread-001", "turnId": "turn-001"}},
                {"method": "item/completed", "params": {"item": {"id": "message-001", "type": "agentMessage", "phase": "final_answer", "text": "Sandbox task complete."}, "threadId": "thread-001", "turnId": "turn-001", "completedAtMs": 1}},
                {"method": "turn/completed", "params": {"turn": {"id": "turn-001"}}},
            ):
                self.stdout.feed_data((json.dumps(value) + "\n").encode())
            self.stdout.feed_eof()

    managed = tmp_path / "managed"
    sandbox = managed / "run-001"
    active = tmp_path / "active"
    sandbox.mkdir(parents=True)
    active.mkdir()
    request = BridgeRunRequest(
        run_id="agent-run-protocol-001",
        provider_id="codex",
        project_id="project-001",
        sandbox_id="sandbox-001",
        instruction="Create one file",
        correlation_id="request-protocol-001",
    )
    from backend.bridges.generic.models import BridgeSandboxContext

    context = BridgeRunContext(
        request,
        BridgeSandboxContext(
            sandbox_id="sandbox-001",
            run_id="agent-run-protocol-001",
            project_id="project-001",
            creation_status="ready",
            cleanup_policy="retain_for_review",
            containment_verified=True,
            internal_sandbox_root=sandbox,
            managed_sandbox_root=managed,
            active_workspace_root=active,
        ),
    )
    provider = CodexAppServerProvider(
        status_service=object(),  # type: ignore[arg-type]
        review_service=BridgeDiffService(),
        managed_root=managed,
        approval_broker=AgentApprovalBroker(timeout_seconds=1),
    )
    process = FakeProcess()
    final_message = await provider._run_protocol(process, context)  # type: ignore[arg-type]
    thread_start = process.stdin.payloads[2]
    assert thread_start["method"] == "thread/start"
    assert thread_start["params"]["sandbox"] == "workspace-write"  # type: ignore[index]
    assert process.stdin.payloads[3]["method"] == "turn/start"
    assert final_message == "Sandbox task complete."
    assert provider.final_message(request.run_id) == "Sandbox task complete."

    resumed = FakeProcess()
    await provider._run_protocol(resumed, context)  # type: ignore[arg-type]
    thread_resume = resumed.stdin.payloads[2]
    assert thread_resume["method"] == "thread/resume"
    assert thread_resume["params"]["threadId"] == "thread-001"  # type: ignore[index]


@pytest.mark.asyncio
async def test_codex_detection_is_reused_during_one_run(tmp_path: Path) -> None:
    class StatusService:
        calls = 0

        def status(self) -> CodexAlignedStatus:
            self.calls += 1
            return CodexAlignedStatus(
                codex_installed=True,
                codex_version="codex-cli 0.142.0",
                auth_status="signed_in",
                oauth_bridge_ready=True,
            )

    status_service = StatusService()
    provider = CodexAppServerProvider(
        status_service=status_service,  # type: ignore[arg-type]
        review_service=BridgeDiffService(),
        managed_root=tmp_path,
        approval_broker=AgentApprovalBroker(timeout_seconds=1),
    )

    first = await provider.detect()
    second = await provider.detect()

    assert first is second
    assert first.available is True
    assert status_service.calls == 1


@pytest.mark.asyncio
async def test_coordinator_accepts_a_successful_conversational_run_without_artifacts(tmp_path: Path) -> None:
    class ConversationalCodex(FakeCodexProvider):
        async def start(self, context: BridgeRunContext) -> BridgeRunResult:
            return BridgeRunResult(True, safe_message="Codex completed with a conversational response.", final_status="completed")

        def final_message(self, run_id: str) -> str:
            return "Hello from Codex."

    active = tmp_path / "active"
    active.mkdir()
    state = tmp_path / "state"
    review_service = BridgeDiffService()
    sandbox_service = BridgeSandboxService(state / "bridge-sandboxes")
    provider = ConversationalCodex(review_service, state)
    registry = BridgeProviderRegistry()
    registry.register(provider)
    evidence = ProviderCapabilityRegistry((
        ProviderCapabilityEvidence(
            provider_id="codex",
            display_name="Conversational Codex",
            provider_kind=ProviderKind.LOCAL_SERVER,
            transport_modes=(TransportMode.APP_SERVER,),
            headless_mode_available=True,
            permission_status=CapabilityStatus.PASSED,
            operational_state=ProviderOperationalState.EXPERIMENTAL,
            execution_allowed=True,
            routing_allowed=True,
        ),
    ))
    coordinator = GenericBridgeRunCoordinator(
        registry=registry,
        routing_policy=DefaultDenyBridgeRoutingPolicy(
            global_enabled=True,
            provider_permissions={"codex": True},
            capability_registry=evidence,
            allow_experimental=True,
        ),
        run_store=BridgeRunStore(state / "runs.json"),
        event_transport=InternalBridgeEventTransport(),
        sandbox_lifecycle=ExistingBridgeSandboxLifecycle(sandbox_service),
        artifact_validator=BridgeArtifactValidator(state),
    )
    sandbox = sandbox_service.create_sandbox(run_id="agent-run-chat-001", workspace_root=active)
    request = BridgeRunRequest(
        run_id="agent-run-chat-001",
        provider_id="codex",
        project_id="project-chat-001",
        sandbox_id="sandbox-chat-001",
        instruction="Hi",
        correlation_id="request-chat-001",
    )
    from backend.bridges.generic.models import BridgeSandboxContext

    record = await coordinator.execute(request, BridgeSandboxContext(
        sandbox_id="sandbox-chat-001",
        run_id="agent-run-chat-001",
        project_id="project-chat-001",
        creation_status="ready",
        cleanup_policy="retain_for_review",
        containment_verified=True,
        internal_sandbox_root=sandbox,
        managed_sandbox_root=sandbox_service.sandbox_root,
        active_workspace_root=active,
    ))

    assert record.status.value == "completed"
    assert coordinator.list_run_artifacts(record.run_id) == ()


@pytest.mark.asyncio
async def test_codex_provider_does_not_hang_on_stderr_drain_after_completed_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class BlockingStream:
        async def read(self, size: int) -> bytes:
            await asyncio.Future()
            return b""

    class FakeProcess:
        def __init__(self) -> None:
            self.stderr = BlockingStream()
            self.returncode = None

    class Executable:
        path = "codex"
        prefix_args: tuple[str, ...] = ()

    managed = tmp_path / "managed"
    sandbox = managed / "agent-run-cleanup-001"
    active = tmp_path / "active"
    sandbox.mkdir(parents=True)
    active.mkdir()
    request = BridgeRunRequest(
        run_id="agent-run-cleanup-001",
        provider_id="codex",
        project_id="project-cleanup-001",
        sandbox_id="sandbox-cleanup-001",
        instruction="Hi",
        correlation_id="request-cleanup-001",
    )
    from backend.bridges.generic.models import BridgeSandboxContext

    context = BridgeRunContext(request, BridgeSandboxContext(
        sandbox_id="sandbox-cleanup-001",
        run_id="agent-run-cleanup-001",
        project_id="project-cleanup-001",
        creation_status="ready",
        cleanup_policy="retain_for_review",
        containment_verified=True,
        internal_sandbox_root=sandbox,
        managed_sandbox_root=managed,
        active_workspace_root=active,
    ))
    status_service = type("StatusService", (), {"resolve_executable": lambda self, env: Executable()})()
    provider = CodexAppServerProvider(
        status_service=status_service,  # type: ignore[arg-type]
        review_service=BridgeDiffService(),
        managed_root=managed,
        approval_broker=AgentApprovalBroker(timeout_seconds=1),
        execution_enabled=True,
    )
    process = FakeProcess()
    cleanup_saw_registered_process = False

    async def valid(request: BridgeRunRequest) -> BridgeValidationResult:
        return BridgeValidationResult(True)

    async def run_protocol(process: object, context: BridgeRunContext) -> str:
        return "Hello from Codex."

    async def terminate(process: object) -> None:
        nonlocal cleanup_saw_registered_process
        cleanup_saw_registered_process = context.request.run_id in provider._processes

    async def create_process(*args: object, **kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr(provider, "validate", valid)
    monkeypatch.setattr(provider, "_run_protocol", run_protocol)
    monkeypatch.setattr(provider, "_terminate", terminate)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    result = await asyncio.wait_for(provider.start(context), timeout=1)

    assert result.succeeded is True
    assert provider.final_message(request.run_id) == "Hello from Codex."
    assert cleanup_saw_registered_process is True
    assert request.run_id not in provider._processes
