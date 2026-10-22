# Phase 2.5.8.11 Agent Runtime Product Integration Results

## Result

The ForgeX-owned runtime is connected to a disabled-by-default FastAPI and Agent UI product path. A fake planner completes a bounded two-turn session: it requests one `write_file`, receives a sanitized completion result, returns a final ToolPlan, and produces one persistent Bridge Review after exact sandbox validation. The active workspace remains unchanged.

## Product API

- `GET /agent-runtime/providers`
- `POST /agent-runtime/runs`
- `GET /agent-runtime/runs/{run_id}`
- `GET /agent-runtime/runs/{run_id}/events`
- `POST /agent-runtime/runs/{run_id}/cancel`
- `GET /agent-runtime/runs/{run_id}/review`

The runtime requires `FORGEX_ENABLE_AGENT_RUNTIME=1`. The fake planner requires `FORGEX_ENABLE_AGENT_RUNTIME_FAKE_PROVIDER=1`. API planners remain non-routeable in this phase.

## ToolPlan and limits

The canonical schema is `forgex.toolplan.v1` with a bounded summary, tool calls, and final flag. Product limits default to five turns, five calls per turn, twenty total calls, 120 seconds, bounded created/modified counts, and 128 KiB per file. Only `write_file` is offered to the product planner.

## Repository hygiene

The initial worktree had 56 tracked modifications and 5,871 untracked files; 5,384 untracked files were under `.promptforge`. `.gitignore` now excludes the complete generated `.promptforge` tree plus `out`, coverage output, and logs. Existing files were not deleted or untracked. Cleanup and intentional commits remain required before release.

## Timeout isolation

The documented LLM timeout default remains 180 seconds. Automated tests now set that default in the test process before application dotenv loading, so the developer-local `.env` override of 300 seconds no longer changes test expectations.

## Verification

- Required agent/runtime/review selector: 104 passed, 1 skipped, 1,619 deselected.
- Timeout/config selector: 127 passed, 1,597 deselected.
- Narrow product/config regression: 29 passed.
- Full backend: 1,714 passed, 10 skipped.
- Safety scan: 171 passed.
- Frontend typecheck and production build: passed; four static pages generated.
- Electron build and tests: passed; 10 tests.
- Fake product smoke: `TOOL_RUNTIME_PASS`, 1 created, 0 modified, 0 deleted, persistent review created, active workspace unchanged.

No AGY, Codex, Claude, OpenCode, or live API call was executed.

## Remaining limitation

Phase 2.5.8.12 removes this limitation for mocked planner transports by registering Gemini, Groq, OpenRouter, OpenAI, and NVIDIA NIM as disabled-by-default API planner providers. Live provider smoke remains gated behind operator keys, provider flags, and explicit confirmation.
