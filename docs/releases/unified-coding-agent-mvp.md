# Unified Coding Agent MVP Release Notes

Date: 2026-07-08

## Summary

The Unified Coding Agent MVP is an experimental, disabled-by-default ForgeX workflow for review-only firmware code generation followed by explicit manual apply, build, flash, and monitor gates. The Phase 21-25 hardening pack adds safer real API use, clearer build repair UX, process-safe local workflow locks, a context file picker, and production-facing documentation.

## Capabilities

- Fake/dev provider for deterministic review generation without model calls.
- Real API provider through `model_router`, using bounded context only.
- Context preview and safe context file listing without provider calls.
- Review-only generation with strict `forgex.api_coding_agent.v1` parsing.
- Manual apply, build, flash, and monitor stages.
- Build-failure repair reviews that create new review-only runs.
- Cancellation for safe waiting states.
- Stale in-progress run detection and manual mark-failed recovery.
- Process-safe local operation locks and manual stale-lock clearing.

## Feature Flags

All flags remain disabled by default:

```env
FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW=0
FORGEX_ENABLE_FAKE_API_CODING_AGENT=0
FORGEX_ENABLE_REAL_API_CODING_AGENT=0
FORGEX_ENABLE_CODING_AGENT_REPAIR_LOOP=0
```

## Routes

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
- `POST /models/api-coding-agent/fake/generate-review`

## Frontend UX

The IDE Coding Agent panel supports fake and real provider modes, real API readiness refresh, live API confirmation, context file loading/search/checkbox selection, context preview, explicit workflow stage buttons, cancel, build repair review generation, and stale workflow/lock recovery refresh.

## Safety Model

- No model output directly mutates the active workspace.
- No command suggestions are executed.
- No auto-apply, auto-build, auto-flash, auto-monitor, or run-all behavior.
- Hardware-facing stages require explicit user action.
- Real API generation requires non-persistent manual acknowledgement.
- Context preview and file listing do not call model providers.
- Provider readiness does not send prompt or context.
- Raw source, context bodies, compiler logs, serial logs, provider traces, and raw model output are not persisted into workflow JSONL.
- Recovery is manual and non-destructive.

## Testing Matrix

Run the backend compile check, coding workflow integration suites, core unit suites, provider/fake tests, backend safety suite, frontend typecheck/lint/build, and git checks documented in `docs/architecture/unified-coding-agent-mvp-status.md`.

Automated tests use stubs and must not call live model providers.

## Known Limitations

- Local lock files protect same-host desktop and multi-process use, not distributed deployments.
- Stale recovery cannot know whether an external hardware operation completed; it only marks or clears state after explicit confirmation.
- Active flash/monitor cancellation is not attempted unless a future adapter exposes safe cancellation.
- Real API behavior depends on configured provider quality and valid JSON output.
- The UI recovery section is manual and does not auto-recover.

## Upgrade Notes

Keep all flags disabled by default in packaged and shared environments. Enable only the fake provider for local MVP demos unless a real API smoke is intentionally planned with configured credentials.

## Manual Smoke Guide

1. Enable unified workflow and fake provider flags.
2. Generate a fake review and confirm the active workspace remains unchanged.
3. Approve apply, then manually build, flash, and monitor as appropriate.
4. Trigger a build failure in a test workspace and generate a build repair review.
5. Cancel a waiting workflow and confirm it cannot continue.
6. Refresh recovery and verify stale candidates require explicit manual action.
7. For real API smoke, enable the real API flag, configure a provider, refresh readiness, load context files, preview context, tick live acknowledgement, then generate one review-only run.

## Rollback Notes

Disable `FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW` to hide the workflow routes from use. No automatic cleanup, file deletion, rollback, or workspace mutation is performed by recovery.
