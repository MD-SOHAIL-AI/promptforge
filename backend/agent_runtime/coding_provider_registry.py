"""Contract-only registry and deterministic selection for coding providers."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

from .coding_provider_contracts import (
    CodingContextMode,
    CodingProviderCapability,
    CodingProviderContractError,
    CodingProviderDescriptor,
    CodingProviderExecutionMode,
    CodingProviderFailureCode,
    CodingProviderReadiness,
    CodingProviderSelectionRequest,
    CodingProviderSelectionResult,
    CodingProviderType,
    validate_provider_descriptor,
    validate_provider_id,
)


class CodingProviderRegistryError(ValueError):
    def __init__(self, code: CodingProviderFailureCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class CodingProviderRegistry:
    """Stores descriptors only; it performs no provider execution or live routing."""

    def __init__(self, descriptors: Iterable[CodingProviderDescriptor] = ()) -> None:
        self._descriptors: dict[str, CodingProviderDescriptor] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    @property
    def routing_enabled(self) -> bool:
        return False

    def register(self, descriptor: CodingProviderDescriptor) -> None:
        try:
            validated = validate_provider_descriptor(descriptor)
        except CodingProviderContractError as exc:
            raise CodingProviderRegistryError(exc.code, str(exc)) from exc
        if validated.provider_id in self._descriptors:
            raise CodingProviderRegistryError(
                CodingProviderFailureCode.INVALID_REQUEST,
                f"coding provider is already registered: {validated.provider_id}",
            )
        self._descriptors[validated.provider_id] = validated

    def register_many(self, descriptors: Iterable[CodingProviderDescriptor]) -> None:
        for descriptor in descriptors:
            self.register(descriptor)

    def get(self, provider_id: str) -> CodingProviderDescriptor:
        try:
            validated = validate_provider_id(provider_id)
        except CodingProviderContractError as exc:
            raise CodingProviderRegistryError(exc.code, str(exc)) from exc
        try:
            return self._descriptors[validated]
        except KeyError as exc:
            raise CodingProviderRegistryError(
                CodingProviderFailureCode.PROVIDER_NOT_FOUND,
                f"coding provider was not found: {validated}",
            ) from exc

    def list_providers(self) -> tuple[CodingProviderDescriptor, ...]:
        """Return providers in deterministic registration order."""

        return tuple(self._descriptors.values())

    def materialize(self, request: CodingProviderSelectionRequest) -> tuple[CodingProviderDescriptor, ...]:
        self._validate_selection_request(request)
        if request.explicit_provider_id is not None:
            try:
                descriptor = self.get(request.explicit_provider_id)
            except CodingProviderRegistryError:
                return ()
            return (descriptor,) if _compatibility_failure(descriptor, request) is None else ()
        return tuple(
            descriptor
            for descriptor in self._descriptors.values()
            if _compatibility_failure(descriptor, request) is None
        )

    def select(self, request: CodingProviderSelectionRequest) -> CodingProviderSelectionResult:
        self._validate_selection_request(request)
        considered = tuple(self._descriptors)
        if request.explicit_provider_id is not None:
            provider_id = request.explicit_provider_id
            try:
                descriptor = self.get(provider_id)
            except CodingProviderRegistryError:
                return _selection_failure(
                    CodingProviderFailureCode.PROVIDER_NOT_FOUND,
                    "The explicitly selected coding provider is not registered.",
                    (provider_id,),
                )
            failure = _compatibility_failure(descriptor, request)
            if failure is not None:
                return _selection_failure(failure[0], failure[1], (provider_id,))
            return CodingProviderSelectionResult(
                provider=descriptor,
                safe_message="Coding provider selected.",
                considered_provider_ids=(provider_id,),
            )

        for descriptor in self._descriptors.values():
            if _compatibility_failure(descriptor, request) is None:
                return CodingProviderSelectionResult(
                    provider=descriptor,
                    safe_message="Coding provider selected.",
                    considered_provider_ids=considered,
                )
        return _selection_failure(
            CodingProviderFailureCode.NO_COMPATIBLE_PROVIDER,
            "No enabled, ready coding provider supports the required capabilities and context mode.",
            considered,
        )

    @staticmethod
    def _validate_selection_request(request: object) -> None:
        if not isinstance(request, CodingProviderSelectionRequest):
            raise CodingProviderRegistryError(
                CodingProviderFailureCode.INVALID_REQUEST,
                "selection request must be a CodingProviderSelectionRequest",
            )


def builtin_coding_provider_descriptors(
    env: Mapping[str, str] | None = None,
) -> tuple[CodingProviderDescriptor, ...]:
    """Return static descriptors without detection, execution, or routing side effects."""

    source = os.environ if env is None else env
    fake_enabled = source.get("FORGEX_ENABLE_FAKE_API_CODING_AGENT", "").strip() == "1"
    real_api_enabled = source.get("FORGEX_ENABLE_REAL_API_CODING_AGENT", "").strip() == "1"
    return (
        CodingProviderDescriptor(
            provider_id="verified_template",
            provider_type=CodingProviderType.VERIFIED_TEMPLATE,
            display_name="Verified template",
            execution_mode=CodingProviderExecutionMode.VERIFIED_TEMPLATE,
            capabilities=frozenset({
                CodingProviderCapability.GENERATE_FILES,
                CodingProviderCapability.CREATE_REVIEW,
            }),
            context_modes=frozenset({CodingContextMode.NONE, CodingContextMode.FILE_TREE_ONLY}),
            readiness=CodingProviderReadiness.READY,
            enabled=True,
            production_eligible=True,
        ),
        CodingProviderDescriptor(
            provider_id="fake_api_coding_agent",
            provider_type=CodingProviderType.API_CODING_AGENT,
            display_name="Fake API coding agent",
            execution_mode=CodingProviderExecutionMode.STRUCTURED_PROPOSAL,
            capabilities=frozenset({
                CodingProviderCapability.GENERATE_FILES,
                CodingProviderCapability.MODIFY_FILES,
                CodingProviderCapability.SUGGEST_COMMANDS,
                CodingProviderCapability.CREATE_REVIEW,
            }),
            context_modes=frozenset({CodingContextMode.FILE_TREE_ONLY, CodingContextMode.SELECTED_FILES}),
            readiness=CodingProviderReadiness.READY if fake_enabled else CodingProviderReadiness.DISABLED,
            enabled=fake_enabled,
            dev_only=True,
            safe_failure_reason=None if fake_enabled else "FAKE_API_CODING_AGENT_DISABLED",
        ),
        CodingProviderDescriptor(
            provider_id="api_coding_agent",
            provider_type=CodingProviderType.API_CODING_AGENT,
            display_name="API coding agent",
            execution_mode=CodingProviderExecutionMode.STRUCTURED_PROPOSAL,
            capabilities=frozenset({
                CodingProviderCapability.GENERATE_FILES,
                CodingProviderCapability.MODIFY_FILES,
                CodingProviderCapability.SUGGEST_COMMANDS,
                CodingProviderCapability.READ_SELECTED_CONTEXT,
                CodingProviderCapability.CREATE_REVIEW,
            }),
            context_modes=frozenset({
                CodingContextMode.FILE_TREE_ONLY,
                CodingContextMode.SELECTED_FILES,
                CodingContextMode.BOUNDED_RELEVANT_FILES,
            }),
            readiness=CodingProviderReadiness.READY if real_api_enabled else CodingProviderReadiness.DISABLED,
            enabled=real_api_enabled,
            production_eligible=False,
            uses_api_keys=True,
            safe_failure_reason=None if real_api_enabled else "REAL_API_CODING_AGENT_DISABLED",
        ),
        CodingProviderDescriptor(
            provider_id="codex_cli",
            provider_type=CodingProviderType.CODEX_CLI,
            display_name="Codex CLI",
            execution_mode=CodingProviderExecutionMode.MANAGED_CLI_SANDBOX,
            capabilities=frozenset({
                CodingProviderCapability.RUN_IN_CLI_SANDBOX,
                CodingProviderCapability.CREATE_REVIEW,
                CodingProviderCapability.SUGGEST_COMMANDS,
            }),
            context_modes=frozenset({CodingContextMode.FILE_TREE_ONLY, CodingContextMode.SELECTED_FILES}),
            readiness=CodingProviderReadiness.DISABLED,
            enabled=False,
            uses_local_cli_auth=True,
            safe_failure_reason="Unified coding-provider routing for Codex CLI is not enabled.",
        ),
        CodingProviderDescriptor(
            provider_id="agy_cli",
            provider_type=CodingProviderType.AGY_CLI,
            display_name="AGY CLI",
            execution_mode=CodingProviderExecutionMode.MANAGED_CLI_SANDBOX,
            capabilities=frozenset({
                CodingProviderCapability.RUN_IN_CLI_SANDBOX,
                CodingProviderCapability.CREATE_REVIEW,
            }),
            context_modes=frozenset({CodingContextMode.FILE_TREE_ONLY, CodingContextMode.SELECTED_FILES}),
            readiness=CodingProviderReadiness.DISABLED,
            enabled=False,
            uses_local_cli_auth=True,
            safe_failure_reason="Unified coding-provider routing for AGY CLI is not enabled.",
        ),
        CodingProviderDescriptor(
            provider_id="claude_code_cli",
            provider_type=CodingProviderType.CLAUDE_CODE_CLI,
            display_name="Claude Code CLI",
            execution_mode=CodingProviderExecutionMode.MANAGED_CLI_SANDBOX,
            capabilities=frozenset(),
            context_modes=frozenset({CodingContextMode.NONE}),
            readiness=CodingProviderReadiness.UNAVAILABLE,
            enabled=False,
            uses_local_cli_auth=True,
            safe_failure_reason="Future provider; no ForgeX execution adapter exists.",
        ),
        CodingProviderDescriptor(
            provider_id="opencode_cli",
            provider_type=CodingProviderType.OPENCODE_CLI,
            display_name="OpenCode CLI",
            execution_mode=CodingProviderExecutionMode.MANAGED_CLI_SANDBOX,
            capabilities=frozenset(),
            context_modes=frozenset({CodingContextMode.NONE}),
            readiness=CodingProviderReadiness.UNAVAILABLE,
            enabled=False,
            uses_local_cli_auth=True,
            safe_failure_reason="Reference-only future provider; no ForgeX execution adapter exists.",
        ),
        CodingProviderDescriptor(
            provider_id="manual_patch",
            provider_type=CodingProviderType.MANUAL_PATCH,
            display_name="Manual patch import",
            execution_mode=CodingProviderExecutionMode.MANUAL_IMPORT,
            capabilities=frozenset({
                CodingProviderCapability.CREATE_PATCH,
                CodingProviderCapability.CREATE_REVIEW,
            }),
            context_modes=frozenset({CodingContextMode.NONE}),
            readiness=CodingProviderReadiness.NOT_CONFIGURED,
            enabled=False,
            safe_failure_reason="Optional manual patch provider is not implemented.",
        ),
    )


def default_coding_provider_registry(
    env: Mapping[str, str] | None = None,
) -> CodingProviderRegistry:
    return CodingProviderRegistry(builtin_coding_provider_descriptors(env))


def _compatibility_failure(
    descriptor: CodingProviderDescriptor,
    request: CodingProviderSelectionRequest,
) -> tuple[CodingProviderFailureCode, str] | None:
    if not descriptor.enabled:
        return CodingProviderFailureCode.PROVIDER_DISABLED, descriptor.safe_failure_reason or "Coding provider is disabled."
    if descriptor.readiness is not CodingProviderReadiness.READY:
        return CodingProviderFailureCode.PROVIDER_UNREADY, descriptor.safe_failure_reason or "Coding provider is not ready."
    if descriptor.dev_only and not request.allow_dev_providers:
        return CodingProviderFailureCode.DEV_PROVIDER_NOT_ALLOWED, "Dev-only coding provider was not allowed for this selection."
    if not request.required_capabilities.issubset(descriptor.capabilities):
        return CodingProviderFailureCode.CAPABILITY_MISMATCH, "Coding provider lacks one or more required capabilities."
    if request.context_mode not in descriptor.context_modes:
        return CodingProviderFailureCode.CONTEXT_MODE_UNSUPPORTED, "Coding provider does not support the requested context mode."
    return None


def _selection_failure(
    code: CodingProviderFailureCode,
    message: str,
    considered: tuple[str, ...],
) -> CodingProviderSelectionResult:
    return CodingProviderSelectionResult(
        provider=None,
        failure_code=code,
        safe_message=message,
        considered_provider_ids=considered,
    )
