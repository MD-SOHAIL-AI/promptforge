# Phase 2.5.8.1 Generic Bridge Contracts Plan

## Objective

Define provider-neutral contracts and durable run semantics without connecting a provider or adding execution authority.

## Work Plan

1. Inspect detection, AGY execution, sandbox, review, patch, apply/rollback, audit, run, API, frontend, cancellation, desktop lifecycle, and safety-scan boundaries.
2. Add an isolated generic package inside the existing bridge package.
3. Define conservative capabilities, runtime-only requests, mandatory sandbox contexts, provider outcomes, events, artifact references, cancellation results, and stable errors.
4. Define an explicit canonical transition table and immutable terminal history.
5. Add atomic versioned metadata persistence with bounded retention and restart reconciliation.
6. Add an explicit non-routing registry and fake-provider contract tests.
7. Extend only the read-only safety diagnostics and safety scan.
8. Run focused tests, the backend suite, frontend build/typecheck, Electron verification, and safety scan.

## Non-Goals

- No provider adapter or AGY migration.
- No generic start, run, prompt, or streaming endpoint.
- No command execution, executable selection, or environment forwarding.
- No active-workspace agent access.
- No automatic patch application, build, flash, or rollback.

## Completion Gates

Completion requires exhaustive state transition tests, instruction leakage tests, containment tests, corrupt storage handling, disabled-provider diagnostics, unchanged AGY behavior, and all requested verification commands passing.
