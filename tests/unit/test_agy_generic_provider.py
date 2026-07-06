from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.bridges.generic import (
    BridgeCancellationDisposition,
    BridgeDomainError,
    BridgeErrorCode,
    BridgeEventType,
    BridgeProvider,
    BridgeProviderRegistry,
    BridgeRunContext,
    BridgeRunRequest,
    BridgeRunStatus,
    BridgeSandboxContext,
)
from backend.bridges.models import BridgeDetectionResult as LegacyDetectionResult
from backend.bridges.providers.agy_generic import (
    AGYBridgeProvider,
    AGY_GENERIC_PROVIDER_ID,
    LEGACY_AGY_STATE_MAP,
    map_legacy_agy_state,
)
from backend.bridges.providers.antigravity_runner import AntigravityRunnerError, SAFE_ENV_NAMES
from backend.bridges.run_models import BridgeSandboxRun


NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
RAW_INSTRUCTION = "change the readme; token=do-not-persist"


def legacy_detection(
    *,
    installed: bool = True,
    auth_status: str = "authenticated",
    executable_path: str | None = "C:/Users/private/tools/agy.exe",
) -> LegacyDetectionResult:
    return LegacyDetectionResult(
        provider_id="antigravity_cli_bridge",
        display_name="Google Antigravity / AGY CLI",
        installed=installed,
        version="agy 1.2.3" if installed else None,
        executable_path=executable_path,
        auth_status=auth_status,  # type: ignore[arg-type]
        auth_message="Authenticated." if auth_status == "authenticated" else "Authentication is unavailable.",
    )


class FakeRunner:
    def __init__(
        self,
        statuses: list[str] | None = None,
        *,
        enabled: bool = True,
        review_id: str | None = "bridge-review-001",
        start_error: BaseException | None = None,
    ) -> None:
        self.statuses = statuses or ["review_ready"]
        self.enabled = enabled
        self.review_id = review_id
        self.start_error = start_error
        self.start_calls: list[dict[str, object]] = []
        self.cancel_calls: list[str] = []
        self._index = 0
        self._run: BridgeSandboxRun | None = None

    def is_enabled(self) -> bool:
        return self.enabled

    def start_run(self, *, workspace_root: str | Path, prompt: str, timeout_seconds: int) -> BridgeSandboxRun:
        if self.start_error:
            raise self.start_error
        self.start_calls.append(
            {"workspace_root": Path(workspace_root), "prompt": prompt, "timeout_seconds": timeout_seconds}
        )
        self._run = self._make_run(self.statuses[0])
        self._index = 1
        return self._run

    def start_run_in_sandbox(
        self, *, run_id: str, sandbox_root: str | Path, prompt: str, timeout_seconds: int, baseline_root: str | Path | None = None
    ) -> BridgeSandboxRun:
        run = self.start_run(workspace_root=sandbox_root, prompt=prompt, timeout_seconds=timeout_seconds)
        run.run_id = run_id
        return run

    def get_run(self, run_id: str) -> BridgeSandboxRun:
        if self._run is None or self._run.run_id != run_id:
            raise AntigravityRunnerError("AGY sandbox run was not found.")
        if self._index < len(self.statuses):
            self._run.status = self.statuses[self._index]  # type: ignore[assignment]
            self._index += 1
            if self._run.status in {"review_ready", "completed"}:
                self._run.review_id = self.review_id
                self._run.changed_file_count = 1
                self._run.completed_at = NOW + timedelta(seconds=2)
            elif self._run.status in {"failed", "failed_timeout", "cancelled", "blocked", "interrupted"}:
                self._run.completed_at = NOW + timedelta(seconds=2)
        return self._run

    def cancel_run(self, run_id: str) -> BridgeSandboxRun:
        if self._run is None or self._run.run_id != run_id:
            raise AntigravityRunnerError("AGY sandbox run was not found.")
        self.cancel_calls.append(run_id)
        self._run.status = "cancelled"
        self._run.completed_at = NOW + timedelta(seconds=1)
        return self._run

    def _make_run(self, status: str) -> BridgeSandboxRun:
        run = BridgeSandboxRun(
            provider_id="antigravity_cli_bridge",
            workspace_root_hash="a" * 64,
            sandbox_root="internal-only",
            status=status,  # type: ignore[arg-type]
            run_id="bridge-run-legacy-001",
            started_at=NOW,
        )
        if status in {"review_ready", "completed"}:
            run.review_id = self.review_id
            run.changed_file_count = 1
            run.completed_at = NOW + timedelta(seconds=2)
        elif status in {"failed", "failed_timeout", "cancelled", "blocked", "interrupted"}:
            run.completed_at = NOW + timedelta(seconds=2)
        return run


class BlockingCancelRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__(["running"])
        self.cancel_entered = threading.Event()
        self.cancel_release = threading.Event()

    def cancel_run(self, run_id: str) -> BridgeSandboxRun:
        self.cancel_entered.set()
        self.cancel_release.wait(timeout=2)
        return super().cancel_run(run_id)


class RejectCancelRunner(FakeRunner):
    def cancel_run(self, run_id: str) -> BridgeSandboxRun:
        raise RuntimeError("internal cancellation detail")


def request(**overrides) -> BridgeRunRequest:
    values = {
        "run_id": "bridge-run-generic-001",
        "provider_id": "agy",
        "project_id": "project-001",
        "sandbox_id": "sandbox-001",
        "instruction": RAW_INSTRUCTION,
        "timeout_seconds": 321,
        "created_at": NOW,
        "correlation_id": "correlation-001",
    }
    values.update(overrides)
    return BridgeRunRequest(**values)


def context(tmp_path: Path, **request_overrides) -> BridgeRunContext:
    active = tmp_path / "active" / "project-001"
    managed = tmp_path / "managed-sandboxes"
    internal = managed / "sandbox-001"
    active.mkdir(parents=True, exist_ok=True)
    internal.mkdir(parents=True, exist_ok=True)
    (internal / "README.md").write_text("base\n", encoding="utf-8")
    run_request = request(**request_overrides)
    sandbox = BridgeSandboxContext(
        sandbox_id=run_request.sandbox_id,
        run_id=run_request.run_id,
        project_id=run_request.project_id,
        creation_status="ready",
        cleanup_policy="retain_for_review",
        containment_verified=True,
        internal_sandbox_root=internal,
        managed_sandbox_root=managed,
        active_workspace_root=active,
    )
    return BridgeRunContext(run_request, sandbox)


def provider(
    tmp_path: Path,
    runner: FakeRunner | None = None,
    *,
    detector=lambda: legacy_detection(),
    execution_enabled: bool = True,
) -> AGYBridgeProvider:
    return AGYBridgeProvider(
        detector=detector,
        runner=runner or FakeRunner(),
        artifact_root=tmp_path / "state",
        execution_enabled=execution_enabled,
        poll_interval_seconds=0.001,
    )


def test_agy_generic_adapter_contract_and_stable_provider_id(tmp_path: Path) -> None:
    adapter = provider(tmp_path)
    assert isinstance(adapter, BridgeProvider)
    assert adapter.provider_id == AGY_GENERIC_PROVIDER_ID == "agy"


def test_agy_generic_detection_delegates_and_omits_sensitive_fields(tmp_path: Path) -> None:
    calls = 0

    def detect():
        nonlocal calls
        calls += 1
        return legacy_detection()

    result = asyncio.run(provider(tmp_path, detector=detect).detect())
    payload = json.dumps(result.to_public_dict())
    assert calls == 1
    assert result.installed is True and result.available is True
    assert result.provider_version == "agy 1.2.3"
    assert result.authentication_status == "authenticated"
    assert "C:/Users/private" not in payload
    assert "executable" not in payload


@pytest.mark.parametrize(
    ("legacy", "installed", "available", "auth", "code"),
    [
        (legacy_detection(), True, True, "authenticated", None),
        (legacy_detection(installed=False, auth_status="not_installed", executable_path=None), False, False, "not_installed", "provider_unavailable"),
        (legacy_detection(auth_status="unauthenticated"), True, True, "unauthenticated", None),
    ],
)
def test_agy_generic_detection_parity(tmp_path: Path, legacy, installed, available, auth, code) -> None:
    result = asyncio.run(provider(tmp_path, detector=lambda: legacy).detect())
    assert (result.installed, result.available, result.authentication_status, result.unavailability_code) == (
        installed,
        available,
        auth,
        code,
    )


