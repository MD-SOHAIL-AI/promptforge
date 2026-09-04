# Phase 2.5.8.13 — AGY Scratch Project Import Provider Plan

## Objective

Treat AGY as a manual artifact generator rather than a ForgeX workspace editor. AGY runs separately under user control and may create a project beneath `%USERPROFILE%\.gemini\antigravity-cli\scratch`. ForgeX accepts only one exact folder selected or pasted by the user, validates it, copies approved text project files into a managed import sandbox, and creates a Bridge Review.

```text
AGY generates separately
  -> user supplies one exact scratch project folder
  -> ForgeX validates that selected tree only
  -> ForgeX copies captured safe bytes to a marked managed sandbox
  -> ForgeX verifies the exact diff and active-workspace integrity
  -> ForgeX creates a persistent review
  -> no automatic apply, build, or flash
```

## Provider model

- Provider ID: `agy_scratch_import`
- Kind: `manual_artifact`
- Execution mode: `manual_import`
- Workspace mode: `managed_import_sandbox`
- Authentication mode: `none`
- Production eligible: false
- QA only: false
- Review eligible: only after `AGY_SCRATCH_IMPORT_PASS`

The dedicated API and UI require `FORGEX_ENABLE_AGY_SCRATCH_IMPORT=1`, which defaults to disabled. The provider is not a planner and cannot route through normal agent execution.

## Validation boundary

The source must be an explicit absolute folder strictly below the AGY scratch root. ForgeX rejects the scratch root itself, brain and credential areas, sensitive roots, missing or non-directory sources, lexical traversal, containment failure, and link/reparse entries. ForgeX never lists the scratch root to discover candidates and never selects by modification time.

The selected project is capped at 200 files, 5 MiB total, 512 KiB per file, and eight path components. Only allowlisted source/configuration/documentation text types are accepted. Secret-name patterns, generated dependency/build directories, binary data, absolute targets, and parent traversal fail the complete import.

Validation captures each accepted file's bytes before sandbox creation. Copying therefore uses the already validated byte set rather than reopening an automatically discovered source.

## Review boundary

The destination is `.promptforge/agy-import-sandboxes/<run_id>` and begins with `README_FORGEX_AGY_IMPORT_SANDBOX.txt`. ForgeX snapshots this marker-only baseline, writes validated files, verifies that all changes are safe creations, confirms the active workspace is unchanged, and only then creates a persistent review.

Review persistence includes the source folder name, relative file tree, total bytes, counts, warnings, classification, and automatic-action booleans. It excludes the full source path, provider output, credentials, and unrelated scratch entries.
