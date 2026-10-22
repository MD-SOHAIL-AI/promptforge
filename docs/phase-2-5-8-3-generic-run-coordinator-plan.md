# Phase 2.5.8.3 Generic Run Coordinator Plan

## Objective

Add an internal coordinator that persists and enforces the generic bridge lifecycle without exposing an HTTP run surface or enabling a production provider.

## Ownership Map

| Concern | Owner |
| --- | --- |
| Provider implementation/process/timeout | Registered `BridgeProvider` |
| Provider permission | `DefaultDenyBridgeRoutingPolicy` |
| Canonical transitions | `BridgeRunStateMachine` |
| Records/events/artifact associations | `BridgeRunStore` |
| Internal fanout/replay | `InternalBridgeEventTransport` |
| Sandbox containment/deletion | `BridgeSandboxService` through a narrow lifecycle adapter |
| Review/patch bytes and approval | Existing review and patch services |
| Coordination ordering and races | `GenericBridgeRunCoordinator` |

## Implementation Plan

1. Add store locking, record revisions, and compare-and-set updates.
2. Add immutable default-deny global and per-provider routing policy.
3. Add bounded async internal event queues and replay history with explicit resynchronization.
4. Add sandbox lifecycle and artifact-validation boundaries over existing owners.
5. Implement persistence-first transitions, fake-provider execution, result mapping, cancellation, cleanup, queries, and restart interruption.
6. Compose the coordinator with production routing and AGY generic execution disabled.
7. Extend diagnostics, safety scanning, tests, and architecture/security documentation.

## Non-Goals

- No generic run or cancellation HTTP route.
- No UI routing/provider selection.
- No provider detection or execution at startup.
- No subprocess, apply, restore, build, flash, or independent sandbox manager.
- No automatic resume after restart.

## Verification Gates

Focused coordinator tests must cover default deny, persistence-before-execution, CAS, one owner, legal states, bounded events, artifacts, cancellation races, cleanup, reconciliation, and safe queries. The full backend, frontend, Electron, and safety suites must pass without invoking a real agent.
