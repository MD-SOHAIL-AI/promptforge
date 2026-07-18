from __future__ import annotations

import pytest

from backend.agent_runtime.coding_provider_contracts import (
    FORBIDDEN_PROVIDER_AUTHORITIES,
    CodingContextMode,
    CodingProviderCapability,
    CodingProviderDescriptor,
    CodingProviderExecutionMode,
    CodingProviderFailureCode,
    CodingProviderReadiness,
    CodingProviderSelectionRequest,
    CodingProviderType,
)
from backend.agent_runtime.coding_provider_registry import (
    CodingProviderRegistry,
    CodingProviderRegistryError,
    builtin_coding_provider_descriptors,
    default_coding_provider_registry,
)


def descriptor(
    provider_id: str,
    *,
    enabled: bool = True,
    readiness: CodingProviderReadiness = CodingProviderReadiness.READY,
    capabilities: frozenset[CodingProviderCapability] = frozenset({CodingProviderCapability.GENERATE_FILES}),
    context_modes: frozenset[CodingContextMode] = frozenset({CodingContextMode.SELECTED_FILES}),
    dev_only: bool = False,
) -> CodingProviderDescriptor:
    return CodingProviderDescriptor(
        provider_id=provider_id,
        provider_type=CodingProviderType.API_CODING_AGENT,
        display_name=provider_id,
        execution_mode=CodingProviderExecutionMode.STRUCTURED_PROPOSAL,
        capabilities=capabilities,
        context_modes=context_modes,
        readiness=readiness,
        enabled=enabled,
        dev_only=dev_only,
        safe_failure_reason=None if enabled and readiness is CodingProviderReadiness.READY else "Not available.",
    )


def request(**overrides: object) -> CodingProviderSelectionRequest:
    values: dict[str, object] = {
        "prompt": "create project",
        "context_mode": CodingContextMode.SELECTED_FILES,
    }
    values.update(overrides)
    return CodingProviderSelectionRequest(**values)  # type: ignore[arg-type]


def test_register_list_and_get_preserve_deterministic_registration_order() -> None:
    second = descriptor("second_provider")
    first = descriptor("first_provider")
    registry = CodingProviderRegistry((second, first))
    assert registry.routing_enabled is False
    assert registry.list_providers() == (second, first)
    assert registry.get("first_provider") is first


def test_duplicate_provider_id_is_rejected() -> None:
    value = descriptor("duplicate_provider")
    registry = CodingProviderRegistry((value,))
    with pytest.raises(CodingProviderRegistryError, match="already registered"):
        registry.register(value)


def test_registry_get_rejects_invalid_provider_id() -> None:
    with pytest.raises(CodingProviderRegistryError) as exc:
        CodingProviderRegistry().get("Invalid-Provider")
    assert exc.value.code is CodingProviderFailureCode.INVALID_REQUEST


def test_registry_revalidates_and_rejects_injected_forbidden_authority() -> None:
    value = descriptor("tampered_provider")
    object.__setattr__(value, "capabilities", frozenset({"run_build"}))
    with pytest.raises(CodingProviderRegistryError, match="forbidden coding-provider authority"):
        CodingProviderRegistry((value,))


def test_explicit_compatible_provider_selection_works() -> None:
    registry = CodingProviderRegistry((descriptor("one_provider"), descriptor("two_provider")))
    result = registry.select(request(explicit_provider_id="two_provider"))
    assert result.selected is True
    assert result.provider is not None and result.provider.provider_id == "two_provider"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (descriptor("disabled_provider", enabled=False, readiness=CodingProviderReadiness.DISABLED), CodingProviderFailureCode.PROVIDER_DISABLED),
        (descriptor("unready_provider", readiness=CodingProviderReadiness.UNAVAILABLE), CodingProviderFailureCode.PROVIDER_UNREADY),
    ],
)
def test_explicit_disabled_or_unready_provider_is_rejected(
    value: CodingProviderDescriptor,
    expected: CodingProviderFailureCode,
) -> None:
    result = CodingProviderRegistry((value,)).select(request(explicit_provider_id=value.provider_id))
    assert result.selected is False
    assert result.failure_code is expected


def test_explicit_dev_provider_requires_allow_dev_providers() -> None:
    value = descriptor("dev_provider", dev_only=True)
    registry = CodingProviderRegistry((value,))
    denied = registry.select(request(explicit_provider_id=value.provider_id))
    allowed = registry.select(request(explicit_provider_id=value.provider_id, allow_dev_providers=True))
    assert denied.failure_code is CodingProviderFailureCode.DEV_PROVIDER_NOT_ALLOWED
    assert allowed.provider is value


def test_capability_mismatch_is_rejected() -> None:
    value = descriptor("limited_provider")
    result = CodingProviderRegistry((value,)).select(request(
        explicit_provider_id=value.provider_id,
        required_capabilities=frozenset({CodingProviderCapability.MODIFY_FILES}),
    ))
    assert result.failure_code is CodingProviderFailureCode.CAPABILITY_MISMATCH


