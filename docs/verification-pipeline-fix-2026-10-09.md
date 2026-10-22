# Verification pipeline fix — 9 October 2026

The temperature-monitor failure involved ForgeX's requirement contract and the model's citations. The original run compiled successfully, but the contract treated the project title, `Requirements:` and `Provide:` as source obligations. Repeated invalid citations then stopped the run before independent review began. A separate defect made the complex completion reviewer send review JSON through the ordinary ToolPlan decoder and limited its summary to 1,200 characters.

## Implemented behavior

1. **Requirements and context.** Partition version 5 separates section labels and project titles from real obligations. Short requests receive the same treatment as long requests; fenced examples and headed state diagrams stay together. The original request remains available for generation and review. On continuation, migration preserves unchanged requirement IDs and revision history, retires obsolete heading obligations, and is idempotent.
2. **Current citations.** `read_file` returns raw text, numbered text, a full-file content hash, inclusive line bounds and a continuation offset. Responses fit the observation budget; oversized individual lines are explicitly marked. Observation rendering preserves citation metadata. Prompts direct the model to repair references from current lines and assess implementation instead of adding declarations merely to echo requirement wording.
3. **Both review gates.** Requirement review and whole-deliverable completion review use separate requests with the same strict, host-selected review schema. Each response must cover every requested ID exactly once. The completion gate assesses interfaces, timing, exclusions and consistency between source and upload instructions. The host supplies full cited files and local dependencies; large-project unit caches distinguish the two review scopes. Invalid responses receive bounded schema repair and cannot certify completion.
4. **Recovery and budgets.** Checkpoints retain submitted citations. Continuation and source edits revalidate them against current files, retaining valid references while exposing changed or missing evidence. Model review and executable evidence remain bound to the current revision. Complex tasks reserve four model attempts, output sized to their requirement count, and two review time allocations. Budget and deadline failures retain their own classifications.
5. **Truthful reporting.** Review starts activate the verification stage. Reports distinguish a passing first review from failed or interrupted completion review. Evidence displays optional tests as `Not requested`; compilation and physical-device evidence remain separate. The local recovery action is labelled **Build saved draft**. Failure messages distinguish citation rejection, unmet requirements, invalid review output and provider failures.

The historical 21-requirement contract was read from the saved run and migrated in memory: 18 real requirements remain, preserving their original IDs; `req-2`, `req-3` and `req-11` are retired. A second migration produced no changes. Historical run records and the original saved draft were retained.

## Validation

| Check | Result |
| --- | --- |
| Final focused Python unit/API suite | 121 passed |
| Additional canonical router, native-tool, recovery and orchestrator coverage | 141 passed in its run |
| UI state suite | 91 passed |
| Browser verification/recovery controls | 8 passed |
| Frontend typecheck and ESLint | Passed |
| Git whitespace check | Passed |

Regression coverage includes the original short temperature-monitor prompt, saved-contract migration, documentation/source typing, current overlay reads, pagination, oversized Unicode lines, literal headings inside examples, citation continuation, both canonical review gates in three response modes, and malformed completion JSON after a passing first review.

## Live qualification

An isolated ESP32/DHT11 project was compiled with the production PlatformIO service and reviewed through the production model router using `nvidia/nemotron-3-super-120b-a12b`.

- Firmware build: passed, 271,552 bytes.
- SHA-256: `e4f356a057969c715e12be0eb7906d98fcee00ce90e171b6f9ccd47f5431be1d`.
- Host references: all 18 real requirements accepted.
- Independent requirement review: all 18 satisfied.
- Independent completion review: all 18 satisfied.
- Provider attempts: two, both HTTP 200; valid review JSON, no response repair.

The evidence is in [result.json](../audit/2026-10-09/verification-qualification-140301/result.json), alongside the isolated project and original request. This live qualification covers compilation and both review gates on a prepared project. The scripted provider regressions cover generation through completion. Physical ESP32 behavior remains unqualified.

Restart the development ForgeX backend to load the updated runtime. Continuing the original saved task will migrate its requirement contract; its source and documentation will still need to pass the current evidence and review gates.
