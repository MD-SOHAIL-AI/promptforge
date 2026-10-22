# Phase 2.5.8.11 Agent Runtime Product Integration Plan

## Objective

Connect the proven ForgeX-owned tool runtime to FastAPI and the Agent UI without giving a model, local CLI, or provider direct filesystem authority.

## Work

1. Isolate automated tests from developer-local timeout overrides and ignore generated runtime state.
2. Add `forgex.toolplan.v1`, bounded product limits, and a compatibility planner registry.
3. Add a deterministic two-turn fake planner: one `write_file` call followed by a final response after receiving the sanitized tool result.
4. Extend `ForgeXToolRuntime` with one-sandbox iterative execution and persistent Bridge Review creation.
5. Add feature-gated product routes for providers, start, detail, events, cancellation, and review.
6. Connect the existing Agent workspace tab to the product routes.
7. Extend tests and safety scanning; execute no local CLI or live API provider.

## Safety invariants

- Active workspace is snapshot-checked and never written.
- Provider output is strict JSON data, not execution authority.
- Only ForgeX-owned sandbox tools execute.
- No prompt, raw provider response, secret, stdout, or stderr persistence.
- Review is post-diff only; apply, build, and flash remain explicit downstream actions.
