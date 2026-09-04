"""Central capability grants for ForgeX-owned agent tools."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .tool_contracts import RuntimeClassification, ToolCall, ToolName, WRITE_TOOLS, EXECUTION_TOOLS
from .tool_policy import ToolPermissionPolicy, ToolPolicyError, product_agent_policy


SERVICE_CAPABILITIES = frozenset({"workspace_stage", "build", "network", "hardware", "serial_monitor"})


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    grant_id: str
    session_id: str | None
    run_id: str
    node_id: str
    workspace_root: str
    allowed_tools: frozenset[ToolName]
    allowed_services: frozenset[str]
    authorization_source: str
    expires_at_monotonic: float
    max_calls: int = 20
    allow_workspace_write: bool = False
    allow_build: bool = False
    allow_network: bool = False
    allow_hardware: bool = False

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "grant_id": self.grant_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "allowed_tools": sorted(item.value for item in self.allowed_tools),
            "allowed_services": sorted(self.allowed_services),
            "authorization_source": self.authorization_source,
            "max_calls": self.max_calls,
            "allow_workspace_write": self.allow_workspace_write,
            "allow_build": self.allow_build,
            "allow_network": self.allow_network,
            "allow_hardware": self.allow_hardware,
        }


class ToolPolicyEngine:
    """Issue scoped grants and validate every tool call at execution time."""

    def __init__(self, *, default_ttl_seconds: float = 300.0) -> None:
        self.default_ttl_seconds = max(1.0, min(float(default_ttl_seconds), 3600.0))
        self._revoked: set[str] = set()
        self._usage: dict[str, int] = {}
        self._grants: dict[str, CapabilityGrant] = {}
        self._lock = threading.RLock()

    def issue_grant(
        self,
        *,
        run_id: str,
        node_id: str,
        workspace_root: str | Path,
        allowed_tools: Iterable[ToolName | str] = (),
        allowed_services: Iterable[str] = (),
        authorization_source: str,
        session_id: str | None = None,
        ttl_seconds: float | None = None,
        max_calls: int = 20,
        allow_workspace_write: bool = False,
        allow_build: bool = False,
        allow_network: bool = False,
        allow_hardware: bool = False,
    ) -> CapabilityGrant:
        root = Path(workspace_root).resolve(strict=True)
        parsed = frozenset(item if isinstance(item, ToolName) else ToolName(item) for item in allowed_tools)
        services = frozenset(str(item).strip().casefold() for item in allowed_services)
        if not services.issubset(SERVICE_CAPABILITIES):
            raise ValueError("CAPABILITY_SERVICE_INVALID")
        if not parsed and not services:
            raise ValueError("CAPABILITY_SCOPE_REQUIRED")
        if parsed.intersection(WRITE_TOOLS) and authorization_source not in {
            "explicit_edit_request",
            "explicit_review",
            "repair_task",
            "system_test",
        }:
            raise PermissionError("CAPABILITY_WRITE_AUTHORIZATION_REQUIRED")
        if ToolName.BUILD_FIRMWARE in parsed and not allow_build:
            raise PermissionError("CAPABILITY_BUILD_AUTHORIZATION_REQUIRED")
        ttl = self.default_ttl_seconds if ttl_seconds is None else max(1.0, min(float(ttl_seconds), 3600.0))
        grant = CapabilityGrant(
            grant_id=f"grant-{uuid.uuid4().hex}",
            session_id=session_id,
            run_id=_required_id(run_id),
            node_id=_required_id(node_id),
            workspace_root=str(root),
            allowed_tools=parsed,
            allowed_services=services,
            authorization_source=authorization_source,
            expires_at_monotonic=time.monotonic() + ttl,
            max_calls=max(1, min(int(max_calls), 100)),
            allow_workspace_write=bool(allow_workspace_write),
            allow_build=bool(allow_build),
            allow_network=bool(allow_network),
            allow_hardware=bool(allow_hardware),
        )
        with self._lock:
            self._grants[grant.grant_id] = grant
        return grant

    def validate_call(
        self,
        grant: CapabilityGrant,
        call: ToolCall,
        root: Path,
        *,
        policy: ToolPermissionPolicy | None = None,
        consume: bool = False,
    ) -> None:
        with self._lock:
            self._validate_live_grant(grant, root)
            if call.tool not in grant.allowed_tools:
                raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "tool_capability_denied")
            used = self._usage.get(grant.grant_id, 0)
            if used >= grant.max_calls:
                raise ToolPolicyError(RuntimeClassification.LIMIT_EXCEEDED, "capability_call_limit")
            (policy or product_agent_policy()).validate_call(call, root)
            if consume:
                self._usage[grant.grant_id] = used + 1

    def validate_service(
        self,
        grant: CapabilityGrant,
        service: str,
        root: str | Path,
        *,
        consume: bool = True,
    ) -> None:
        capability = str(service).strip().casefold()
        with self._lock:
            self._validate_live_grant(grant, Path(root))
            if capability not in grant.allowed_services:
                raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "service_capability_denied")
            boolean_gate = {
                "workspace_stage": grant.allow_workspace_write,
                "build": grant.allow_build,
                "network": grant.allow_network,
                "hardware": grant.allow_hardware,
                "serial_monitor": grant.allow_hardware,
            }.get(capability, False)
            if not boolean_gate:
                raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "service_capability_gate_closed")
            used = self._usage.get(grant.grant_id, 0)
            if used >= grant.max_calls:
                raise ToolPolicyError(RuntimeClassification.LIMIT_EXCEEDED, "capability_call_limit")
            if consume:
                self._usage[grant.grant_id] = used + 1

    def revoke(self, grant_id: str) -> None:
        with self._lock:
            self._revoked.add(grant_id)

    def revoke_run(self, run_id: str) -> None:
        with self._lock:
            for grant_id, grant in self._grants.items():
                if grant.run_id == run_id:
                    self._revoked.add(grant_id)

    def remaining_calls(self, grant: CapabilityGrant) -> int:
        with self._lock:
            return max(0, grant.max_calls - self._usage.get(grant.grant_id, 0))

    def _validate_live_grant(self, grant: CapabilityGrant, root: Path) -> None:
        issued = self._grants.get(grant.grant_id)
        if issued is None or issued != grant:
            raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "capability_not_issued")
        if grant.grant_id in self._revoked:
            raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "capability_revoked")
        if time.monotonic() >= grant.expires_at_monotonic:
            self._revoked.add(grant.grant_id)
            raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "capability_expired")
        if Path(grant.workspace_root).resolve() != root.resolve():
            raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "capability_workspace_mismatch")


def _required_id(value: str) -> str:
    candidate = str(value).strip()
    if not candidate or len(candidate) > 160:
        raise ValueError("CAPABILITY_ID_INVALID")
    return candidate
