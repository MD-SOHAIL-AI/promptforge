"""Default-deny routing policy kept separate from provider registration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contracts import BridgeProvider
from .errors import BridgeErrorCode
from .models import BridgeRunRequest, BridgeSandboxContext
from .provider_capabilities import (
    CapabilityStatus,
    ProviderCapabilityRegistry,
    ProviderOperationalState,
    default_provider_capability_registry,
)


class ProviderStrategyFailure(str, Enum):
    LOCAL_CLI_PROVIDERS_PAUSED = "LOCAL_CLI_PROVIDERS_PAUSED"
    PROVIDER_RUNTIME_NOT_READY = "PROVIDER_RUNTIME_NOT_READY"
    PROVIDER_REQUIRES_FORGEX_TOOL_RUNTIME = "PROVIDER_REQUIRES_FORGEX_TOOL_RUNTIME"
    PROVIDER_API_DESIGN_ONLY = "PROVIDER_API_DESIGN_ONLY"
    PROVIDER_NOT_ROUTEABLE = "PROVIDER_NOT_ROUTEABLE"
    PROVIDER_NOT_REVIEW_ELIGIBLE = "PROVIDER_NOT_REVIEW_ELIGIBLE"
    PROVIDER_PAUSED = "PROVIDER_PAUSED"
    PROVIDER_DISABLED = "PROVIDER_DISABLED"
    PROVIDER_NATIVE_WRITE_UNPROVEN = "PROVIDER_NATIVE_WRITE_UNPROVEN"
    PROVIDER_PERMISSION_BLOCKED = "PROVIDER_PERMISSION_BLOCKED"
    PROVIDER_ARTIFACT_UNRELIABLE = "PROVIDER_ARTIFACT_UNRELIABLE"
    PROVIDER_HEADLESS_UNSUPPORTED = "PROVIDER_HEADLESS_UNSUPPORTED"
    PROVIDER_STRATEGY_DECISION_REQUIRED = "PROVIDER_STRATEGY_DECISION_REQUIRED"


@dataclass(frozen=True, slots=True)
class BridgeRouteDecision:
    allowed: bool
    failure_code: str | None = None
    safe_message: str | None = None
    classification: str | None = None


class DefaultDenyBridgeRoutingPolicy:
    """Immutable policy input; requests cannot modify routing permissions."""

    def __init__(
        self,
        *,
        global_enabled: bool = False,
        provider_permissions: dict[str, bool] | None = None,
        capability_registry: ProviderCapabilityRegistry | None = None,
        allow_experimental: bool = False,
    ) -> None:
        self._global_enabled = bool(global_enabled)
        self._provider_permissions = dict(provider_permissions or {})
        self._capability_registry = capability_registry or default_provider_capability_registry()
        self._allow_experimental = bool(allow_experimental)

    @property
    def global_enabled(self) -> bool:
        return self._global_enabled

    def provider_enabled(self, provider_id: str) -> bool:
        return self._provider_permissions.get(provider_id, False)

    def evaluate(
        self,
        *,
        provider: BridgeProvider | None,
        request: BridgeRunRequest,
        sandbox: BridgeSandboxContext,
    ) -> BridgeRouteDecision:
        if provider is None:
            return BridgeRouteDecision(False, BridgeErrorCode.PROVIDER_UNAVAILABLE.value, "Provider is not registered.")
        if not self._global_enabled or not self.provider_enabled(request.provider_id):
            return BridgeRouteDecision(False, BridgeErrorCode.PROVIDER_DISABLED.value, "Generic bridge routing is disabled.")
        strategy = self._capability_registry.get(request.provider_id)
        if strategy is None:
            return BridgeRouteDecision(
                False,
                BridgeErrorCode.PROVIDER_STRATEGY_DECISION_REQUIRED.value,
                "Provider strategy evidence is required.",
                ProviderStrategyFailure.PROVIDER_STRATEGY_DECISION_REQUIRED.value,
            )
        if strategy.operational_state is ProviderOperationalState.PAUSED:
            return BridgeRouteDecision(
                False,
                BridgeErrorCode.PROVIDER_PAUSED.value,
                "The provider is paused by evidence policy.",
                ProviderStrategyFailure.PROVIDER_PAUSED.value,
            )
        if strategy.operational_state in {ProviderOperationalState.DISABLED, ProviderOperationalState.REFERENCE_ONLY}:
            return BridgeRouteDecision(
                False,
                BridgeErrorCode.PROVIDER_DISABLED.value,
                "The provider is disabled by strategy policy.",
                ProviderStrategyFailure.PROVIDER_DISABLED.value,
            )
        if not strategy.execution_allowed or not strategy.routing_allowed:
            return BridgeRouteDecision(
                False,
                BridgeErrorCode.PROVIDER_NOT_ROUTEABLE.value,
                "The provider is not routeable.",
                ProviderStrategyFailure.PROVIDER_NOT_ROUTEABLE.value,
            )
        if strategy.permission_status is CapabilityStatus.BLOCKED:
            return BridgeRouteDecision(
                False,
                BridgeErrorCode.PROVIDER_PERMISSION_BLOCKED.value,
                "The provider permission boundary is blocked.",
                ProviderStrategyFailure.PROVIDER_PERMISSION_BLOCKED.value,
            )
        if not strategy.headless_mode_available:
            return BridgeRouteDecision(
                False,
                BridgeErrorCode.PROVIDER_HEADLESS_UNSUPPORTED.value,
                "A supported headless mode has not been established.",
                ProviderStrategyFailure.PROVIDER_HEADLESS_UNSUPPORTED.value,
            )
        experimental_allowed = (
            self._allow_experimental
            and strategy.operational_state is ProviderOperationalState.EXPERIMENTAL
        )
        if not strategy.review_eligible and not experimental_allowed:
            if strategy.native_write_status in {CapabilityStatus.FAILED, CapabilityStatus.BLOCKED, CapabilityStatus.UNKNOWN}:
                classification = ProviderStrategyFailure.PROVIDER_NATIVE_WRITE_UNPROVEN
                code = BridgeErrorCode.PROVIDER_NATIVE_WRITE_UNPROVEN
            elif strategy.scratch_artifact_status is CapabilityStatus.FAILED:
                classification = ProviderStrategyFailure.PROVIDER_ARTIFACT_UNRELIABLE
                code = BridgeErrorCode.PROVIDER_ARTIFACT_UNRELIABLE
            else:
                classification = ProviderStrategyFailure.PROVIDER_NOT_REVIEW_ELIGIBLE
                code = BridgeErrorCode.PROVIDER_NOT_REVIEW_ELIGIBLE
            return BridgeRouteDecision(False, code.value, "Provider review eligibility has not been earned.", classification.value)
        if not strategy.production_eligible and not experimental_allowed:
            return BridgeRouteDecision(
                False,
                BridgeErrorCode.PROVIDER_NOT_REVIEW_ELIGIBLE.value,
                "Provider production eligibility has not been earned.",
                ProviderStrategyFailure.PROVIDER_NOT_REVIEW_ELIGIBLE.value,
            )
        capabilities = provider.capabilities()
        if not capabilities.available:
            return BridgeRouteDecision(False, BridgeErrorCode.PROVIDER_UNAVAILABLE.value, "Provider is unavailable.")
        if not sandbox.containment_verified or not capabilities.sandbox_required:
            return BridgeRouteDecision(False, BridgeErrorCode.SANDBOX_REQUIRED.value, "A verified sandbox is required.")
        supported = {
            "edit_files": capabilities.non_interactive and capabilities.supports_artifacts,
            "artifacts": capabilities.supports_artifacts,
            "structured_output": capabilities.supports_structured_output,
        }.get(request.requested_capability, False)
        if not supported:
            return BridgeRouteDecision(False, BridgeErrorCode.PROVIDER_UNSUPPORTED.value, "Requested capability is unsupported.")
        return BridgeRouteDecision(True)
