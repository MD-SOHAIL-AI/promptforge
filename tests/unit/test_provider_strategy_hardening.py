from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.bridges.generic import (
    BridgeCapabilities,
    BridgeRunRequest,
    BridgeSandboxContext,
    CapabilityStatus,
    DefaultDenyBridgeRoutingPolicy,
    PermissionDecision,
    ProviderCapabilityEvidence,
    ProviderCapabilityRegistry,
    ProviderEvidenceLedger,
    ProviderKind,
    ProviderLifecycle,
    ProviderLifecycleState,
    ProviderOperationalState,
    ProviderPermissionPolicy,
    ProviderRunEvidence,
    ReviewEligibilityEvidence,
    ReviewOutputFile,
    TransportMode,
    default_local_cli_policy,
    default_provider_capability_registry,
    evaluate_review_eligibility,
    native_smoke_policy,
)


class FakeProvider:
    environment_allowlist = frozenset()

    def __init__(self, provider_id: str = "agy") -> None:
        self.provider_id = provider_id

    def capabilities(self) -> BridgeCapabilities:
        return BridgeCapabilities(
            provider_id=self.provider_id,
            display_name="Fake provider",
            available=self.provider_id == "agy",
            non_interactive=True,
            supports_artifacts=True,
        )


def capability(**overrides) -> ProviderCapabilityEvidence:
    values = {
        "provider_id": "agy",
        "display_name": "Fake AGY",
        "provider_kind": ProviderKind.LOCAL_CLI,
        "transport_modes": (TransportMode.CLI_HEADLESS,),
        "detected": True,
        "headless_mode_available": True,
        "auth_status": CapabilityStatus.PASSED,
        "permission_status": CapabilityStatus.PASSED,
        "native_write_status": CapabilityStatus.UNKNOWN,
        "safety_scan_status": CapabilityStatus.UNKNOWN,
        "operational_state": ProviderOperationalState.EXPERIMENTAL,
        "evidence_source": "fake_test_evidence",
    }
    values.update(overrides)
    return ProviderCapabilityEvidence(**values)


def eligible_capability(**overrides) -> ProviderCapabilityEvidence:
    values = {
        "native_write_status": CapabilityStatus.PASSED,
        "safety_scan_status": CapabilityStatus.PASSED,
        "operational_state": ProviderOperationalState.ACTIVE,
    }
    values.update(overrides)
    return capability(**values)


def sandbox(tmp_path: Path) -> BridgeSandboxContext:
    managed = tmp_path / "managed"
    active = tmp_path / "active"
    child = managed / "sandbox"
    child.mkdir(parents=True)
    active.mkdir()
    return BridgeSandboxContext(
        sandbox_id="sandbox-strategy-001",
        run_id="bridge-run-strategy-001",
        project_id="project-strategy-001",
        creation_status="ready",
        cleanup_policy="retain_for_review",
        containment_verified=True,
        internal_sandbox_root=child,
        managed_sandbox_root=managed,
        active_workspace_root=active,
    )


