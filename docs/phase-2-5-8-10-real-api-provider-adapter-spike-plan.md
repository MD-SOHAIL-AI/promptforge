# Phase 2.5.8.10 Real API Provider Adapter Spike Plan

## Objective

Add one disabled OpenAI API planner adapter without giving the remote model filesystem or execution authority. The adapter returns the same internal `ToolPlan` used by the fake provider; the existing ForgeX runtime remains the only filesystem executor.

## Scope

1. Extend `ApiBackedProvider` with keyword-only task, sanitized sandbox manifest, allowed tools, policy, and run ID inputs.
2. Define a strict root-object ToolPlan schema containing exactly one smoke `write_file` call.
3. Add a non-streaming OpenAI Responses API adapter using `gpt-4.1-mini` by default.
4. Require explicit confirmation plus both disabled-by-default feature flags before checking the process environment for API authorization.
5. Use bounded HTTP response reads, `store: false`, strict JSON parsing, and provider-neutral safe classifications.
6. Add a guarded QA command and a safe no-key path that performs no request or tool execution.
7. Test all provider behavior with injected mock transports; automated tests must perform no real request.
8. Preserve exact-diff review creation and post-review-only patch sequencing. Apply, build, and flash remain unavailable.

AGY and Codex remain paused, Claude CLI remains disabled, and OpenCode remains reference-only.
