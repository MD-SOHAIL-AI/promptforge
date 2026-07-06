"""Evidence-backed provider capabilities; detection alone grants no authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable


class ProviderKind(str, Enum):
    LOCAL_CLI = "local_cli"
    LOCAL_SERVER = "local_server"
    API = "api"
    MANUAL_ARTIFACT = "manual_artifact"
    DISABLED = "disabled"


class TransportMode(str, Enum):
    CLI_HEADLESS = "cli_headless"
    LOCAL_HTTP_SERVER = "local_http_server"
    SDK = "sdk"
    APP_SERVER = "app_server"
    API_REMOTE = "api_remote"
    MANUAL_IMPORT = "manual_import"
    NONE = "none"


class CapabilityStatus(str, Enum):
    UNKNOWN = "unknown"
    NOT_SUPPORTED = "not_supported"
    BLOCKED = "blocked"
    FAILED = "failed"
    PASSED = "passed"
    PAUSED = "paused"
    DISABLED = "disabled"


class ProviderOperationalState(str, Enum):
    ACTIVE = "active"
    EXPERIMENTAL = "experimental"
    PAUSED = "paused"
    DISABLED = "disabled"
    REFERENCE_ONLY = "reference_only"
    CANDIDATE = "candidate"


@dataclass(frozen=True, slots=True)
class ProviderCapabilityEvidence:
    provider_id: str
    display_name: str
    provider_kind: ProviderKind
    transport_modes: tuple[TransportMode, ...] = (TransportMode.NONE,)
    detected: bool = False
    version: str | None = None
    headless_mode_available: bool = False
    auth_status: CapabilityStatus = CapabilityStatus.UNKNOWN
    permission_status: CapabilityStatus = CapabilityStatus.UNKNOWN
    workspace_write_status: CapabilityStatus = CapabilityStatus.UNKNOWN
    scratch_artifact_status: CapabilityStatus = CapabilityStatus.NOT_SUPPORTED
    server_api_status: CapabilityStatus = CapabilityStatus.NOT_SUPPORTED
    native_write_status: CapabilityStatus = CapabilityStatus.UNKNOWN
    safety_scan_status: CapabilityStatus = CapabilityStatus.UNKNOWN
    operational_state: ProviderOperationalState = ProviderOperationalState.DISABLED
    execution_allowed: bool = True
    routing_allowed: bool = True
    production_eligible: bool = False
    last_verified_at: datetime | None = None
    evidence_source: str = "unverified"
    safe_failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_id or not self.provider_id.replace("_", "").isalnum():
            raise ValueError("provider_id_invalid")
        if not self.transport_modes:
            raise ValueError("transport_mode_required")
        if self.last_verified_at is not None and self.last_verified_at.tzinfo is None:
            raise ValueError("last_verified_at_must_be_timezone_aware")
        if self.provider_kind is ProviderKind.LOCAL_CLI and self.production_eligible:
            raise ValueError("local_cli_production_eligibility_forbidden")
        if self.production_eligible and not self.hardening_eligible:
            raise ValueError("production_requires_hardening")

    @property
    def safe_artifact_import_status(self) -> CapabilityStatus:
        return self.scratch_artifact_status

    @property
    def review_eligible(self) -> bool:
        evidence_passed = (
            self.native_write_status is CapabilityStatus.PASSED
            or self.safe_artifact_import_status is CapabilityStatus.PASSED
        )
        return evidence_passed and self.operational_state not in {
            ProviderOperationalState.PAUSED,
            ProviderOperationalState.DISABLED,
            ProviderOperationalState.REFERENCE_ONLY,
        }

    @property
    def hardening_eligible(self) -> bool:
        return self.review_eligible and self.safety_scan_status is CapabilityStatus.PASSED

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "provider_kind": self.provider_kind.value,
            "transport_modes": [item.value for item in self.transport_modes],
            "detected": self.detected,
            "version": self.version,
            "headless_mode_available": self.headless_mode_available,
            "auth_status": self.auth_status.value,
            "permission_status": self.permission_status.value,
            "workspace_write_status": self.workspace_write_status.value,
            "scratch_artifact_status": self.scratch_artifact_status.value,
            "server_api_status": self.server_api_status.value,
            "native_write_status": self.native_write_status.value,
            "review_eligible": self.review_eligible,
            "hardening_eligible": self.hardening_eligible,
            "production_eligible": self.production_eligible,
            "last_verified_at": self.last_verified_at.isoformat() if self.last_verified_at else None,
            "evidence_source": self.evidence_source,
            "safe_failure_reason": self.safe_failure_reason,
            "operational_state": self.operational_state.value,
            "execution_allowed": self.execution_allowed,
            "routing_allowed": self.routing_allowed,
        }


class ProviderCapabilityRegistry:
    """Registration is evidence storage only; it never detects or executes a provider."""

    def __init__(self, records: Iterable[ProviderCapabilityEvidence] = ()) -> None:
        self._records: dict[str, ProviderCapabilityEvidence] = {}
        for record in records:
            self.register(record)

    def register(self, record: ProviderCapabilityEvidence) -> None:
        if record.provider_id in self._records:
            raise ValueError("provider_capability_duplicate")
        self._records[record.provider_id] = record

    def get(self, provider_id: str) -> ProviderCapabilityEvidence | None:
        return self._records.get(provider_id)

    def list(self) -> tuple[ProviderCapabilityEvidence, ...]:
        return tuple(self._records[key] for key in sorted(self._records))


def default_provider_capability_registry() -> ProviderCapabilityRegistry:
    verified = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return ProviderCapabilityRegistry(
        (
            ProviderCapabilityEvidence(
                provider_id="agy",
                display_name="Google Antigravity / AGY CLI",
                provider_kind=ProviderKind.LOCAL_CLI,
                transport_modes=(TransportMode.CLI_HEADLESS,),
                detected=True,
                version="1.0.14",
                headless_mode_available=True,
                auth_status=CapabilityStatus.PASSED,
                permission_status=CapabilityStatus.FAILED,
                workspace_write_status=CapabilityStatus.FAILED,
                scratch_artifact_status=CapabilityStatus.FAILED,
                native_write_status=CapabilityStatus.FAILED,
                operational_state=ProviderOperationalState.PAUSED,
                execution_allowed=False,
                routing_allowed=False,
                last_verified_at=verified,
                evidence_source="phase_2_5_8_6_5",
                safe_failure_reason="unreliable_workspace_and_exact_scratch_output",
            ),
            ProviderCapabilityEvidence(
                provider_id="codex",
                display_name="OpenAI Codex App Server",
                provider_kind=ProviderKind.LOCAL_SERVER,
                transport_modes=(TransportMode.APP_SERVER,),
                detected=False,
                headless_mode_available=True,
                operational_state=ProviderOperationalState.EXPERIMENTAL,
                execution_allowed=False,
                routing_allowed=False,
                production_eligible=False,
                evidence_source="unified_agent_runtime_default_disabled",
                safe_failure_reason="explicit_feature_flag_and_live_validation_required",
            ),
            ProviderCapabilityEvidence(
                provider_id="codex_bridge",
                display_name="OpenAI Codex CLI",
                provider_kind=ProviderKind.LOCAL_CLI,
                transport_modes=(TransportMode.CLI_HEADLESS,),
                detected=True,
                version="codex-cli 0.142.5",
                headless_mode_available=True,
                permission_status=CapabilityStatus.BLOCKED,
                workspace_write_status=CapabilityStatus.BLOCKED,
                native_write_status=CapabilityStatus.BLOCKED,
                operational_state=ProviderOperationalState.PAUSED,
                execution_allowed=False,
                routing_allowed=False,
                last_verified_at=verified,
                evidence_source="phase_2_5_8_7",
                safe_failure_reason="permission_workspace_blocked_under_safe_policy",
            ),
            ProviderCapabilityEvidence(
                provider_id="claude_code_bridge",
                display_name="Claude Code",
                provider_kind=ProviderKind.DISABLED,
                operational_state=ProviderOperationalState.DISABLED,
                execution_allowed=False,
                routing_allowed=False,
                evidence_source="not_investigated",
            ),
            ProviderCapabilityEvidence(
                provider_id="opencode_bridge",
                display_name="OpenCode (reference only)",
                provider_kind=ProviderKind.DISABLED,
                operational_state=ProviderOperationalState.REFERENCE_ONLY,
                execution_allowed=False,
                routing_allowed=False,
                evidence_source="architecture_reference_only",
                safe_failure_reason="provider_integration_forbidden",
            ),
            ProviderCapabilityEvidence(
                provider_id="openai_api",
                display_name="OpenAI API provider spike",
                provider_kind=ProviderKind.API,
                transport_modes=(TransportMode.API_REMOTE,),
                headless_mode_available=True,
                server_api_status=CapabilityStatus.UNKNOWN,
                operational_state=ProviderOperationalState.DISABLED,
                execution_allowed=False,
                routing_allowed=False,
                evidence_source="phase_2_5_8_10_spike",
                safe_failure_reason="explicit_qa_flags_and_confirmation_required",
            ),
        )
    )
