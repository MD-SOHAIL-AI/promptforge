# Unified Coding Agent MVP Status

Date: 2026-07-08

This document is the current stabilization inventory for the ForgeX V2 unified coding-agent MVP. It is a review and commit-prep checklist, not a feature specification.

Phase 21-25 update: the MVP now includes live API manual confirmation, stricter real-provider JSON instructions, clearer build-repair UX, process-safe local workflow locks, a safe context file picker route, and production-facing docs. These phases do not add auto-advance, auto-apply, auto-build, auto-flash, auto-monitor, destructive recovery, or live API calls in automated tests.

## Implemented capabilities

- Dev-only fake coding provider that creates review records without real API calls.
- Real API coding provider path through `model_router`, gated separately from the unified workflow.
- Bounded API context builder and context preview.
- Provider readiness check for real API coding workflows.
- Review-only generation; model output is converted into a ForgeX review and then stops.
- Manual apply, build, flash, and monitor gates.
- Build-failure repair loop that creates a new review-only repair run.
- Per-run operation locking for state-changing workflow operations, backed by local process-safe lock files.
- Explicit cancellation for safe waiting states.
- Stale in-progress run detection and manual mark-failed recovery.
- Stale operation lock detection and explicit manual lock clearing.
- Real API generation requires an explicit live-provider acknowledgement.
- Context selection uses a body-free project file picker for selected-files mode.
- Frontend Coding Agent panel in the IDE.

## Feature flags

All MVP workflow flags are disabled by default in `.env.example`:

```env
FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW=0
FORGEX_ENABLE_FAKE_API_CODING_AGENT=0
FORGEX_ENABLE_REAL_API_CODING_AGENT=0
FORGEX_ENABLE_CODING_AGENT_REPAIR_LOOP=0
```

No experimental unified coding-agent workflow feature is enabled by default.

## Backend routes

Unified coding workflow routes:

- `POST /models/coding-workflow/fake/generate-review`
- `POST /models/coding-workflow/api/generate-review`
- `GET  /models/coding-workflow/api/status`
- `POST /models/coding-workflow/context/preview`
- `POST /models/coding-workflow/context/files`
- `POST /models/coding-workflow/{run_id}/approve-apply`
- `POST /models/coding-workflow/{run_id}/build`
- `POST /models/coding-workflow/{run_id}/flash`
- `POST /models/coding-workflow/{run_id}/monitor`
- `POST /models/coding-workflow/{run_id}/repair/build`
- `POST /models/coding-workflow/{run_id}/cancel`
- `GET  /models/coding-workflow/recovery/stale`
- `POST /models/coding-workflow/{run_id}/recovery/mark-failed`
- `POST /models/coding-workflow/{run_id}/recovery/clear-lock`
- `GET  /models/coding-workflow`
- `GET  /models/coding-workflow/{run_id}`
- `GET  /models/coding-workflow/{run_id}/events`

Legacy direct fake endpoint retained:

- `POST /models/api-coding-agent/fake/generate-review`

## Frontend files

- `frontend/components/ide/coding-workflow/UnifiedCodingWorkflowPanel.tsx`
- `frontend/lib/coding-workflow-api.ts`
- `frontend/types/index.ts`
- `frontend/components/ide/forgex-shell.tsx`

Related model/provider settings UI from adjacent provider work:

- `frontend/components/ide/model-settings-panel.tsx`
- `frontend/components/ide/model-settings/`

## Backend services

Agent runtime and workflow services:

- `backend/agent_runtime/api_agent_contracts.py`
- `backend/agent_runtime/api_coding_agent_fake.py`
- `backend/agent_runtime/api_coding_agent_service.py`
- `backend/agent_runtime/api_coding_context.py`
- `backend/agent_runtime/coding_provider_contracts.py`
- `backend/agent_runtime/coding_provider_registry.py`
- `backend/agent_runtime/fake_api_coding_provider_adapter.py`
- `backend/agent_runtime/real_api_coding_provider_adapter.py`
- `backend/agent_runtime/coding_workflow_store.py`
- `backend/agent_runtime/coding_workflow_locks.py`
- `backend/agent_runtime/coding_workflow_service.py`
- `backend/agent_runtime/coding_workflow_build_service.py`
- `backend/agent_runtime/coding_workflow_flash_service.py`
- `backend/agent_runtime/coding_workflow_monitor_service.py`
- `backend/agent_runtime/coding_workflow_repair_service.py`
- `backend/workflow/adapters/coding_agent_adapter.py`