def test_agy_generic_detection_failure_is_conservative(tmp_path: Path) -> None:
    def fail():
        raise RuntimeError("token=secret at C:/private/tool")

    result = asyncio.run(provider(tmp_path, detector=fail).detect())
    assert result.available is False
    assert result.capabilities is not None and result.capabilities.available is False
    assert "secret" not in json.dumps(result.to_public_dict())


def test_agy_generic_capabilities_are_truthful_and_conservative(tmp_path: Path) -> None:
    adapter = provider(tmp_path)
    before = adapter.capabilities()
    assert before.available is False and before.sandbox_required is True
    asyncio.run(adapter.detect())
    capabilities = adapter.capabilities()
    assert capabilities.available is True
    assert capabilities.non_interactive is True
    assert capabilities.supports_cancellation is True
    assert capabilities.supports_timeout is True
    assert capabilities.supports_artifacts is True
    assert capabilities.supports_structured_output is True
    assert capabilities.supports_streaming is False
    assert capabilities.supports_resume is False
    assert capabilities.supports_subscription_auth is False


def test_agy_generic_validation_rejects_provider_mismatch_unsupported_capability_and_disabled_adapter(tmp_path: Path) -> None:
    adapter = provider(tmp_path)
    mismatch = asyncio.run(adapter.validate(request(provider_id="codex_bridge")))
    unsupported = asyncio.run(adapter.validate(request(requested_capability="streaming")))
    disabled = asyncio.run(provider(tmp_path, execution_enabled=False).validate(request()))
    assert mismatch.failure_code == "provider_unsupported"
    assert unsupported.failure_code == "provider_unsupported"
    assert disabled.failure_code == "provider_disabled"


def test_agy_generic_missing_sandbox_is_rejected(tmp_path: Path) -> None:
    adapter = provider(tmp_path)
    run_context = context(tmp_path)
    (run_context.sandbox.internal_sandbox_root / "README.md").unlink()
    run_context.sandbox.internal_sandbox_root.rmdir()
    with pytest.raises(BridgeDomainError) as caught:
        adapter.translate_request(run_context)
    assert caught.value.code is BridgeErrorCode.SANDBOX_INVALID


def test_agy_generic_active_workspace_is_rejected_by_sandbox_contract(tmp_path: Path) -> None:
    active = tmp_path / "active"
    active.mkdir()
    with pytest.raises(BridgeDomainError) as caught:
        BridgeSandboxContext(
            sandbox_id="sandbox-001",
            run_id="bridge-run-generic-001",
            project_id="project-001",
            creation_status="ready",
            cleanup_policy="retain_for_review",
            containment_verified=True,
            internal_sandbox_root=active,
            managed_sandbox_root=tmp_path,
            active_workspace_root=active,
        )
    assert caught.value.code is BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED


def test_agy_generic_translation_preserves_runtime_instruction_timeout_and_environment_allowlist(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = provider(tmp_path)
    translated = adapter.translate_request(context(tmp_path))
    assert translated.prompt == RAW_INSTRUCTION
    assert translated.timeout_seconds == 321
    assert translated.workspace_root.name == "sandbox-001"
    assert RAW_INSTRUCTION not in repr(translated)
    assert RAW_INSTRUCTION not in json.dumps(translated.to_safe_metadata())
    assert RAW_INSTRUCTION not in caplog.text
    assert adapter.environment_allowlist == frozenset(SAFE_ENV_NAMES)
    assert not any(marker in name for name in adapter.environment_allowlist for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))


@pytest.mark.parametrize(("legacy", "canonical"), sorted(LEGACY_AGY_STATE_MAP.items()))
def test_agy_generic_maps_every_legacy_state(legacy: str, canonical: BridgeRunStatus) -> None:
    assert map_legacy_agy_state(legacy) is canonical


def test_agy_generic_unknown_state_fails_closed() -> None:
    assert map_legacy_agy_state("future_unknown_state") is BridgeRunStatus.FAILED


