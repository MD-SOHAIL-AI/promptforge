# Phase 2.5.8.5.1 Packaged Agent Readiness Plan

## Scope

Stabilize the existing AGY-only generic API, SSE boundary, packaged lifecycle, Agent UI, QA capture path, and live-smoke guard. No provider expansion or apply/build/flash behavior is permitted.

## Work

1. Reproduce packaged startup with retained isolated state and inspect composition before `/health`.
2. Make corrupt generic metadata fail closed at the generic API boundary without taking down backend health.
3. Add phase-scoped, sanitized startup evidence and child-exit/probe diagnostics.
4. Validate packaged providers, list, disabled start, SSE replay/heartbeat/terminal/resync, Bridge Safety, frontend, and renderer.
5. Add QA-only inert Agent fixtures and capture the visual state matrix through the existing packaged CDP path.
6. Verify the live AGY command refuses execution without every gate and explicit operator confirmation.
7. Run focused, full, frontend, Electron, safety, reset-package, and smoke-package verification.

## Exit criteria

Packaged backend/frontend/renderer and generic API/SSE checks pass; screenshots or DOM evidence exist; live AGY is either authorized and passing or explicitly blocked; defaults and all existing safety boundaries remain unchanged.

