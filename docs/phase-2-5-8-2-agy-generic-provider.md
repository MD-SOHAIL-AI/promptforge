Good night.# Phase 2.5.8.2 AGY Generic Provider

## Result

`AGYBridgeProvider` implements the generic provider protocol using stable provider ID `agy`. It is explicitly registered in application composition with generic execution disabled. Existing AGY endpoints still call `AntigravitySandboxRunner` directly and are unchanged.

```text
Generic request/context
        |
        v
AGYBridgeProvider (translate and map only)
        |
        v
AntigravitySandboxRunner
        |
        +--> BridgeSandboxService
        +--> owned process / timeout / cancellation
        +--> BridgeDiffService / review ID
                         |
                         v
             existing patch safety pipeline
```

## Adapter Boundary

The adapter contains no subprocess import, command builder, executable resolver, process-tree termination, sandbox cleanup, patch apply, build, or flash call. Its dedicated `AGYRunnerRequest` passes the runtime-only instruction, validated internal sandbox root, and bounded timeout to the legacy runner. The translation object hides prompt and path from `repr` and exposes only instruction hash/length in safe metadata.

The generic sandbox is passed as the legacy runner's source workspace. The runner remains responsible for copying that source into its owned execution sandbox before it invokes AGY. The active workspace is never passed to the runner.

## Detection and Capabilities

Generic detection delegates to `BridgeDetectionService.detect_provider("antigravity_cli_bridge")` and maps installed, availability, version, safe authentication status, and unavailability code. Executable paths, checked command output, credentials, account identity, and environment data are discarded.

AGY reports non-interactive, sandbox-required, cancellation, timeout, artifact, and structured-result support. Streaming, resume, API-key auth, and safely detectable subscription-auth support remain false. Before successful detection, availability is conservative (`false`). Installation never enables routing.

## State Mapping

| Legacy state | Canonical state |
| --- | --- |
| `detected`, `accepted` | `queued` |
| `validating` | `validating` |
| `pending`, `preparing_sandbox` | `preparing_sandbox` |
| `running` | `running` |
| `cancelling`, `cancellation_requested` | `cancelling` |
| `collecting_changes` | `collecting_artifacts` |
| `review_ready`, `completed` | `completed` |
| `failed` | `failed` |
| `cancelled` | `cancelled` |
| `failed_timeout`, `timed_out` | `timed_out` |
| `blocked` | `blocked` |
| `interrupted` | `interrupted` |
| unknown | `failed` / `internal_error` |

The adapter reports outcomes and events; it does not mutate persisted canonical run records. Completion is emitted only after review artifact collection. One terminal event is permitted, and late completion is ignored after cancellation.

## Results, Events, and Artifacts

Legacy failure, timeout, cancellation, blocked, interrupted, and unknown outcomes map to stable generic codes. Raw stdout/stderr, legacy error text, stack traces, paths, and executable details are omitted. Duration is derived from legacy timestamps.

Lifecycle events use per-run monotonic sequences and static sanitized messages. No raw process output is converted to an event. Existing `BridgeRunEvent` length, secret, path, and patch-content controls remain authoritative.

A successful `review_ready` result reuses the existing review ID as a generic review artifact ID and pipeline reference. The reference is resolved beneath managed state and contains no patch/file bytes. It has no apply field or authority; integrity, approval, drift, preflight, apply, and rollback owners remain unchanged.

## Cancellation and Registration

Generic cancellation records a constrained reason, emits a request event, and delegates to `AntigravitySandboxRunner.cancel_run`. The adapter adds no kill logic. Concurrent repetition returns `already_requested`; terminal repetition returns `already_terminal`; missing mappings return `not_found`; unexpected delegation failure returns `rejected`.

Application composition registers exactly one `agy` adapter without calling detection. `execution_enabled=False` and `BridgeProviderRegistry.routing_enabled=False` remain hard boundaries.

## Parity Result

The fake parity harness covers successful/missing/unauthenticated detection, disabled and invalid validation, sandbox rejection, success/review artifact creation, process failure, timeout, cancellation, unknown errors, event ordering, containment, registry behavior, and late completion protection. It does not start a real process.

Verification completed with 49 focused parity tests passing, 1,467 backend tests passing with 7 skipped, 10 Electron tests passing, frontend typecheck/build passing, and all 25 safety-scan assertions passing.

## Intentional Limitations

The AGY adapter is registered but not routed. Existing AGY behavior remains authoritative. No generic run endpoint exists. Generic coordination, persisted lifecycle integration, event transport, and UI routing are deferred to Phase 2.5.8.3. Codex, Claude Code, and OpenCode execution remain disabled.
