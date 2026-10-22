# ForgeX Harness-Inspired Agent + Workspace Upgrade

## Scope

This build evolves ForgeX toward a durable, event-driven coding-agent runtime inspired by DeepSeek Harness while preserving ForgeX-specific embedded/hardware safeguards.

### Agent runtime

- Canonical Turn -> Step -> Model Attempt -> Settlement -> ToolInvocation -> Tool Result loop.
- Transient indexed assistant streaming separated from durable Session Journal state.
- Durable event identity, monotonic session sequence, checksums/idempotency and fenced orchestration leases.
- Durable queued steering with list/edit/remove/claim semantics.
- Ordered tool scheduler with bounded parallel read execution and mutation/process barriers.
- Provider-neutral native tool normalization with explicit compatibility fallback.
- Common journal-derived projection/checkpoint layer for activity/recovery/UI state.
- WorkspaceIndex for bounded file discovery rather than whole-repository context dumping.
- Loop/cancellation/recovery hardening and safe interrupted settlement.

### Safe Harness-style tools

ForgeX now exposes/normalizes a safe Harness-like logical surface including read, write, edit, list, glob, grep, bounded bash/pwsh, todo_write, skill, memory_search, subagent, build, simulate, present, session_event_read, session_event_search and session_trace.

All model-facing calls are normalized into ForgeX-owned contracts before validation and execution. Physical flash/reset authority is not exposed directly to the model.

### Workspace + UI/UX

- NexusShell is now the default product shell.
- Left navigation is workspace/session-first with the active project summarized in place.
- The center remains a focused Chat/Agent conversation with a compact live work journal.
- A resizable right Workspace dock hosts Files, Changes, Build, Device and Terminal.
- Workspace dock surfaces were flattened and densified to avoid dashboard/card overload.
- Agent activity rows are compact, expandable, evidence-backed and derived from real runtime events.
- Durable queued guidance is attached to the composer and can be edited/removed before claim.
- Build/device/serial screens use restrained hierarchy suited to a contextual dock.
- Workspace shortcuts: Ctrl/Cmd+Shift+E (Files), Ctrl/Cmd+Shift+G (Changes), Ctrl/Cmd+` (Terminal).

### Hardware safety

- Builds carry stable identity including build/workspace/source/artifact information.
- Flash approval remains host-owned and tied to the exact build/workspace/device binding.
- Device identity prefers USB serial + VID/PID, then weaker fallbacks that remain explicit.
- Planning, editing, build, test and simulation remain board-independent.
- ChangeSet staging, project containment and workspace revision checks remain authoritative.

### Codex CLI removal

Codex CLI/OAuth bridge support has been removed from the active product, provider registry, model router, frontend readiness UI, scripts and tests. A source scan of backend/frontend/electron/scripts/tests finds no Codex CLI/OAuth bridge references.

## Validation

- Python backend: **1529 passed / 1529 total**.
- Python bytecode compile: passed (`python -m compileall backend scripts`).
- Frontend TS/TSX source transpile: **101 files, 0 syntax diagnostics**.
- Dependency-free frontend state tests: **58 passed / 58 total**.
- Active Codex CLI/OAuth source scan: no matches.

### Frontend production-build limitation

The uploaded source archive contained an incomplete frontend dependency tree. A clean `npm ci` was attempted but the execution environment timed out while fetching dependencies. For that reason, a real Next.js production build and Playwright/Electron visual run could not be certified here. The deliverable intentionally excludes `node_modules`; run `npm ci` in `frontend/` on a networked development machine before `npm run typecheck`, `npm run build`, and Playwright validation.

## Recommended local final gate

```bash
python -m pytest -q
cd frontend
npm ci
npm run typecheck
npm run lint -- --max-warnings=0
npm run build
cd ..
npm run build:electron
npm run test:electron
npm run test:conversations
```

Physical ESP32 flash sign-off should be performed only after the software gates above pass on the target development machine.
