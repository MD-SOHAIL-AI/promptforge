# Phase 2.5.8.8 Provider Strategy Decision

## Codex status alignment

Codex OAuth remains QA-only and production-ineligible. Provider registry detection now consumes the shared aligned official-CLI status classification, while review eligibility still requires an exact sandbox-smoke pass. Status alone never enables product routing.

Phase 2.5.8.17B keeps provider detection on the selected shared resolved status runner. Multiple launcher detection and session parity classifications are diagnostic only and cannot enable product routing.

Phase 2.5.8.17R2 changes only QA smoke content validation and review eligibility. It does not make the Codex OAuth bridge production-routeable.

Phase 2.5.8.17R3 changes classifier ordering and the QA review gate only. A smoke pass and review still grant no production routing, apply, build, or flash authority.

## Phase 2.5.8.17 Codex OAuth update

Codex remains non-production and non-routeable. A QA-only OAuth bridge may run one confirmed external-sandbox smoke after official CLI status reports signed in. Passing makes it review-eligible only; it does not enable project editing, product routing, apply, build, or flash.

## Phase 2.5.8.14 AGY generator update

AGY remains paused as a direct editor. The experimental `agy_scratch_runner` may invoke the official CLI only as a scratch generator from an external neutral cwd. It imports one precomputed expected folder through the existing manual-artifact validator and remains non-production and non-routeable. Missing expected output falls back to user-selected `agy_scratch_import` without scanning or guessing.

## Phase 2.5.8.13 AGY manual artifact update

AGY direct workspace execution remains paused. ForgeX instead exposes `agy_scratch_import` as a non-production manual-artifact provider: the user selects one exact existing AGY scratch project, ForgeX validates and copies it into a managed sandbox, and a safe exact diff may become review eligible. This path has no provider execution or authentication role and is not planner-routeable.

## Decision

Local CLI providers are paused because AGY and Codex failed safe write-provider validation.

AGY responded in non-interactive mode but did not reliably create the exact managed-workspace or nonce-bound scratch artifact. Codex exposed a non-interactive execution mode but its guarded workspace-write attempt was blocked by the safe permission/workspace policy. Neither result earned review or production eligibility.

The strategy is therefore:

- AGY: paused; execution and routing denied; detection and isolated QA evidence may remain.
- Codex CLI: paused; execution and routing denied; detection and isolated QA evidence may remain.
- Claude CLI: disabled; execution and routing denied.
- OpenCode: architecture reference only; it is not a provider and cannot route.
- API-backed models: design candidates only. ForgeX must own all filesystem actions, permission checks, sandboxing, diff creation, and review authority.

Phase 2.5.8.11 advances API-backed planners into a disabled-by-default product runtime. The fake planner is routeable only under explicit development flags. API planner entries remain disabled pending a guarded live product-runtime smoke. Local CLI and reference-only entries cannot be resolved by the product registry.

Registration or detection never grants execution authority. Local CLI providers are not production eligible.

## Required runtime boundary

The selected architecture is `model plan -> ForgeX tool validation -> managed sandbox tools -> exact diff -> review candidate`. Models return structured plans or text. They do not receive direct write authority over the active workspace.

Phase 2.5.8.9 implements this boundary first with an in-process fake API provider. Real API adapters remain disabled until the runtime is proven.

## Phase 2.5.8.12C exception

Codex now has an isolated experimental QA-only subscription bridge path based on official CLI authentication. This does not reverse the production strategy: normal Codex product routing remains disabled, and the provider is never production eligible. ForgeX owns the external disposable sandbox, diff, review, and later approval boundary; Codex owns authentication and model execution. Token extraction, private endpoint use, and ForgeX-owned OAuth are forbidden.

An exact native pass can make the resulting Bridge Review eligible. Any permission, authentication, usage/quota, timeout, content, no-change, or extra-change result leaves review eligibility false and records that classification as the paused reason.

The 12C native retry returned `CODEX_NATIVE_UNKNOWN_SAFE_FAILURE` with zero changes. Therefore the current Codex subscription entry remains experimental, QA-only, non-routeable, non-production, and not review eligible.
## Phase 2.5.8.12 update

The strategy is now implemented in the product Agent Runtime for mocked API transports. Gemini, Groq, OpenRouter, OpenAI, and NVIDIA NIM are registered as API planner providers, not executor providers. AGY and Codex remain paused, Claude CLI remains disabled, and OpenCode remains reference-only.

The provider/model plans. ForgeX executes. Real API execution still requires explicit operator gating and is not production eligible by default.
