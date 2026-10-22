# Agent redesign implementation ledger

Updated 2026-09-20. This is an implementation ledger, not a production-readiness certification.

## Architectural decisions

- Python owns execution; the TypeScript harness remains a compatibility/shadow consumer until event parity permits retirement.
- Explicit provider/model choices and session/role bindings; never automatically disclose a project to another provider when the selected provider fails.
- Workspace mutations use isolated stages and reviewed ChangeSets. Hardware permissions remain separate.
- Arbitrary commands require a real OS isolation boundary and approvals. A working directory or command-name filter is not a sandbox.

## Implemented in this work

- Streaming uses the same model resolver and locality/capability checks as ordinary generation. Generation pins the resolved model for its attempt.
- Streaming usage retains token counts; telemetry write failures do not replace a successful model result with an error.
- Provider settings and usage default to SQLite. Settings migrate from the actual previous settings location; usage history imports once. Original files are preserved. Credentials use the existing credential store.
- Persistent application/session/role choices, catalog/selection APIs, role selection for subagents, reset support, and provider-reference deletion protection.
- Explicit model IDs travel from agent API requests into staged and build/repair execution. Unavailable default providers no longer silently select a different recipient.
- Bounded conversation projection keeps the latest user request. Tool-observation projection remains valid JSON and retains recent evidence; this is **not** semantic or token-aware compaction.
- Ranged reads return line counts and continuation offsets while preserving the existing size/secret checks.
- Recoverable stale-edit errors become observations so the agent can retry without discarding earlier successful staged edits. Policy denials remain enforced.
- Staged writes publish atomically rather than truncating the existing staged file first.
- `delete_file` and `move_file` operate on staged text files; moves never overwrite a destination. ChangeSets represent moves as delete/create pairs.
- Deletion-aware manifest verification, conflict checking, apply, rollback, persisted reload, and undo. A recreated deleted file prevents undo. Deletions require explicit user approval even when an automated reviewer approves.
- Deterministic ten-file creation/stale-edit/apply/undo and staged move/deletion safety fixtures.
- Agent-mode routing now prefers real provider-native function calls. OpenAI Chat Completions, OpenAI Responses, Anthropic Messages, Gemini, and Ollama receive canonical multi-message tool conversations with stable provider call IDs; legacy ToolPlan envelopes remain a validated compatibility decode path.
- Native conversation history is bounded against the selected model context window. Compaction preserves the original task and complete recent assistant/tool exchanges, never leaves orphaned call IDs, and instructs the model to re-read omitted file evidence.
- Canonical router planners expose an awaitable turn API. Autonomous and subagent loops await it directly, including ordered streamed-summary callbacks; legacy synchronous planners remain isolated in worker threads.
- Fully settled turns produce journal-bound durable checkpoints. Restart diagnostics expose the last safe turn, while checkpoint metadata explicitly prohibits effect replay and permits only an explicit replacement run.
- Attempt settlement is single-use, duplicate provider call IDs are rejected, and validated invocation arguments are defensively copied before execution.
- Verification evidence is invalidated after staged edits or artifact/workspace changes so stale successful builds cannot authorize apply or flash.
- Settings now exposes persistent global model bindings for Explorer, Reviewer, Verifier, Compactor, and Title roles. Each role shows whether it is inherited or overridden, accepts routed model IDs containing slashes, and can explicitly return to the main model.
- Delegated specialist tool calls now consume the parent run's remaining tool budget. A subagent receives only the remaining allowance, and child execution counts are included in the parent result instead of multiplying limits invisibly.
- Specialist planner metadata records the resolved role, binding source, and whether the choice was inherited, making routing decisions diagnosable without exposing credentials or enabling fallback.
- Autonomous observation compaction now emits a durable, versioned context-checkpoint event only when the omitted set advances. It includes a canonical digest, tool counts, recent failure details, plan and verification state, and a session-history retrieval hint.
- Omitted observations are stored outside the public journal in an incremental, per-run SQLite artifact chain. Each segment and reconstructed aggregate is SHA-256 verified, chain positions cannot fork, corrupt artifacts fail closed, and a local authenticated API retrieves the evidence by artifact ID.

## Still required before the complete redesign can be declared finished

1. One asynchronous agent engine shared by staged and build/repair profiles; preserve event ordering, cancellation, leases, policy boundaries, and build evidence. The canonical planner path is async, but the staged runtime still has a synchronous compatibility engine.
2. Full semantic compaction. Native history is model-window bounded and autonomous checkpoints now have dedicated, digest-verified evidence artifacts with recent-failure retention, but summaries remain deterministic rather than model-generated semantic condensations.
3. Safe checkpoint fork/reconciliation beyond replacement-run diagnostics. Interrupted hardware/process operations must never be automatically replayed.
4. A verified OS sandbox and approval UX for general verification commands, plus Git/LSP capability integration. No arbitrary host shell is enabled by this work.
5. Durable subagent execution across process restart; shared parent/child tool accounting is implemented, but current role-specific subagents are not a distributed scheduler.
6. Session-scoped role-binding management and richer routing diagnostics in the frontend; global role bindings are now manageable in Settings.
7. Removal of old planner/runtime/harness paths only after behavioral parity tests pass.
8. Real-provider, compiler-repair, crash/restart, conflict, cancellation, and long-context competence evaluation. Scripted planner tests establish engine mechanics, not model capability or parity with Codex/Claude Code/Cursor/OpenCode.

## Verification record

- Baseline before these changes: 1,548 passed, 4 skipped.
- Intermediate complete suite: 1,555 passed, 4 skipped.
- A later full run: 1,564 passed, 4 skipped, one failure in process-owner cancellation. The cancellation test plus agent API tests passed on an isolated rerun (20 passed); the single failure is not silently discarded.
- Subsequent staged move and agent API batch: 33 passed.
- Current full Python suite: **1,664 passed, 4 skipped** in 107.40 seconds. All skips are host symlink-capability skips. This includes orchestrator/SQLite boundary, incremental artifact-chain integrity, corruption, and authenticated artifact-retrieval coverage.
- Focused native-planner/provider verification: **83 passed**.
- Recovery/orchestration/native integration batch: **43 passed**.
- Frontend typecheck passed; frontend state suite: **60 passed**.
- Shared subagent accounting and async/autonomous hardening batch: **19 passed**; routing/selection/native focused batch: **15 passed**.
- TypeScript harness suite: **9 passed**.
- Backend bytecode compilation passed.

## Known limits

Multi-file apply/undo uses preflight checks, backups, atomic individual writes, and best-effort rollback. It is not a filesystem-wide transaction and is not certified power-loss atomic. Case-only renames and binary-file moves are not supported. Empty directory operations are not represented as ChangeSets. Existing plaintext credentials in a preserved legacy JSON file remain there until an explicitly authorized cleanup; migration does not create new plaintext credential copies.
