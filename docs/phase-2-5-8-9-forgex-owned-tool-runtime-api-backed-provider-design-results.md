# Phase 2.5.8.9 ForgeX-Owned Tool Runtime and API-Backed Provider Design Results

## Phase 2.5.8.10 continuation

The design-only boundary has been extended by one disabled OpenAI API planner spike. The fake provider remains supported. The concrete adapter returns the same internal ToolPlan and is not admitted to normal routing or production eligibility. Live authorization and transport validation are deferred; the guarded no-key result is `API_PROVIDER_KEY_MISSING`.

## Result

ForgeX now owns the tool runtime and validates a fake API-backed provider through sandbox -> tool execution -> diff -> review without touching the active workspace. Real API providers remain disabled design placeholders.

## Phase questions

**Q1. What local CLI execution paths are paused/removed from routing?**  
AGY and Codex CLI are paused with execution and routing denied. Claude CLI is disabled. OpenCode remains reference-only and is not a provider. Generic routing rejects these states; detection and isolated legacy QA helpers may remain.

**Q2. What ForgeX-owned tools are introduced?**  
`list_files`, `read_file`, `write_file`, and `edit_file_simple`; `create_review` is runtime-owned and available only after exact diff validation.

**Q3. How are tool calls validated?**  
Plans use strict tool and argument schemas. The policy validates tool authority, exact keys/types, path form, containment, extension, sensitive filenames, file/content bounds, text encoding, and smoke-specific filename/content before any call executes.

**Q4. How is sandbox containment enforced?**  
The runtime creates a unique child under a managed sandbox root by copying the active baseline. Tool targets must be relative, traversal-free, drive-free, non-link/reparse paths whose resolved location remains beneath that immutable sandbox root.

**Q5. How does a model response become a review candidate?**  
ForgeX requests a memory-only `ToolPlan`, validates all calls, executes allowed tools, snapshots the result, verifies exactly one expected created file and content, verifies active-workspace and marker integrity, and then invokes the existing Bridge Review service.

**Q6. How does ForgeX prevent active workspace mutation?**  
No tool receives the active root. Every tool is rooted in the managed sandbox. The active workspace is hash-snapshotted before planning and compared after execution; any change aborts review creation.

**Q7. How does patch export / verify / preflight remain unchanged?**  
The existing Bridge patch services are not replaced or weakened. An optional post-review hook can invoke them only after a review ID exists. The fake smoke does not apply a patch and does not build or flash.

**Q8. What API-backed provider abstraction is designed?**  
`ApiBackedProvider` defines provider/model identity, structured-call, streaming and JSON-schema capabilities, input/output bounds, and `request_plan(task, sandbox_manifest, allowed_tools, policy) -> ToolPlan`.

**Q9. What fake provider validates the runtime?**  
`fake_api_provider` is an in-process, network-free, key-free provider returning one exact `write_file` call for `FORGEX_TOOL_RUNTIME_SMOKE.txt`.

**Q10. Is ForgeX ready for real API provider integration next?**  
Yes for a constrained adapter spike against this runtime boundary; no provider is production eligible. Authentication, transport, retries, schema enforcement at the remote boundary, rate limits, streaming, and provider-specific safety still require implementation and tests.

## Smoke result

The guarded smoke classification is `TOOL_RUNTIME_PASS`: one file created in the disposable sandbox, one review candidate created, active workspace and marker unchanged, and apply/build/flash not run. Patch export/verify/preflight were not run by the smoke; sequencing is covered by the runtime tests.

## Verification

| Check | Result |
| --- | --- |
| Focused tool/runtime/provider/security selector | 72 passed, 1 host-limited symlink skip, 1,604 deselected |
| Fake-provider smoke | `TOOL_RUNTIME_PASS`; one created file and one review |
| Safety scan | 143 passed, 0 failed |
| Backend compile | PASS |
| Unmodified full backend suite | 1,666 passed, 10 skipped, 1 unrelated failure because the local `.env` overrides the documented timeout to 300 seconds |
| Process-scoped 180-second full backend suite | 1,667 passed, 10 skipped |
| Frontend typecheck | PASS |
| Frontend production build | PASS; four static pages |
| Electron build | PASS |
| Electron tests | 10 passed |

## Remaining limitation and next phase

This phase proves the internal runtime with a deterministic fake provider, not real network behavior. The recommended next phase is **Phase 2.5.8.10 — Real API Provider Adapter Spike**, retaining disabled-by-default routing and the same ForgeX-owned tool and review gates.
