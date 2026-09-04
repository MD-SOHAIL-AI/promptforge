# Phase 2.5.8.2 AGY Generic Provider Plan

## Objective

Adapt the proven AGY detector and sandbox runner to the Phase 2.5.8.1 generic contracts without creating a second execution path or routing UI traffic through the adapter.

## Ownership Constraints

| Responsibility | Authoritative owner |
| --- | --- |
| Executable, version, and safe auth detection | `AntigravityCliDetector` / `BridgeDetectionService` |
| Executable resolution and fixed `agy -p` argv | `AntigravitySandboxRunner` |
| Environment allowlist | `safe_bridge_env` and `SAFE_ENV_NAMES` |
| Sandbox creation and cleanup boundary | `BridgeSandboxService` and lifecycle owner |
| Process, timeout, and process-tree cancellation | `AntigravitySandboxRunner` |
| Capped stdout/stderr diagnostics | `AntigravitySandboxRunner` |
| Diff, review session, and audit events | `BridgeDiffService`, `BridgeReviewStore`, `BridgeAuditLog` |
| Patch verification/apply/rollback | Existing patch safety pipeline |
| Generic translation only | `AGYBridgeProvider` |

## Work Plan

1. Extend generic detection/result contracts only where AGY parity needs safe structured fields.
2. Add `AGYBridgeProvider` with stable provider ID `agy` and a dedicated request translation object.
3. Delegate detect/start/cancel to existing owners and map legacy state/results to canonical outcomes.
4. Translate safe lifecycle observations to bounded generic events and review IDs to contained references.
5. Register the adapter explicitly during application composition with execution disabled.
6. Add mock parity tests for detection, validation, success, failure, timeout, cancellation, artifacts, and unknown failures.
7. Extend read-only diagnostics, safety scan, and architecture/security documentation.
8. Run focused, backend, frontend, Electron, and safety verification without invoking a real agent.

## Non-Goals

- No generic run API, coordinator, streaming, or UI provider selection.
- No changes to AGY argv, environment, timeout, cancellation, or review semantics.
- No Codex, Claude Code, or OpenCode adapter.
- No apply, build, flash, or automatic cleanup authority.

## Completion Gates

The adapter must be registered but not routed, use only validated managed sandbox roots, reuse existing review IDs, emit no raw output or instructions, preserve legacy endpoint behavior, and pass the complete verification matrix.