API route and shared dependency files:

- `backend/api/app.py`
- `backend/api/routes/_model_common.py`
- `backend/api/routes/api_coding_agent.py`
- `backend/api/routes/coding_workflow.py`

Related provider/review infrastructure from adjacent phases:

- `backend/api/routes/model_providers.py`
- `backend/api/routes/model_routes.py`
- `backend/api/routes/provider_reviews.py`
- `backend/api/routes/provider_patches.py`
- `backend/api/routes/bridge_safety.py`
- `backend/bridges/patch_export_service.py`
- `backend/bridges/review_models.py`
- `backend/model_router/storage.py`
- `backend/provider_runtime/templates.py`

## Tests

Core workflow integration tests:

- `tests/integration/test_coding_workflow_routes.py`
- `tests/integration/test_unified_coding_workflow_full_chain.py`
- `tests/integration/test_coding_workflow_real_api_route.py`
- `tests/integration/test_coding_workflow_real_api_status_route.py`
- `tests/integration/test_coding_workflow_context_preview_route.py`
- `tests/integration/test_coding_workflow_context_files_route.py`
- `tests/integration/test_coding_workflow_repair_route.py`
- `tests/integration/test_coding_workflow_recovery_routes.py`
- `tests/integration/test_api_coding_agent_fake_route.py`

Core unit tests:

- `tests/unit/test_coding_workflow_store.py`
- `tests/unit/test_coding_workflow_locks.py`
- `tests/unit/test_coding_workflow_apply_resume.py`
- `tests/unit/test_coding_workflow_build_resume.py`
- `tests/unit/test_coding_workflow_flash_resume.py`
- `tests/unit/test_coding_workflow_monitor_resume.py`
- `tests/unit/test_coding_workflow_repair_service.py`
- `tests/unit/test_coding_workflow_recovery.py`
- `tests/unit/test_api_coding_context.py`
- `tests/unit/test_real_api_coding_provider_adapter.py`
- `tests/unit/test_fake_api_coding_provider_adapter.py`
- `tests/unit/test_api_coding_agent_contracts.py`
- `tests/unit/test_api_coding_agent_service.py`
- `tests/unit/test_coding_agent_workflow_adapter.py`
- `tests/unit/test_coding_provider_contracts.py`
- `tests/unit/test_coding_provider_registry.py`

Provider and safety regression tests:

- `tests/unit/test_provider_strategy_hardening.py`
- `tests/unit/test_codex_status.py`
- `tests/unit/test_codex_login.py`
- `tests/unit/test_model_router.py`
- `tests/unit/test_model_provider_storage.py`
- `tests/unit/test_provider_runtime_contracts.py`

## Safety checklist

- No model output directly mutates the active workspace.
- No command suggestions are executed.
- No auto-apply, auto-build, auto-flash, or auto-monitor.
- All hardware-facing stages require explicit user action.
- Real API generation uses the bounded context builder.
- Context preview does not call providers.
- Provider readiness does not send prompt or context.
- Live API generation requires explicit non-persistent user confirmation.
- Repair loop is review-only.
- Recovery is manual and non-destructive, including stale lock clearing.
- Context file listing returns relative metadata only and no file bodies.
- Raw source, raw context, raw compiler logs, raw serial logs, and raw model output are not persisted in workflow JSONL.

## Known limitations

