"""Canonical provider-neutral contracts for ForgeX generation."""

from .contracts import (
    AuthType,
    ForgeXRunSummary,
    GenerationStatus,
    ProviderErrorCode,
    ProviderState,
    ProviderType,
    StructuredRunEvent,
    WorkspaceMode,
)
from .catalog import ProviderCatalog, ProviderDescriptor
from .validation import ArtifactValidationResult, GeneratedArtifactValidator, detect_workspace_mode
from .errors import actionable_message, classify_cli_failure, normalize_provider_error
from .summary_store import ProviderRunSummaryStore

__all__ = [
    "ArtifactValidationResult",
    "AuthType",
    "ForgeXRunSummary",
    "GeneratedArtifactValidator",
    "GenerationStatus",
    "ProviderCatalog",
    "ProviderDescriptor",
    "ProviderErrorCode",
    "ProviderState",
    "ProviderType",
    "StructuredRunEvent",
    "WorkspaceMode",
    "detect_workspace_mode",
    "actionable_message",
    "normalize_provider_error",
    "classify_cli_failure",
    "ProviderRunSummaryStore",
]