def test_context_mismatch_is_rejected() -> None:
    value = descriptor("tree_provider", context_modes=frozenset({CodingContextMode.FILE_TREE_ONLY}))
    result = CodingProviderRegistry((value,)).select(request(explicit_provider_id=value.provider_id))
    assert result.failure_code is CodingProviderFailureCode.CONTEXT_MODE_UNSUPPORTED


def test_automatic_selection_uses_first_compatible_registration() -> None:
    disabled = descriptor("disabled_provider", enabled=False, readiness=CodingProviderReadiness.DISABLED)
    first = descriptor("first_provider")
    second = descriptor("second_provider")
    registry = CodingProviderRegistry((disabled, first, second))
    assert registry.materialize(request()) == (first, second)
    assert registry.select(request()).provider is first


def test_no_compatible_provider_returns_clear_failure() -> None:
    registry = CodingProviderRegistry((descriptor("disabled_provider", enabled=False, readiness=CodingProviderReadiness.DISABLED),))
    result = registry.select(request())
    assert result.failure_code is CodingProviderFailureCode.NO_COMPATIBLE_PROVIDER
    assert "No enabled, ready coding provider" in result.safe_message


def test_builtin_descriptors_are_static_safe_and_complete() -> None:
    descriptors = builtin_coding_provider_descriptors(env={})
    by_id = {item.provider_id: item for item in descriptors}
    assert tuple(by_id) == (
        "verified_template",
        "fake_api_coding_agent",
        "api_coding_agent",
        "codex_cli",
        "agy_cli",
        "claude_code_cli",
        "opencode_cli",
        "manual_patch",
    )
    assert by_id["verified_template"].production_eligible is True
    assert by_id["fake_api_coding_agent"].dev_only is True
    assert by_id["fake_api_coding_agent"].enabled is False
    assert by_id["fake_api_coding_agent"].safe_failure_reason == "FAKE_API_CODING_AGENT_DISABLED"
    assert by_id["fake_api_coding_agent"].uses_api_keys is False
    assert by_id["fake_api_coding_agent"].uses_local_cli_auth is False
    assert by_id["api_coding_agent"].enabled is False
    assert by_id["api_coding_agent"].uses_api_keys is True
    assert by_id["api_coding_agent"].safe_failure_reason == "REAL_API_CODING_AGENT_DISABLED"
    assert by_id["codex_cli"].production_eligible is False
    assert by_id["agy_cli"].production_eligible is False
    assert by_id["claude_code_cli"].readiness is CodingProviderReadiness.UNAVAILABLE
    assert by_id["opencode_cli"].readiness is CodingProviderReadiness.UNAVAILABLE
    assert all(item.can_mutate_active_workspace is False for item in descriptors)
    assert all(
        not ({capability.value for capability in item.capabilities} & FORBIDDEN_PROVIDER_AUTHORITIES)
        for item in descriptors
    )


def test_fake_builtin_enablement_reads_only_existing_dev_flag() -> None:
    registry = default_coding_provider_registry({"FORGEX_ENABLE_FAKE_API_CODING_AGENT": "1"})
    fake = registry.get("fake_api_coding_agent")
    assert fake.enabled is True
    assert fake.readiness is CodingProviderReadiness.READY
    assert fake.dev_only is True
    assert fake.production_eligible is False
    assert fake.can_mutate_active_workspace is False
    assert fake.capabilities == frozenset({
        CodingProviderCapability.GENERATE_FILES,
        CodingProviderCapability.MODIFY_FILES,
        CodingProviderCapability.SUGGEST_COMMANDS,
        CodingProviderCapability.CREATE_REVIEW,
    })
    assert fake.context_modes == frozenset({CodingContextMode.FILE_TREE_ONLY, CodingContextMode.SELECTED_FILES})
    assert registry.routing_enabled is False
    denied = registry.select(request(explicit_provider_id="fake_api_coding_agent"))
    allowed = registry.select(request(explicit_provider_id="fake_api_coding_agent", allow_dev_providers=True))
    assert denied.failure_code is CodingProviderFailureCode.DEV_PROVIDER_NOT_ALLOWED
    assert allowed.provider is fake


def test_real_api_builtin_enablement_reads_only_real_api_flag() -> None:
    registry = default_coding_provider_registry({"FORGEX_ENABLE_REAL_API_CODING_AGENT": "1"})
    real = registry.get("api_coding_agent")
    fake = registry.get("fake_api_coding_agent")
    assert real.enabled is True
    assert real.readiness is CodingProviderReadiness.READY
    assert real.dev_only is False
    assert real.production_eligible is False
    assert real.uses_api_keys is True
    assert real.can_mutate_active_workspace is False
    assert fake.enabled is False
    assert registry.select(request(explicit_provider_id="api_coding_agent")).provider is real