def test_agy_generic_success_parity_reuses_review_artifact_and_has_no_apply_authority(tmp_path: Path) -> None:
    fake = FakeRunner(["pending", "running", "review_ready"])
    adapter = provider(tmp_path, fake)
    result = asyncio.run(adapter.start(context(tmp_path)))
    assert result.succeeded is True
    assert result.final_status == "completed"
    assert result.review_id == "bridge-review-001"
    assert result.artifact_ids == ("bridge-review-001",)
    assert result.artifacts[0].pipeline_artifact_id == "bridge-review-001"
    public = json.dumps(result.to_public_dict())
    assert "storage_reference" not in public
    assert "apply" not in public
    assert RAW_INSTRUCTION not in public
    assert fake.start_calls[0]["workspace_root"] == context(tmp_path).sandbox.internal_sandbox_root


@pytest.mark.parametrize(
    ("status", "final_status", "failure_code"),
    [
        ("failed", "failed", "process_failed"),
        ("failed_timeout", "timed_out", "timeout"),
        ("cancelled", "cancelled", "cancelled"),
        ("blocked", "blocked", "provider_disabled"),
        ("interrupted", "interrupted", "internal_error"),
        ("unknown", "failed", "internal_error"),
    ],
)
def test_agy_generic_result_parity(tmp_path: Path, status: str, final_status: str, failure_code: str) -> None:
    result = asyncio.run(provider(tmp_path, FakeRunner([status], review_id=None)).start(context(tmp_path)))
    assert result.succeeded is False
    assert result.final_status == final_status
    assert result.failure_code == failure_code
    assert not result.artifacts


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AntigravityRunnerError("AGY sandbox execution is disabled."), "provider_disabled"),
        (AntigravityRunnerError("AGY CLI was not found on PATH."), "provider_unavailable"),
        (AntigravityRunnerError("Workspace root must be an existing directory."), "sandbox_invalid"),
        (AntigravityRunnerError("Could not start."), "process_start_failed"),
        (RuntimeError("token=secret C:/private"), "internal_error"),
    ],
)
def test_agy_generic_start_error_mapping_never_leaks_raw_error(tmp_path: Path, error: BaseException, code: str) -> None:
    result = asyncio.run(provider(tmp_path, FakeRunner(start_error=error)).start(context(tmp_path)))
    assert result.failure_code == code
    assert "secret" not in json.dumps(result.to_public_dict())
    assert "C:/private" not in json.dumps(result.to_public_dict())


def test_agy_generic_events_are_monotonic_sanitized_and_terminal_once(tmp_path: Path) -> None:
    adapter = provider(tmp_path, FakeRunner(["pending", "running", "review_ready"]))
    result = asyncio.run(adapter.start(context(tmp_path)))
    events = adapter.events_for_run("bridge-run-generic-001")
    assert [item.sequence for item in events] == list(range(1, len(events) + 1))
    assert events[-1].event_type is BridgeEventType.COMPLETED
    assert sum(item.event_type is BridgeEventType.COMPLETED for item in events) == 1
    assert all(RAW_INSTRUCTION not in json.dumps(item.to_dict()) for item in events)
    assert result.succeeded is True


def test_agy_generic_event_messages_use_generic_sanitization(tmp_path: Path) -> None:
    adapter = provider(tmp_path)
    adapter._append_event(
        "bridge-run-generic-001",
        BridgeEventType.WARNING,
        BridgeRunStatus.RUNNING,
        "token=secret at C:\\Users\\private\\workspace",
    )
    message = adapter.events_for_run("bridge-run-generic-001")[0].safe_message or ""
    assert "secret" not in message
    assert "C:\\Users" not in message


def test_agy_generic_artifact_reference_is_contained(tmp_path: Path) -> None:
    adapter = provider(tmp_path, FakeRunner(["review_ready"]))
    result = asyncio.run(adapter.start(context(tmp_path)))
    resolved = result.artifacts[0].resolve_internal(tmp_path / "state")
    assert resolved.is_relative_to((tmp_path / "state").resolve())


