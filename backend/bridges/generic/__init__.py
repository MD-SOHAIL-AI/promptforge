"""Provider-neutral bridge language; generic routing remains disabled."""

from .contracts import BridgeProvider
from .coordinator import BridgeArtifactValidator, ExistingBridgeSandboxLifecycle, GenericBridgeRunCoordinator
from .errors import BridgeDomainError, BridgeErrorCode, normalize_bridge_error
from .models import (
    BridgeArtifactReference,
    BridgeArtifactType,
    BridgeCancellationDisposition,
    BridgeCancellationEscalation,
    BridgeCancellationResult,
    BridgeCapabilities,
    BridgeDetectionResult,
    BridgeEventType,
    BridgeRunContext,
    BridgeRunEvent,
    BridgeRunRequest,
    BridgeRunResult,
    BridgeSandboxContext,
    BridgeValidationResult,
)
from .persistence import BridgeRunStore
from .registry import BridgeProviderRegistry
from .routing import BridgeRouteDecision, DefaultDenyBridgeRoutingPolicy
from .routing import ProviderStrategyFailure
from .provider_capabilities import (
    CapabilityStatus,
    ProviderCapabilityEvidence,
    ProviderCapabilityRegistry,
    ProviderKind,
    ProviderOperationalState,
    TransportMode,
    default_provider_capability_registry,
)
from .provider_lifecycle import ALLOWED_PROVIDER_TRANSITIONS, ProviderLifecycle, ProviderLifecycleState
from .permission_policy import PermissionDecision, ProviderPermissionPolicy, default_local_cli_policy, native_smoke_policy
from .evidence_ledger import ProviderEvidenceLedger, ProviderRunEvidence
from .review_eligibility import (
    ReviewEligibilityDecision,
    ReviewEligibilityEvidence,
    ReviewOutputFile,
    evaluate_review_eligibility,
)
from .event_transport import BridgeEventSubscription, InternalBridgeEventTransport
from .states import ALLOWED_TRANSITIONS, TERMINAL_STATES, BridgeRunRecord, BridgeRunStateMachine, BridgeRunStatus

__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATES",
    "BridgeArtifactReference",
    "BridgeArtifactValidator",
    "BridgeArtifactType",
    "BridgeCancellationDisposition",
    "BridgeCancellationEscalation",
    "BridgeCancellationResult",
    "BridgeCapabilities",
    "BridgeDetectionResult",
    "BridgeDomainError",
    "BridgeErrorCode",
    "BridgeEventType",
    "BridgeEventSubscription",
    "BridgeProvider",
    "BridgeProviderRegistry",
    "BridgeRouteDecision",
    "BridgeRunContext",
    "BridgeRunEvent",
    "BridgeRunRecord",
    "BridgeRunRequest",
    "BridgeRunResult",
    "BridgeRunStateMachine",
    "BridgeRunStatus",
    "BridgeRunStore",
    "BridgeSandboxContext",
    "BridgeValidationResult",
    "DefaultDenyBridgeRoutingPolicy",
    "ProviderStrategyFailure",
    "CapabilityStatus",
    "ProviderCapabilityEvidence",
    "ProviderCapabilityRegistry",
    "ProviderKind",
    "ProviderOperationalState",
    "TransportMode",
    "default_provider_capability_registry",
    "ALLOWED_PROVIDER_TRANSITIONS",
    "ProviderLifecycle",
    "ProviderLifecycleState",
    "PermissionDecision",
    "ProviderPermissionPolicy",
    "default_local_cli_policy",
    "native_smoke_policy",
    "ProviderEvidenceLedger",
    "ProviderRunEvidence",
    "ReviewEligibilityDecision",
    "ReviewEligibilityEvidence",
    "ReviewOutputFile",
    "evaluate_review_eligibility",
    "ExistingBridgeSandboxLifecycle",
    "GenericBridgeRunCoordinator",
    "InternalBridgeEventTransport",
    "normalize_bridge_error",
]
