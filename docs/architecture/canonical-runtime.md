# Canonical ForgeX Runtime Architecture

The target runtime has two provider-facing contracts:

1. Connection Registry owns detection, authentication observation, transport health, named accounts, models, and safe diagnostics.
2. AgentAdapter Registry owns bounded proposal execution. Adapters receive approved context, run in ForgeX-managed containment, and return untrusted proposals.

Model routing handles model calls only. Codex CLI and AGY are AgentAdapters using CLI-owned-session Connections; they are not model endpoints. ForgeX alone owns review derivation, approvals, active-workspace apply, rollback, build, flash, monitor, and verification.

Durable SQLite workflow runs, steps, append-only events, outbox delivery, approvals, artifacts, leases, usage, profile-version bindings, and routing decisions are the intended authorities. Backend conversation messages derive from committed workflow events. Browser storage is limited to harmless presentation preferences.

## Current cutover status

The machine-readable report at ../releases/compatibility-cutover-audit.json is authoritative. As of this release:

- Codex model-provider implementation is removed.
- Browser-local conversation authority is removed.
- Durable profile binding, approval binding, and routing-decision audits pass for current SQLite data.
- ProductProviderRegistry, CodingProviderRegistry, and BridgeProviderRegistry still have production callers.
- CodingWorkflowStore JSONL still has production API/service callers.
- Legacy Product Agent and Coding Workflow components remain compatibility-flagged.
- Unified Agent Workspace executor parity is intentionally fail-closed.

Those blocked paths must not be removed until their individual caller inventories reach zero and parity tests pass.
