# Phase 2.5.8.5 Generic Run API and Agent UI Plan

## Scope

Expose the proven generic coordinator only inside the existing loopback desktop boundary. AGY is the sole supported provider. Codex, Claude, and OpenCode execution remain disabled. Apply, build, and flash remain separate explicit workflows.

## Delivery plan

1. Add an immutable five-gate public execution policy: existing AGY bridge, generic API, generic routing, AGY generic provider, and generic cutover.
2. Add strict public request/response schemas that never serialize instructions, provider output, commands, environment, paths, storage references, patch content, or stack traces.
3. Preserve the existing AGY polling routes. Use `POST /models/bridges/runs` for generic submission and `/models/bridges/generic/runs` for sanitized list/detail/cancel reads. Add the required `/models/bridges/runs/{run_id}/events` SSE endpoint.
4. Resolve project identity server-side, create exactly one managed sandbox through the existing compatibility router, persist before provider execution, and use the idempotency key as the persisted correlation identity.
5. Add ordered SSE replay, bounded history, `Last-Event-ID`, explicit `resync_required`, heartbeats, slow-client isolation, disconnect cleanup, terminal close, and polling compatibility.
6. Integrate one Agent tab into the established ForgeX right panel. Keep AGY as the only option and reuse the existing bridge review UI.
7. Add fake-provider API/state tests, extend the safety scan, add a confirmation-gated live command, and capture sanitized visual QA states.

## Route compatibility

The existing legacy endpoints remain unchanged:

- `POST /models/bridges/antigravity/sandbox-run`
- `GET /models/bridges/runs`
- `GET /models/bridges/runs/{run_id}`
- `POST /models/bridges/runs/{run_id}/cancel`

The generic public routes are:

- `POST /models/bridges/runs`
- `GET /models/bridges/generic/runs`
- `GET /models/bridges/generic/runs/{run_id}`
- `POST /models/bridges/generic/runs/{run_id}/cancel`
- `GET /models/bridges/runs/{run_id}/events`
- `GET /models/bridges/generic/runs/{run_id}/events`
- `GET /models/bridges/providers`

The split avoids changing the existing legacy AGY response contract while keeping every generic read sanitized.

## Live boundary

`npm.cmd run qa:agy-generic-live -- --confirm-real-agy` is the only live launcher. It additionally requires QA mode, the throwaway marker, an operator throwaway attestation, all five execution gates, an authentication attestation, and an installed AGY command. Ordinary tests never invoke AGY.

