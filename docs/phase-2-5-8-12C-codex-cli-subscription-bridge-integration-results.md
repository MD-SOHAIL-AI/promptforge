# Phase 2.5.8.12C — Codex CLI Subscription Bridge Integration Results

## Manual standalone proof

Manual command shape:

```text
codex --ask-for-approval never exec --sandbox workspace-write --cd C:\forgex-codex-smoke "<prompt>"
```

Manual classification: `CODEX_STANDALONE_WRITE_PASS`.

| Field | Result |
| --- | --- |
| Created | `hello.py` |
| Content | `print("Hello from ForgeX Codex smoke test")` |
| Approval | `never` |
| Sandbox | `workspace-write` |
| Workdir | `C:\forgex-codex-smoke` |
| Real ForgeX repository touched | no |

No account identity, session identifier, runtime instruction, or raw process output is recorded here.

## Integration result

Phase 2.5.8.12C is complete as success-definition outcome B. The single gated ForgeX retry used the corrected argv order and external managed sandbox but exited without a recognized permission, authentication, usage/quota, timeout, or invocation diagnostic. It created no files and is therefore classified `CODEX_NATIVE_UNKNOWN_SAFE_FAILURE`. No retry was attempted and no success was inferred.

## Implemented bridge boundary

The subscription retry builder now produces this exact shape:

```text
--ask-for-approval never exec --sandbox workspace-write --cd <external-managed-sandbox> <runtime-instruction>
```

The approval option is global, the instruction is the final positional argument, and process launch uses direct argv with `shell: false`. The retry omits `--ignore-user-config`, `--ephemeral`, and `--skip-git-repo-check`. Dangerous bypass flags are rejected.

Each run is a unique direct child of `C:\forgex-codex-sandboxes`. The guard requires the immutable ForgeX marker and rejects the repository, active workspace, home, Desktop, OneDrive root, filesystem root, nested/non-direct children, and link/reparse escapes. Hash snapshots, rather than Git, provide the exact change set, so Git initialization is not required.

Codex owns official CLI authentication and model execution. ForgeX owns sandbox creation, active-workspace integrity, diff validation, review creation, and the later approval boundary. A token bridge is forbidden because it would move credentials across that ownership boundary and create unnecessary secret-storage and private-endpoint risk.

## Native retry result

| Field | Result |
| --- | --- |
| Codex version | `codex-cli 0.142.5` |
| Manual standalone classification | `CODEX_STANDALONE_WRITE_PASS` |
| Working argv shape implemented | yes |
| External sandbox root used | yes |
| ForgeX native retry attempted | yes |
| Real Codex model execution count | 1 |
| Final classification | `CODEX_NATIVE_UNKNOWN_SAFE_FAILURE` |
| Created / modified / deleted | 0 / 0 / 0 |
| `CODEX_GENERIC_SMOKE.txt` created | no |
| Marker unchanged | yes |
| Active workspace unchanged | yes |
| Review created | no |
| Raw runtime instruction/process output persisted | no |
| Authentication files read by ForgeX | no |
| Dangerous flags used | no |
| Auto-apply / build / flash | no / no / no |
| Production routing enabled | no |

The sanitized delta from the historical wrapper is:

- Historical ForgeX run: nested repository/OneDrive sandbox, different argv order, three extra exec flags, `CODEX_NATIVE_PERMISSION_BLOCKED`, zero changes.
- 12C ForgeX run: external direct-child sandbox, manual-pass argv order, no extra exec flags, `CODEX_NATIVE_UNKNOWN_SAFE_FAILURE`, zero changes.
- Manual standalone run: external sandbox and manual-pass argv order, `CODEX_STANDALONE_WRITE_PASS`.

This evidence does not establish a new root cause. Raw diagnostics were intentionally not retained, and the one-execution limit prevents a comparison retry in this phase. The standalone pass shows subscription authentication was available at that time; the 12C result is not reclassified as auth, permission, or usage failure without matching evidence.

## Review and routing policy

The persistent-review helper validates the exact marker plus one exact smoke file and creates a `codex_cli_subscription` Bridge Review only after `CODEX_SUBSCRIPTION_BRIDGE_PASS`. Because this run did not pass, no review record was created.

The provider remains `experimental=true`, `qa_only=true`, `review_eligible=false`, `production_eligible=false`, and `product_routing_enabled=false`. Its paused reason is the final classification. AGY remains paused, OpenCode remains reference-only, and no API, Claude, AGY, or OpenCode provider was executed.

## Verification

| Command | Result |
| --- | --- |
| Fake Codex Node tests | 34 passed |
| `python -m pytest tests -k "codex or provider_registry or bridge_security"` | 12 passed, 1,751 deselected |
| Codex subscription review/backend tests | 6 passed |
| `python -m compileall backend` | passed |
| `python -m pytest` | 1,753 passed, 10 skipped |
| `npm.cmd run qa:safety-scan` | 199 passed |
| Frontend typecheck | passed |
| Frontend production build | passed; 4 static pages |
| Electron build | passed |
| Electron tests | 10 passed |

## Recommendation

Keep the Codex subscription bridge QA-only and non-routeable. Continue API planner provider validation. Any future Codex comparison must be a separate explicitly authorized phase using `--allow-second-codex-retry`, with a predeclared sanitized diagnostic strategy that still does not persist raw process output or authentication material.