def test_agy_generic_cancellation_delegates_and_is_idempotent(tmp_path: Path) -> None:
    async def scenario():
        fake = BlockingCancelRunner()
        adapter = provider(tmp_path, fake)
        start_task = asyncio.create_task(adapter.start(context(tmp_path)))
        for _ in range(100):
            if adapter.legacy_run_id("bridge-run-generic-001"):
                break
            await asyncio.sleep(0.001)
        first_task = asyncio.create_task(adapter.cancel("bridge-run-generic-001"))
        await asyncio.to_thread(fake.cancel_entered.wait, 1)
        repeated = await adapter.cancel("bridge-run-generic-001")
        fake.cancel_release.set()
        first = await first_task
        result = await start_task
        return fake, adapter, first, repeated, result

    fake, adapter, first, repeated, result = asyncio.run(scenario())
    assert first.disposition is BridgeCancellationDisposition.ACCEPTED
    assert first.escalation.value == "process_termination"
    assert repeated.disposition is BridgeCancellationDisposition.ALREADY_REQUESTED
    assert repeated.requested_at == first.requested_at
    assert fake.cancel_calls == ["bridge-run-generic-001"]
    assert result.final_status == "cancelled"
    assert adapter.events_for_run("bridge-run-generic-001")[-1].event_type is BridgeEventType.CANCELLED


def test_agy_generic_late_completion_cannot_overwrite_cancelled(tmp_path: Path) -> None:
    adapter = provider(tmp_path, FakeRunner(["cancelled"]))
    cancelled = asyncio.run(adapter.start(context(tmp_path)))
    legacy = FakeRunner(["review_ready"])._make_run("review_ready")
    late = adapter._map_result("bridge-run-generic-001", legacy)
    events = adapter.events_for_run("bridge-run-generic-001")
    assert cancelled.final_status == "cancelled"
    assert late.final_status == "cancelled"
    assert not any(item.event_type is BridgeEventType.COMPLETED for item in events)


def test_agy_generic_zero_change_maps_to_safe_failure(tmp_path: Path) -> None:
    adapter = provider(tmp_path, FakeRunner(["completed_no_changes"]))

    result = asyncio.run(adapter.start(context(tmp_path)))
    events = adapter.events_for_run("bridge-run-generic-001")

    assert result.succeeded is False
    assert result.final_status == "failed"
    assert result.failure_code == "no_changes_produced"
    assert result.safe_message == "AGY completed but produced no sandbox changes."
    assert events[-1].failure_code == "no_changes_produced"
    assert events[-1].event_type is BridgeEventType.FAILURE


def test_agy_generic_cancel_not_found(tmp_path: Path) -> None:
    result = asyncio.run(provider(tmp_path).cancel("bridge-run-missing"))
    assert result.disposition is BridgeCancellationDisposition.NOT_FOUND


def test_agy_generic_cancel_already_terminal_and_rejected(tmp_path: Path) -> None:
    completed_adapter = provider(tmp_path, FakeRunner(["review_ready"]))
    asyncio.run(completed_adapter.start(context(tmp_path)))
    terminal = asyncio.run(completed_adapter.cancel("bridge-run-generic-001"))
    assert terminal.disposition is BridgeCancellationDisposition.ALREADY_TERMINAL

    rejecting_runner = RejectCancelRunner(["running"])
    rejecting_runner._run = rejecting_runner._make_run("running")
    rejecting_adapter = provider(tmp_path, rejecting_runner)
    rejecting_adapter._generic_to_legacy["bridge-run-generic-001"] = "bridge-run-legacy-001"
    rejected = asyncio.run(rejecting_adapter.cancel("bridge-run-generic-001"))
    assert rejected.disposition is BridgeCancellationDisposition.REJECTED


def test_agy_generic_registry_registration_is_explicit_and_non_routing(tmp_path: Path) -> None:
    detection_calls = 0

    def detect():
        nonlocal detection_calls
        detection_calls += 1
        return legacy_detection()

    registry = BridgeProviderRegistry()
    adapter = provider(tmp_path, detector=detect, execution_enabled=False)
    registry.register(adapter)
    assert registry.get("agy") is adapter
    assert registry.routing_enabled is False
    assert registry.is_routable("agy") is False
    assert detection_calls == 0
    with pytest.raises(BridgeDomainError):
        registry.register(adapter)