- Workflow locks use local atomic lock files suitable for desktop backend restarts and same-host multi-process protection. Distributed deployments would still need a shared lock backend.
- Stale lock clearing is manual and only allowed after the lock expires.
- Stale recovery only marks a run failed after explicit confirmation; it does not infer whether an external operation completed.
- Cancellation is limited to safe waiting states and selected failed states. Active flash and monitor operations are not cancelled unless a future adapter exposes safe cancellation.
- Real API coding workflow remains experimental and feature-flagged.
- The frontend recovery section is manual; it does not poll or auto-recover.
- Commit isolation is complicated by the current worktree containing unrelated dirty files and generated workspace deletions.

## Manual smoke steps

1. Set `FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW=1` and `FORGEX_ENABLE_FAKE_API_CODING_AGENT=1`.
2. Start backend and frontend locally.
3. Open the IDE Coding Agent panel with a safe test workspace.
4. Generate a fake-provider review and confirm the workspace is unchanged.
5. Approve apply and confirm the run stops at `awaiting_build`.
6. Run build and confirm the run stops at `awaiting_flash`.
7. Run flash only with an explicit port and board.
8. Run monitor and confirm the workflow completes with bounded output preview.
9. Generate a build failure in a test workspace, then generate a repair review and confirm it creates a new `awaiting_apply` run.
10. Cancel a waiting run and confirm it cannot continue.
11. Inspect stale recovery and only mark failed using explicit confirmation in a seeded stale test run.
12. Repeat real API readiness, load context files, and preview context with `FORGEX_ENABLE_REAL_API_CODING_AGENT=1`.
13. For a deliberate real API smoke only, tick the live-provider acknowledgement before Generate Review. Do not include API keys in prompts or docs.
14. For stale lock recovery, inspect `/models/coding-workflow/recovery/stale` or the UI recovery section and clear only expired locks with explicit confirmation.

## Commit file checklist

Backend `agent_runtime`:

- `backend/agent_runtime/api_agent_contracts.py`
- `backend/agent_runtime/api_coding_agent_fake.py`
- `backend/agent_runtime/api_coding_agent_service.py`
- `backend/agent_runtime/api_coding_context.py`
- `backend/agent_runtime/coding_provider_contracts.py`
- `backend/agent_runtime/coding_provider_registry.py`
- `backend/agent_runtime/fake_api_coding_provider_adapter.py`
- `backend/agent_runtime/real_api_coding_provider_adapter.py`
- `backend/agent_runtime/coding_workflow_store.py`
- `backend/agent_runtime/coding_workflow_locks.py`
- `backend/agent_runtime/coding_workflow_service.py`
- `backend/agent_runtime/coding_workflow_build_service.py`
- `backend/agent_runtime/coding_workflow_flash_service.py`
- `backend/agent_runtime/coding_workflow_monitor_service.py`
- `backend/agent_runtime/coding_workflow_repair_service.py`
- `backend/workflow/adapters/coding_agent_adapter.py`

Backend API routes:

- `backend/api/app.py`
- `backend/api/routes/_model_common.py`
- `backend/api/routes/api_coding_agent.py`
- `backend/api/routes/coding_workflow.py`
- `backend/api/routes/model_providers.py`
- `backend/api/routes/model_routes.py`
- `backend/api/routes/provider_reviews.py`
- `backend/api/routes/provider_patches.py`
- `backend/api/routes/bridge_safety.py`

Frontend:

- `frontend/components/ide/coding-workflow/UnifiedCodingWorkflowPanel.tsx`
- `frontend/components/ide/forgex-shell.tsx`
- `frontend/lib/coding-workflow-api.ts`
- `frontend/types/index.ts`
- `frontend/components/ide/model-settings-panel.tsx`
- `frontend/components/ide/model-settings/`

Tests:

