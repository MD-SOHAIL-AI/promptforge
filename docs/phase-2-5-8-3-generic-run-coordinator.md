# Phase 2.5.8.3 Generic Run Coordinator

## Result

ForgeX now composes an internal `GenericBridgeRunCoordinator`. It is available for injected internal use, but production routing and every provider permission remain disabled. No HTTP, SSE, WebSocket, or frontend execution surface was added.

```text
runtime-only request + verified sandbox
                |
                v
GenericBridgeRunCoordinator
  | persist/CAS state -> append safe event -> internal fanout
  | default-deny policy
  | registry lookup
  | provider.validate/start/cancel
  | validate reference -> existing review/patch ownership
  v
existing sandbox lifecycle owner (finalize once)
```

## Coordinator Boundary

The coordinator validates identities through existing contracts, creates `queued` state before execution, resolves the registered provider, evaluates policy, validates the sandbox through its owner, applies only canonical transitions, accepts structured results, validates artifact references, and delegates cancellation. It never constructs commands, starts/terminates processes, parses raw provider output, writes active-workspace files, or invokes apply/restore/build/flash.

## Routing Policy

`DefaultDenyBridgeRoutingPolicy` requires all of the following: global enablement, explicit provider permission, registration, provider availability, supported requested capability, and verified mandatory sandboxing. Global and provider defaults are false. Requests cannot mutate policy. Production uses the empty default policy, so registered AGY remains unroutable and disabled.

## Persistence Ordering

Each transition is validated by `BridgeRunStateMachine`, atomically stored through `BridgeRunStore`, and only then represented by a persisted event and internal publication. Records carry monotonic revisions. Coordinator updates use compare-and-set against the previous revision; stale updates fail closed. Provider invocation begins only after `queued`, `validating`, `preparing_sandbox`, and `running` have been persisted.

Instructions remain only on the in-memory request/provider context. Persistence contains only SHA-256 and length. Records, events, failures, and queries contain no instruction, raw output, stack trace, environment, command, executable, or internal path.

## Event Delivery

`InternalBridgeEventTransport` implements publish, subscribe, unsubscribe, and replay without a network endpoint. Per-run history and subscriber queues are bounded. Publication uses nonblocking queue insertion, so a slow subscriber cannot block a run. Overflow clears that subscriber queue and supplies `resync_required`. Subscriber state is isolated and unsubscription is idempotent.

The persisted event stream is authoritative. If internal fanout itself fails, the coordinator persists a warning/resync requirement and prevents provider execution or successful completion when detected before finalization.

## Artifact Lifecycle

Only provider-returned `BridgeArtifactReference` values are accepted. The validator checks run/sandbox ownership, known type, required review/pipeline identifiers, size/hash contract, and containment beneath managed artifact storage. The store rejects duplicate IDs. Association and `artifact_created` persistence occur before completion. Public queries remove storage references. No artifact has apply authority; existing review, integrity, drift, preflight, apply, restore, and rollback owners remain authoritative.

## Cancellation and Races

Cancellation reloads persisted state, transitions to `cancelling` with a constrained reason code and timestamp, persists/publishes the request, and delegates to the provider/process owner. Accepted cancellation transitions legally to `cancelled`; rejection becomes a safe failure. Repetition returns `already_requested` or `already_terminal`. A terminal completion observed before cancellation remains completed; a cancellation persisted first wins over late provider success.

## Sandbox and Timeout Ownership

The coordinator receives a pre-created `BridgeSandboxContext`. `ExistingBridgeSandboxLifecycle` rechecks direct-child containment under `BridgeSandboxService`, active-workspace separation, and existence. Finalization is invoked once. Deletion, when policy requests it, is performed by the existing service's contained single-sandbox cleanup. Cleanup failure creates a warning and never rewrites the primary terminal result.

Provider timeout remains provider-owned. The coordinator only passes the validated timeout and maps `timeout` to `timed_out`; it adds no process timer or termination logic.

## Restart and Queries

Startup reconciliation changes nonterminal records to `interrupted`, increments their revisions, and appends a safe event. Terminal records, events, and artifacts are not duplicated. No provider is detected, invoked, or resumed. Internal queries provide bounded recent records, safe events, and public artifact summaries only.

## Production State

- Coordinator available: true.
- Event transport: internal only.
- Generic routing: false.
- AGY generic execution: false.
- Codex, Claude Code, OpenCode: false.
- Auto-apply, auto-build, auto-flash: false.
- Public generic run/cancel API: absent.

The next planned phase is Phase 2.5.8.4, which may perform an explicitly gated AGY compatibility cutover and end-to-end parity QA.

## Verification Result

The focused coordinator selection passed 25 tests with 1,474 deselected. The complete backend suite passed 1,492 tests with 7 skipped. Frontend typecheck/build passed, Electron passed 10 tests, and all 29 safety-scan assertions passed. All provider paths used fakes; no real AGY, Codex, Claude Code, or OpenCode process executed.
