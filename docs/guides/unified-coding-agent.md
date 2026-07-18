# Unified Coding Agent Guide

The Unified Coding Agent is an experimental ForgeX workflow for review-only coding proposals followed by explicit user-controlled apply, build, flash, and monitor stages.

## Enable Fake Provider

Use the fake provider for local demos and automated-safe smoke tests:

```env
FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW=1
FORGEX_ENABLE_FAKE_API_CODING_AGENT=1
FORGEX_ENABLE_REAL_API_CODING_AGENT=0
FORGEX_ENABLE_CODING_AGENT_REPAIR_LOOP=0
```

Restart the backend after changing flags.

## Enable Real API Provider

Real API mode is disabled by default and may call a configured remote or paid provider through `model_router`.

```env
FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW=1
FORGEX_ENABLE_REAL_API_CODING_AGENT=1
```

Configure provider credentials through the normal model provider settings. Do not place API keys in prompts, docs, workflow reasons, or context files.

The UI requires a non-persistent confirmation before real API review generation:

```text
This may call a configured remote/paid model provider through model_router.
I understand and want to generate a review.
```

The confirmation resets when provider, model, context mode, or selected files change.

## Configure Model Providers

Open Model Settings, configure a real API provider, and confirm its cached metadata shows a healthy provider/model. The coding workflow readiness route checks provider metadata only; it does not send prompt or context.

## Preview Context

In Real API Provider mode:

1. Select `selected_files` or `project_summary`.
2. For `selected_files`, click `Load Files`.
3. Search and select safe project files with checkboxes.
4. Click `Preview Context`.

Context preview and context file listing do not call model providers and do not return file bodies to the UI. Secret, generated, unsupported, oversized, binary, and unsafe paths are disabled or excluded.

## Generate Review

Fake provider mode creates a deterministic review without model calls.

Real API mode requires:

- Unified workflow flag enabled.
- Real API flag enabled.
- Healthy provider readiness.
- Selected files when using `selected_files` context.
- Live API confirmation checkbox.

Generation creates a ForgeX review and stops at `awaiting_apply`. It does not apply changes to the active workspace.

## Approve, Apply, Build, Flash, Monitor

Each stage requires an explicit button click:

- `Approve Apply` applies the reviewed diff and stops at `awaiting_build`.
- `Build` runs the existing V1 build behavior and stops at `awaiting_flash`.
- `Flash` runs the existing V1 flash behavior and stops at `awaiting_monitor`.
- `Monitor` runs the existing V1 monitor behavior and completes the workflow.

There is no auto-advance and no run-all action.

## Repair Loop

If a workflow fails at build, the UI shows a build-failure repair card with safe failure code/message, attempt count, and a `Generate Build Repair Review` button.

Repair requires:

```env
FORGEX_ENABLE_CODING_AGENT_REPAIR_LOOP=1
FORGEX_ENABLE_REAL_API_CODING_AGENT=1
```

Repair generation is review-only. It creates a new repair run paused at `awaiting_apply`; it does not auto-apply, build, flash, or monitor.

## Cancel And Recovery

Cancel is available only for safe waiting states and selected failed repairable states. Completed workflows and active physical operations are not cancelled.

Recovery is manual:

- `GET /models/coding-workflow/recovery/stale` lists stale in-progress runs and stale operation locks.
- `POST /models/coding-workflow/{run_id}/recovery/mark-failed` marks a stale in-progress run failed after explicit confirmation.
- `POST /models/coding-workflow/{run_id}/recovery/clear-lock` clears an expired operation lock after explicit confirmation.

Recovery does not delete files, roll back changes, or infer hardware state.

## Troubleshooting

- `UNIFIED_CODING_WORKFLOW_DISABLED`: enable `FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW=1`.
- `FAKE_API_CODING_AGENT_DISABLED`: enable fake provider flag for fake mode.
- `REAL_API_CODING_AGENT_DISABLED`: enable real API flag for real provider mode.
- `REAL_API_CODING_AGENT_CONFIRMATION_REQUIRED`: tick the live API acknowledgement.
- `REAL_API_CODING_AGENT_UNAVAILABLE`: configure a healthy provider in Model Settings.
- `API_CODING_AGENT_CONTRACT_INVALID`: the model did not return valid ForgeX coding-agent JSON. Try a different model, simplify the request, or use `project_summary` context.
- `CODING_WORKFLOW_OPERATION_IN_PROGRESS`: another operation or stale lock exists for the run. Refresh runs/recovery before retrying.
- `CODING_WORKFLOW_REPAIR_DISABLED`: enable the repair loop flag before generating build repair reviews.
- `CODING_WORKFLOW_CANCEL_NOT_ALLOWED`: the current state is not safe to cancel.
- `CODING_WORKFLOW_RECOVERY_NOT_ALLOWED`: the run is not stale or not in an in-progress state.
- `CODING_WORKFLOW_LOCK_CLEAR_NOT_ALLOWED`: the lock is not stale or no lock exists.