- `tests/integration/test_api_coding_agent_fake_route.py`
- `tests/integration/test_coding_workflow_context_preview_route.py`
- `tests/integration/test_coding_workflow_context_files_route.py`
- `tests/integration/test_coding_workflow_real_api_route.py`
- `tests/integration/test_coding_workflow_real_api_status_route.py`
- `tests/integration/test_coding_workflow_recovery_routes.py`
- `tests/integration/test_coding_workflow_repair_route.py`
- `tests/integration/test_coding_workflow_routes.py`
- `tests/integration/test_unified_coding_workflow_full_chain.py`
- `tests/unit/test_api_coding_agent_contracts.py`
- `tests/unit/test_api_coding_agent_service.py`
- `tests/unit/test_api_coding_context.py`
- `tests/unit/test_coding_agent_workflow_adapter.py`
- `tests/unit/test_coding_provider_contracts.py`
- `tests/unit/test_coding_provider_registry.py`
- `tests/unit/test_coding_workflow_apply_resume.py`
- `tests/unit/test_coding_workflow_build_resume.py`
- `tests/unit/test_coding_workflow_flash_resume.py`
- `tests/unit/test_coding_workflow_monitor_resume.py`
- `tests/unit/test_coding_workflow_recovery.py`
- `tests/unit/test_coding_workflow_repair_service.py`
- `tests/unit/test_coding_workflow_store.py`
- `tests/unit/test_coding_workflow_locks.py`
- `tests/unit/test_fake_api_coding_provider_adapter.py`
- `tests/unit/test_real_api_coding_provider_adapter.py`

Docs:

- `docs/architecture/api-coding-agent.md`
- `docs/architecture/unified-coding-agent-workflow.md`
- `docs/architecture/unified-coding-agent-mvp-status.md`

Env/config:

- `.env.example`

Suspicious or unrelated dirty/untracked files to review separately before staging:

- `.promptforge/*.log` deletions.
- `workspace/artifacts/**`, `workspace/builds/**`, and `workspace/projects/**` deletions.
- `workspace/forgex-apply-test/` and `workspace/phase-2-3-empty-folder/`.
- `frontend/tsconfig.tsbuildinfo` deletion.
- `promptforge-promo-video/`.
- `FORGEX_BOARD_SYSTEM.md`, `README.md`, `backend/api/routes/devices.py`, `backend/api/routes/flash.py`, `backend/api/routes/models.py`, `backend/bridges/codex_status.py`, `backend/runtime/serial_runtime.py`, `backend/tools/board_detector.py`, `backend/tools/flash_firmware.py`, `scripts/qa-agy-generic-live-core.mjs`, and `scripts/qa-agy-generic-live.test.mjs` need owner review if they are not part of adjacent provider/runtime phases.

## Validation matrix

Backend compile:

```bash
python -m compileall -q backend tests
```

Core workflow tests:

```bash
pytest -q tests/integration/test_coding_workflow_routes.py
pytest -q tests/integration/test_unified_coding_workflow_full_chain.py
pytest -q tests/integration/test_coding_workflow_real_api_route.py
pytest -q tests/integration/test_coding_workflow_real_api_status_route.py
pytest -q tests/integration/test_coding_workflow_context_preview_route.py
pytest -q tests/integration/test_coding_workflow_context_files_route.py
pytest -q tests/integration/test_coding_workflow_repair_route.py
pytest -q tests/integration/test_coding_workflow_recovery_routes.py
```

Core unit tests:

```bash
pytest -q tests/unit/test_coding_workflow_store.py
pytest -q tests/unit/test_coding_workflow_locks.py
pytest -q tests/unit/test_api_coding_context.py
pytest -q tests/unit/test_real_api_coding_provider_adapter.py
pytest -q tests/unit/test_coding_workflow_repair_service.py
pytest -q tests/unit/test_coding_workflow_recovery.py
```

Provider/fake tests:

```bash
pytest -q tests/unit/test_coding_provider_contracts.py tests/unit/test_coding_provider_registry.py tests/unit/test_fake_api_coding_provider_adapter.py tests/unit/test_api_coding_agent_contracts.py tests/unit/test_api_coding_agent_service.py tests/integration/test_api_coding_agent_fake_route.py
```

Backend safety suite:

```bash
pytest -q tests/unit/test_provider_strategy_hardening.py tests/unit/test_codex_status.py tests/unit/test_codex_login.py tests/unit/test_model_router.py tests/unit/test_model_provider_storage.py tests/unit/test_provider_runtime_contracts.py
```

Frontend:

```bash
cd frontend
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
cd ..
```

Git checks:

```bash
git status --short
git diff --check
git diff --stat
```
