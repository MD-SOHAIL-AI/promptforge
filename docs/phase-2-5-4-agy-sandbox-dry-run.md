# Phase 2.5.4 AGY Sandbox Dry-Run

## Summary

ForgeX now has a feature-flagged AGY sandbox dry-run prototype. It can copy the active workspace to a managed sandbox, run AGY only inside that sandbox, diff sandbox changes, and create a persistent bridge review.

Normal generation routing is unchanged.

## Feature Flag

Set:

```text
FORGEX_ENABLE_AGY_BRIDGE=1
```

Default behavior is disabled. The Settings -> Models AGY card explains that the flag is required.

## Sandbox Copy Behavior

Sandbox copies are created under ForgeX app state:

```text
.promptforge/state/bridge-sandboxes/<run_id>
```

The copy excludes heavy/generated folders and `.forgex`. Symlinks are skipped so the copy does not follow links outside the active workspace.

## AGY Command Behavior

ForgeX resolves `agy` first, then `antigravity`.

Execution uses argv arrays with `shell=False`:

```text
agy -p <prompt>
```

ForgeX does not run plain `agy`, does not open the AGY TUI, does not trigger login automatically, and does not pass `--dangerously-skip-permissions`.

Stdout and stderr previews are capped.

## Review Integration

After AGY exits successfully, ForgeX diffs the sandbox against the sandbox baseline and creates a persistent review session with provider:

```text
antigravity_cli_bridge
```

Approval and rejection remain state-only. ForgeX does not apply sandbox changes to the active workspace.

## Cancel And Timeout

Runs are tracked by run ID. Cancel and timeout terminate only the spawned AGY process tree owned by that run.

## Audit Events

ForgeX records:

- `agy_sandbox_run_requested`
- `agy_sandbox_created`
- `agy_sandbox_started`
- `agy_sandbox_completed`
- `agy_sandbox_failed`
- `agy_review_created`
- `agy_run_cancelled`

Audit records use workspace hashes and do not store prompts, tokens, cookies, raw workspace paths, or full outputs.

## Remaining Limitations

AGY auth remains official-tool owned. If AGY is not authenticated, ForgeX reports the failed run and instructs the user to open AGY manually and complete Google sign-in.

No active workspace apply/revert exists yet.
