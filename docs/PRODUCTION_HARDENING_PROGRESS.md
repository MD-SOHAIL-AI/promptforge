# Production hardening implementation status

This is an implementation ledger, not production certification. The agreed plan
is only partially implemented. Existing release requirements still apply.

## Implemented

- Dedicated `/v1/chat/sessions` CRUD, message, cancellation, and SSE routes.
  Chat routes do not import Agent execution or intent routing and reject Agent
  session IDs and execution-only request fields. The Chat UI uses these routes
  for messages, history, model selection, rename, delete, streaming, and stop.
- Independent frontend Chat session type. Existing histories and compatibility
  endpoints remain available; the underlying store is still shared.
- Incomplete/unavailable/truncated diff analysis requires review and approval.
  Full-content risk analysis independent of previews is still pending.
- Undo prevalidates every retained backup hash before changing targets and
  retains the applied state and recovery files when validation fails.
- Managed project loading discovers added text files; saving preserves additions
  outside the previous manifest, including additions made after the caller loaded
  its project. Full transaction unification with ChangeSets remains pending.
- Editor save completion requires the same project and unchanged tab object;
  subsequent edits and reopened tabs are preserved, and saving does not change
  the selected tab.
- Embedded context prunes hidden/generated/dependency directories, prioritizes
  application directories, and bounds source-file reads and directory traversal.
- Autonomous staging retains read-only access to original project skills.
  Skill reading is bounded and rejects symlink/junction components.
- Builds explicitly request `buildprog` and `checkprogsize`. Executable hooks,
  indirect configuration, custom platform packages, upload commands, and unsafe
  configured targets are rejected at the executor before subprocess invocation.
  This is a restrictive policy, not an OS sandbox or completed trust workflow.
- Agent/workflow subprocesses inherit run ownership. Cancellation terminates
  only that owner's processes and prevents subsequent processes under the same
  cancelled owner. Global shutdown uses one stable process snapshot.

## Remaining implementation

1. Separate normalized backend Chat/Agent schemas, transactional migrations,
   paginated histories, atomic message/event commits, and legacy cutover.
2. Versioned Agent endpoints with explicit Plan/Ask/Auto capability contracts;
   remove heuristic permission escalation and duplicate conversation branches.
3. One durable Agent engine, persistent project leases, atomic transitions,
   idempotent tool execution, checkpoint validation, and explicit resume/discard.
4. Provider capability certification, native tool/structured APIs, token-aware
   context, adaptive budgets, stronger-model escalation, stall detection, and
   complex-task live evaluations.
5. Persisted Chat-to-Agent context packages with hashes, provenance, selection,
   source links, and an unexecuted new Agent task.
6. Atomic managed-project/ChangeSet inventory; editor draft recovery and
   Save/Discard/Cancel flows; full-content risk analysis.
7. Immutable multi-image upload bundles, direct upload of approved bytes,
   shared flash executable resolution, build trust, package isolation, and
   device-operation arbitration. Existing flash rebuild behavior is unresolved.
8. Dependency updates and refreshed audit gates; installer/upgrade verification;
   legacy entry-point retirement and complete observability.
9. Hardware sign-off for Uno Rev3, ESP32 DevKit V1, and Blue Pill reference paths;
   actual provider/model conformance and quality results.

## Validation notes

New regressions cover Chat boundary rejection, incomplete diffs, corrupted or
missing undo backups, dependency-heavy hardware context, rejected build hooks
and upload targets, managed ChangeSet additions followed by save, original
project skill access, and real two-process cancellation isolation.

No physical device was flashed, no paid model was invoked, and no production
certification is claimed. The historical reproduction script intentionally
asserts defects and will stop when it encounters a now-fixed defect; use the
new regression tests to validate corrected behavior.