def request(provider_id: str = "agy") -> BridgeRunRequest:
    return BridgeRunRequest(
        run_id="bridge-run-strategy-001",
        provider_id=provider_id,
        project_id="project-strategy-001",
        sandbox_id="sandbox-strategy-001",
        instruction="Fake runtime-only instruction.",
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def route(tmp_path: Path, record: ProviderCapabilityEvidence, provider_id: str = "agy"):
    policy = DefaultDenyBridgeRoutingPolicy(
        global_enabled=True,
        provider_permissions={provider_id: True},
        capability_registry=ProviderCapabilityRegistry((record,)),
    )
    return policy.evaluate(provider=FakeProvider(provider_id), request=request(provider_id), sandbox=sandbox(tmp_path))


def native_review(record: ProviderCapabilityEvidence, **overrides):
    values = {
        "classification": "PROVIDER_NATIVE_WRITE_PASS",
        "output_mode": "native_write",
        "changed_files": (ReviewOutputFile("EXPECTED.txt"),),
        "expected_files": ("EXPECTED.txt",),
        "active_workspace_unchanged": True,
        "marker_unchanged": True,
    }
    values.update(overrides)
    return evaluate_review_eligibility(record, ReviewEligibilityEvidence(**values))


def test_provider_installed_but_not_review_eligible() -> None:
    assert capability(detected=True).review_eligible is False


def test_provider_headless_mode_found_but_not_review_eligible() -> None:
    assert capability(headless_mode_available=True).review_eligible is False


def test_agy_paused_blocks_routing(tmp_path: Path) -> None:
    record = default_provider_capability_registry().get("agy")
    assert record is not None
    decision = route(tmp_path, record)
    assert not decision.allowed and decision.classification == "PROVIDER_PAUSED"


def test_codex_paused_blocks_routing(tmp_path: Path) -> None:
    record = default_provider_capability_registry().get("codex_bridge")
    assert record is not None
    decision = route(tmp_path, record, "codex_bridge")
    assert not decision.allowed and decision.classification == "PROVIDER_PAUSED"


def test_opencode_reference_only_cannot_route(tmp_path: Path) -> None:
    record = default_provider_capability_registry().get("opencode_bridge")
    assert record is not None
    decision = route(tmp_path, record, "opencode_bridge")
    assert not decision.allowed and decision.classification == "PROVIDER_DISABLED"


def test_openai_api_spike_is_disabled_and_cannot_route(tmp_path: Path) -> None:
    del tmp_path
    record = default_provider_capability_registry().get("openai_api")
    assert record is not None
    assert record.provider_kind is ProviderKind.API
    assert not record.execution_allowed and not record.routing_allowed and not record.production_eligible


def test_disabled_provider_cannot_route(tmp_path: Path) -> None:
    record = capability(operational_state=ProviderOperationalState.DISABLED)
    decision = route(tmp_path, record)
    assert not decision.allowed and decision.classification == "PROVIDER_DISABLED"


def test_native_write_failed_blocks_review() -> None:
    assert native_review(capability(native_write_status=CapabilityStatus.FAILED)).eligible is False


def test_permission_blocked_blocks_review() -> None:
    record = capability(permission_status=CapabilityStatus.BLOCKED, native_write_status=CapabilityStatus.BLOCKED)
    assert native_review(record).eligible is False


def test_artifact_missing_blocks_review() -> None:
    record = capability(provider_kind=ProviderKind.MANUAL_ARTIFACT, transport_modes=(TransportMode.MANUAL_IMPORT,))
    decision = evaluate_review_eligibility(
        record,
        ReviewEligibilityEvidence("SAFE_ARTIFACT_IMPORT_PASS", "artifact_import", (), ("artifact.txt",), True, True),
    )
    assert decision.eligible is False


def test_exact_native_write_pass_enables_review_eligibility() -> None:
    assert eligible_capability().review_eligible is True
    assert native_review(eligible_capability()).eligible is True


def test_exact_artifact_import_pass_enables_artifact_review() -> None:
    record = capability(
        provider_kind=ProviderKind.MANUAL_ARTIFACT,
        transport_modes=(TransportMode.MANUAL_IMPORT,),
        scratch_artifact_status=CapabilityStatus.PASSED,
    )
    evidence = ReviewEligibilityEvidence(
        "PROVIDER_ARTIFACT_IMPORT_PASS", "artifact_import", (ReviewOutputFile("artifact.txt"),), ("artifact.txt",), True, True
    )
    assert record.review_eligible and evaluate_review_eligibility(record, evidence).eligible


def test_provider_lifecycle_valid_transitions() -> None:
    lifecycle = ProviderLifecycle("fake")
    for state in (
        ProviderLifecycleState.DETECTED,
        ProviderLifecycleState.HELP_INSPECTED,
        ProviderLifecycleState.HEADLESS_MODE_FOUND,
        ProviderLifecycleState.AUTH_READY,
        ProviderLifecycleState.NATIVE_WRITE_TESTED,
        ProviderLifecycleState.NATIVE_WRITE_PASSED,
        ProviderLifecycleState.REVIEW_ELIGIBLE,
        ProviderLifecycleState.HARDENING_ELIGIBLE,
        ProviderLifecycleState.PRODUCTION_CANDIDATE,
    ):
        lifecycle.transition(state)
    assert lifecycle.state is ProviderLifecycleState.PRODUCTION_CANDIDATE


def test_provider_lifecycle_invalid_transition_rejected() -> None:
    with pytest.raises(ValueError, match="invalid_transition"):
        ProviderLifecycle("fake").transition(ProviderLifecycleState.REVIEW_ELIGIBLE)


@pytest.mark.parametrize("dimension", ["shell", "network", "write_external", "modify_active_workspace"])
def test_default_permission_policy_denies_unsafe_dimension(dimension: str) -> None:
    assert getattr(default_local_cli_policy(), dimension) is PermissionDecision.DENY


def test_native_smoke_policy_allows_only_managed_sandbox_write() -> None:
    policy = native_smoke_policy(managed_sandbox_verified=True)
    assert policy.write_sandbox is PermissionDecision.ALLOW
    assert policy.shell is PermissionDecision.DENY
    assert policy.apply_patch is PermissionDecision.DENY
    with pytest.raises(ValueError, match="managed_sandbox"):
        native_smoke_policy(managed_sandbox_verified=False)


def evidence(**overrides) -> ProviderRunEvidence:
    values = {
        "run_id": "evidence-run-001",
        "provider_id": "agy",
        "provider_version": "fake",
        "transport_mode": "cli_headless",
        "sandbox_id": "sandbox-001",
        "policy_id": "policy-001",
        "confirmation_flag_present": True,
        "feature_flags_present": True,
        "execution_attempted": False,
        "execution_count": 0,
        "created_file_count": 0,
        "modified_file_count": 0,
        "deleted_file_count": 0,
        "active_workspace_unchanged": True,
        "marker_unchanged": True,
        "classification": "PROVIDER_PAUSED",
        "safe_summary": "Provider remained paused.",
    }
    values.update(overrides)
    return ProviderRunEvidence(**values)


def test_evidence_ledger_stores_no_raw_prompt_or_output_and_zero_credential_reads() -> None:
    ledger = ProviderEvidenceLedger()
    ledger.record(evidence())
    stored = ledger.get("evidence-run-001").to_safe_dict()
    assert stored["raw_prompt_persisted"] is False
    assert stored["raw_output_persisted"] is False
    assert stored["credential_files_read"] is False
    assert "stdout" not in stored and "stderr" not in stored and "prompt" not in stored


@pytest.mark.parametrize("field", ["raw_prompt_persisted", "raw_output_persisted", "credential_files_read", "apply_run", "build_run", "flash_run"])
def test_evidence_ledger_rejects_forbidden_sensitive_or_authority_field(field: str) -> None:
    with pytest.raises(ValueError):
        evidence(**{field: True})


def test_review_eligibility_rejects_extra_changes() -> None:
    assert not native_review(eligible_capability(), changed_files=(ReviewOutputFile("EXPECTED.txt"), ReviewOutputFile("extra.txt"))).eligible


def test_review_eligibility_rejects_symlink_or_reparse_output() -> None:
    assert not native_review(eligible_capability(), changed_files=(ReviewOutputFile("EXPECTED.txt", True),)).eligible


@pytest.mark.parametrize("unsafe", ["C:/absolute.txt", "/absolute.txt", "../parent.txt", "safe/../../parent.txt"])
def test_review_eligibility_rejects_absolute_and_parent_traversal(unsafe: str) -> None:
    assert not native_review(eligible_capability(), changed_files=(ReviewOutputFile(unsafe),), expected_files=(unsafe,)).eligible


def test_review_eligibility_requires_unchanged_active_workspace_and_marker() -> None:
    assert not native_review(eligible_capability(), active_workspace_unchanged=False).eligible
    assert not native_review(eligible_capability(), marker_unchanged=False).eligible


def test_local_cli_production_eligibility_is_forbidden() -> None:
    with pytest.raises(ValueError, match="local_cli"):
        eligible_capability(production_eligible=True)


def test_installed_headless_provider_cannot_route_without_native_evidence(tmp_path: Path) -> None:
    decision = route(tmp_path, capability())
    assert not decision.allowed and decision.classification == "PROVIDER_NATIVE_WRITE_UNPROVEN"


def test_production_route_requires_production_eligibility(tmp_path: Path) -> None:
    decision = route(tmp_path, eligible_capability())
    assert not decision.allowed and decision.classification == "PROVIDER_NOT_REVIEW_ELIGIBLE"


def test_fake_production_eligible_non_cli_provider_can_route(tmp_path: Path) -> None:
    record = eligible_capability(provider_kind=ProviderKind.LOCAL_SERVER, production_eligible=True)
    assert route(tmp_path, record).allowed
