# Phase 2.5.8.1 Generic Bridge Contracts

## Result

ForgeX now has provider-neutral contracts for capabilities, detection, validation, sandboxed run context, provider outcomes, canonical state, cancellation, events, artifact references, stable errors, explicit registration, and versioned persistence. The implementation is intentionally disconnected from API routing and all provider runners.

## Implementation Boundary

The implementation lives in `backend/bridges/generic` beside the existing bridge services. This preserves established ownership: AGY remains provider-specific, sandbox creation stays in `BridgeSandboxService`, patches and reviews stay in their existing stores, and apply/rollback checks remain unchanged.

The read-only Bridge Safety response advertises that contracts exist while explicitly reporting generic routing, AGY generic adaptation, Codex, Claude, OpenCode, auto-apply, auto-build, and auto-flash as disabled.

## Security Properties

- Provider and object IDs are validated; provider IDs use an explicit allowlist.
- Instructions are runtime-only, omitted from `repr`, and replaced by SHA-256/length in metadata.
- Requests contain no command, executable, environment, shell, or workspace-path fields.
- Sandbox roots are internal, verified, and disjoint from active workspaces.
- Events redact paths/secrets, reject patch markers, and are size bounded.
- Artifacts are contained references connected to existing review/patch IDs.
- Records use deterministic atomic persistence and fail closed on corruption/version mismatch.
- Registration is explicit and cannot enable routing.

## Deferrals

AGY adaptation is deferred to Phase 2.5.8.2. Streaming is deferred to Phase 2.5.8.3. Codex, Claude Code, and OpenCode execution are deferred. No existing provider behavior was migrated in this phase.

See [Generic Headless Bridge Architecture](generic-headless-bridge-architecture.md) for the complete contract, transition table, persistence model, and ownership map.
