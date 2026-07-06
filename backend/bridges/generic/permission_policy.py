"""ForgeX-owned provider permissions; provider trust/config is never imported."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from enum import Enum


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ProviderPermissionPolicy:
    policy_id: str
    read_workspace: PermissionDecision = PermissionDecision.DENY
    write_workspace: PermissionDecision = PermissionDecision.DENY
    write_sandbox: PermissionDecision = PermissionDecision.ASK
    read_external: PermissionDecision = PermissionDecision.DENY
    write_external: PermissionDecision = PermissionDecision.DENY
    shell: PermissionDecision = PermissionDecision.DENY
    network: PermissionDecision = PermissionDecision.DENY
    install_dependencies: PermissionDecision = PermissionDecision.DENY
    ask_user: PermissionDecision = PermissionDecision.DENY
    modify_active_workspace: PermissionDecision = PermissionDecision.DENY
    apply_patch: PermissionDecision = PermissionDecision.DENY
    build_project: PermissionDecision = PermissionDecision.DENY
    flash_device: PermissionDecision = PermissionDecision.DENY
    import_artifact: PermissionDecision = PermissionDecision.ASK

    def to_safe_dict(self) -> dict[str, str]:
        return {
            item.name: getattr(self, item.name).value if item.name != "policy_id" else self.policy_id
            for item in fields(self)
        }


def default_local_cli_policy() -> ProviderPermissionPolicy:
    return ProviderPermissionPolicy(policy_id="forgex-local-cli-default-v1")


def native_smoke_policy(*, managed_sandbox_verified: bool) -> ProviderPermissionPolicy:
    if not managed_sandbox_verified:
        raise ValueError("managed_sandbox_required")
    return replace(
        default_local_cli_policy(),
        policy_id="forgex-local-cli-native-smoke-v1",
        write_sandbox=PermissionDecision.ALLOW,
    )
