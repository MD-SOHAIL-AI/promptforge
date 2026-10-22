# Phase 2.5.7 Feature-Flagged Safe Patch Apply

Patch apply is now implemented behind a strict development feature flag.

Required flags:

```text
FORGEX_ENABLE_PATCH_APPLY=1
FORGEX_ENABLE_ROLLBACK_RESTORE=1
```

Default behavior remains disabled. If the apply flag is missing, the API returns:

```text
Patch apply is disabled. Enable FORGEX_ENABLE_PATCH_APPLY=1 for development testing.
```

If rollback restore support is missing, the API returns:

```text
Patch apply requires rollback restore support. Enable FORGEX_ENABLE_ROLLBACK_RESTORE=1.
```

Safe apply flow:

```text
verify patch SHA-256
require approved, unexpired review
run fresh preflight
create fresh rollback snapshot
parse ForgeX unified diff
stage all outputs before writing
write only files listed by preflight
verify final hashes
persist apply metadata
show rollback snapshot ID
```

Supported patch operations are text create, text modify, and explicit file delete. Unsupported patch types include binary patches, renames, mode-only changes, submodule patches, absolute paths, `..` paths, Windows drive paths, ignored folders, symlink escapes, ambiguous hunks, and failed context matches.

If a write fails after any workspace mutation, ForgeX attempts an automatic rollback using the fresh rollback snapshot. The final apply status records `failed_rolled_back` or `failed_rollback_failed`.

Apply does not enable AGY routing, Codex execution, Claude execution, auto-build, or auto-flash.
