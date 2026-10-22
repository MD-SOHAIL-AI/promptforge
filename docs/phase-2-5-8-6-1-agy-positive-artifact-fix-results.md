# Phase 2.5.8.6.1 AGY Positive Artifact Fix Results

## Decision

Phase 2.5.8.6.1 is **INCOMPLETE / BLOCKED BY AGY SCOPED TRUST**.

Readiness is `READY`, automated safety is green, and ForgeX now classifies zero-change completion safely. Real AGY 1.0.14 still did not create the expected sandbox artifact without an interactive per-folder trust/write approval. No safe scoped non-interactive override was found in local help or official documentation.

## Root-cause diagnosis

The original invocation, `agy -p`, is the documented non-interactive mode and receives the runtime instruction in the managed sandbox working directory. AGY uses project/workspace trust and defaults write-capable actions to review. ForgeX creates a unique sandbox directory for each run and intentionally provides no interactive stdin approval channel. AGY can therefore complete without performing a write.

Two fixed-flag alternatives were tested and rejected:

- New-project mode waited for project/trust interaction and reached the bounded timeout.
- Explicit added-workspace plus AGY terminal-sandbox mode returned a sanitized provider failure.

The final code retains the documented `-p` mode. It does not use the unsafe permission-bypass flag, modify global AGY settings, automate approval, or weaken ForgeX containment.

## Reproduction and live outcomes

| Run | Result | Run ID | Review ID | Changed files |
| --- | --- | --- | --- | --- |
| Original zero-change reproduction | completed with zero changes | `bridge-run-1dd96164811b422b81ceea6595a4e950` | `bridge-review-52898f7141d140138ba70b717315c3db` | 0 |
| New-project diagnostic | timed out safely | `bridge-run-079bad281c7840518e7b3ee8e2940c3a` | none | 0 |
| Explicit-workspace diagnostic | failed safely | `bridge-run-1eae246cd83b4b4d9fbd2235d35cf4a5` | none | 0 |

Total real AGY executions in this diagnostic phase: **3**. Each request created one generic run, one managed sandbox, and one AGY process. Successful positive-artifact executions: **0**.

The active throwaway workspace remained unchanged after every attempt. The expected smoke artifact was absent from both the active workspace and all three sandboxes.

## Implementation completed

- Added safe runner diagnostics using booleans, counts, length, hash, and enums only.
- Removed persisted stdout/stderr previews from new runner executions.
- Added three-attempt, 100-millisecond bounded diff settling after process exit.
- Added `completed_no_changes` at the legacy runner boundary.
- Added generic failure code `no_changes_produced` and the public message: “AGY completed but produced no sandbox changes.”
- Prevented zero-change runs from creating review or apply authority.
- Updated the live smoke instruction to target the current working directory and exactly one created file while remaining runtime-only.
- Preserved fixed argv, `shell=False`, managed-sandbox cwd, filtered environment, and no generic-to-legacy fallback.

## Patch and review pipeline

The reproduction created an empty compatibility review under the pre-fix behavior. The hardened zero-change behavior creates no review. No positive review existed, so patch export, patch verification, and preflight were not run. No patch was applied.

## Event, UI, and audit safety

The final diagnostic emitted five ordered events, one heartbeat, and exactly one terminal event. A metadata scan found no prompt/output fields, absolute paths, credentials, environment data, patch content, or file content.

The managed audit contained 24 metadata-only records and passed the same forbidden-data scan. Agent UI validation and screenshots were not available. Cancellation and timeout smokes were not run because the positive smoke did not pass.

## Verification

| Command | Result |
| --- | --- |
| Focused AGY selector | 9 passed, 1578 deselected |
| AGY runner unit tests | 15 passed |
| `python -m compileall backend` | PASS |
| `python -m pytest` | 1580 passed, 7 skipped |
| `npm.cmd --prefix frontend run typecheck` | PASS |
| `npm.cmd --prefix frontend run build` | PASS; 4 static pages |
| `npm.cmd run build:electron` | PASS |
| `npm.cmd run test:electron` | 10 passed |
| `npm.cmd run qa:safety-scan` | 62 passed, 0 failed |

## Remaining limitation

AGY 1.0.14 needs a trusted write-capable workspace for autonomous file mutation, but ForgeX sandboxes are intentionally unique and non-interactive. Completion requires an official scoped mechanism that can authorize only the current managed sandbox without global permission changes or arbitrary host access.

Recommended follow-up: Phase 2.5.8.6.2, scoped AGY workspace-trust integration. Phase 2.5.8.7 should begin only after the positive artifact path passes.

## Phase 2.5.8.6.2 trust-constraint confirmation

AGY 1.0.14 requires per-folder interactive trust/write approval. Unique temporary sandbox directories cannot be safely pre-authorized headlessly, and no safe scoped CLI override was found. ForgeX continues to reject global permission changes and `--dangerously-skip-permissions`.

The follow-up introduces a stable ForgeX-managed QA workspace that an operator can explicitly trust once. The active throwaway workspace is copied into that contained, marked, symlink-checked folder before each run. This preserves the existing no-apply review boundary while avoiding unique live AGY cwd folders. Real validation remains pending operator trust.
