# Phase 2.5.8.10A Operator API Key Validation and Real Smoke Plan

## Purpose

Validate one explicitly authorized OpenAI Responses API request without changing the planner/executor boundary. The remote model may return only the exact structured ToolPlan; ForgeX remains the sole filesystem actor.

## Guard sequence

1. Require provider `openai` and `--confirm-real-api`.
2. Require `FORGEX_ENABLE_API_PROVIDER_SPIKE=1` and `FORGEX_ENABLE_OPENAI_API_PROVIDER=1`.
3. Check only whether `OPENAI_API_KEY` exists in the process environment. Never print or persist its value.
4. Record zero outbound requests until the adapter invokes its single bounded transport call.
5. Request strict non-streaming JSON output with no hosted tools and no retries.
6. Reparse and validate the response locally, then pass the internal ToolPlan to the existing ForgeX sandbox runtime.
7. Require one exact created file, unchanged active workspace and marker, and a real Bridge Review before reporting pass.
8. Keep patch export, verification, and preflight optional and post-review only. Never apply, build, or flash.

Automated tests use injected transports and make no real request. If authorization is unavailable, complete the phase only as `API_PROVIDER_KEY_MISSING_CONFIRMED` and leave the real smoke pending.
