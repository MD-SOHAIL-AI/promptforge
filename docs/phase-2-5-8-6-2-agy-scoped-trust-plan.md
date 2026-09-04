# Phase 2.5.8.6.2 — scoped AGY workspace trust plan

## Constraint

AGY 1.0.14 requires interactive per-folder trust/write approval. Unique temporary sandbox directories therefore cannot be safely pre-authorized for headless execution. No safe scoped CLI override was found. ForgeX rejects global permission changes, automated approval input, and `--dangerously-skip-permissions`.

## Design

ForgeX owns one stable QA workspace per active throwaway workspace beneath managed application state. The stable folder is never the active workspace, repository root, home, Desktop, OneDrive root, or filesystem root. Its identity is derived from a one-way workspace hash; it has a ForgeX marker and is accepted only after realpath, direct-child containment, protected-root, and recursive symlink checks.

Preparation is explicit. The operator runs the prepare command, opens only the displayed local managed folder in AGY, and manually approves that folder. Preparation copies the active throwaway workspace but executes no AGY request. Verification checks installation, authentication attestation, structure, marker, containment, separation, and symlinks. Because AGY has no safe machine-readable folder-trust query, verification reports `TRUST_STATUS_OPERATOR_ATTESTED_REQUIRED`.

## Live execution

Trusted mode is disabled by default and requires QA mode plus all existing AGY generic flags and `FORGEX_ENABLE_AGY_TRUSTED_WORKSPACE=1`. Each real run additionally requires `--confirm-real-agy` and the non-persistent `--trusted-workspace-attested` argument.

Before launch, ForgeX obtains an exclusive ownership-token lock, revalidates containment and the marker, rejects symlinks, removes only direct children of the contained managed workspace, and copies the active workspace while excluding ignored directories. AGY runs with the stable workspace as cwd. The instruction remains runtime-only.

The existing bounded post-exit scan, diff, review, patch export, integrity verification, and preflight pipeline is retained. ForgeX internal marker/prompt names are excluded. The lock remains held through artifact collection and review creation. The active workspace is only the preflight target and is never mutated by this flow. Apply, build, and flash are not invoked.

Stale locks recover only after the age bound has elapsed and the recorded process is no longer alive. Malformed, recent, or live-owner locks fail closed. Lock release requires the original random ownership token.

## Validation

Automated tests use fake AGY processes. Real AGY is reachable only through the confirmed live command. Positive success requires one process, one created smoke artifact, one review change, an unchanged active workspace, and successful read-only patch pipeline checks. Zero changes remains `no_changes_produced` with no review authority.

Cancellation and timeout are deferred until the positive artifact path succeeds.
