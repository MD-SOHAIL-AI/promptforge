# Bridge Security Model

## Codex aligned status boundary

Codex status, login launch, and the smoke precheck use a safe user-environment allowlist. `USERPROFILE`, `APPDATA`, and `LOCALAPPDATA` are retained only so the official CLI can resolve its own user session; ForgeX does not inspect that storage. Status commands use direct argv, `shell:false`, and a neutral system-temp child directory. Secret-like environment names and raw status output are excluded. Diagnostics and parity cannot execute prompts or enable production routing.

## Phase 2.5.8.17 Codex OAuth smoke boundary

The official Codex CLI remains the OAuth client. ForgeX uses only `codex login status` and never reads auth files or browser callback data. A real smoke requires confirmation and `signed_in`, runs once with direct argv and `shell:false` under `C:\forgex-codex-oauth-smoke\<run_id>`, and uses `workspace-write`. Unsafe roots and link/reparse escapes are rejected. Only an exact one-file pass creates a review; raw prompt/output, tokens, auto-apply, build, flash, and production routing remain excluded.

Phase 2.5.8.17B adds status-only session parity diagnostics. A fixed `cmd.exe` runner is permitted only for the constant official status command (and the separately confirmed official login launcher if selected); it is never permitted for prompt execution. Diagnostics retain only flags, categories, counts, launcher kind, and a one-way path hash.

Phase 2.5.8.17R2 keeps smoke content validation exact after removing only a UTF-8 BOM, normalizing CRLF, and removing EOF LF characters. Inspection uses only the exact sanitized last-run identifier under the managed root, rejects link/reparse escapes, executes no provider, and emits hashes and categorical diagnostics rather than content.

Phase 2.5.8.17R3 evaluates known process failures before artifact success, then permits success only through a complete sanitized pass predicate. Raw byte-count equality is not authoritative after strict normalized validation. A second backend predicate and the review gate reject any unsafe persistence, credential-read, routing, automation, diff, marker, or workspace flag.

## Phase 2.5.8.14 AGY assisted-generation boundary

The assisted runner requires a feature flag and an explicit action. QA additionally requires real-execution confirmation. AGY runs with direct argv and `shell:false` only from a unique marked child of `C:\forgex-agy-runs`; repository, active-workspace, home, Desktop, OneDrive, filesystem-root, and link/reparse locations are rejected.

ForgeX captures the fixed template instruction and process output in memory only. It reads no AGY authentication material and does not use stdout paths. Only the precomputed expected scratch folder is checked. Missing output creates no review and exposes manual exact-path import; no alternative or newest folder is searched.

All source validation and sandbox copying reuse the manual importer. Active and invocation workspace integrity checks surround execution, and exact validation precedes review creation. Assisted reviews require manual user approval; no automatic apply, build, or flash authority exists.

## Phase 2.5.8.13 AGY manual scratch project import boundary

The project importer is not a provider process runner. It accepts one explicit absolute folder and requires that folder to be a strict descendant of `%USERPROFILE%\.gemini\antigravity-cli\scratch`. It rejects the root itself, brain/sensitive locations, traversal, missing/non-directory inputs, source and child link/reparse entries, rejected directories, secrets, binaries, unsupported file types, and configured count/size/depth limits.

ForgeX enumerates only the selected project tree. It does not enumerate the scratch root for candidates, inspect unrelated folders, select by time, or consume AGY output. Accepted file bytes are captured during validation and written only to a marked managed import sandbox. An exact created-only diff and unchanged active workspace precede review creation.

The feature defaults off behind `FORGEX_ENABLE_AGY_SCRATCH_IMPORT`. Reviews persist only a safe source folder name, relative file tree, counts, total bytes, warnings, and safety booleans. No automatic apply, build, or flash authority is granted.

## Phase 2.5.8.12C Codex subscription bridge boundary

The experimental Codex path is a CLI-auth bridge, never a token bridge. The official Codex CLI owns authentication and model execution. ForgeX does not inspect authentication files or private endpoints; it owns the disposable sandbox, baselines, exact diff validation, persistent review, and later user approval boundary.

The gated command requires both `--confirm-real-codex` and `--subscription-bridge-retry`. It uses direct argv with `shell: false` in this exact order: global `--ask-for-approval never`, `exec`, `--sandbox workspace-write`, `--cd <sandbox>`, then the runtime-only instruction. The subscription retry rejects dangerous bypass flags and omits `--ignore-user-config`, `--ephemeral`, and `--skip-git-repo-check`.

Each run uses a unique direct child of `C:\forgex-codex-sandboxes`. ForgeX rejects the repository, active workspace, home, Desktop, OneDrive root, filesystem root, nested children, and link/reparse escapes. The active workspace and immutable marker are hash-checked before and after execution. Process output remains memory-only for classification; persisted status contains only expected-file metadata, hashes, counts, booleans, and the final classification.

A Bridge Review is created only for `CODEX_SUBSCRIPTION_BRIDGE_PASS`: exactly one created `CODEX_GENERIC_SMOKE.txt`, exact content, no modified/deleted/extra files, unchanged marker, and unchanged active workspace. The provider remains experimental, QA-only, non-production, and non-routeable. No apply, build, or flash action occurs.

## Phase 2.5.8.11 product agent boundary

Product agent requests execute only through a versioned structured ToolPlan and ForgeX-owned tools. The default product policy offers `write_file` in a managed sandbox and denies active-workspace writes, external reads/writes, shell, network, dependency installation, patch application, build, and flash. Every run is bounded by turns, calls per turn, total calls, runtime, changed-file counts, and bytes per file.

Run records and events contain identifiers, statuses, classifications, tool names, and counts only. Instructions and provider responses remain in process memory and are not written to run, event, review, or audit persistence. A denied tool is represented by a sanitized denial result and prevents review creation. Persistent review creation requires a non-empty safe sandbox diff, unchanged active workspace, unchanged marker, and no unauthorized change.

## Phase 2.5.8.7 Codex native boundary (superseded for the 12C retry)

Codex detection and help/version probes run only from marked ForgeX-managed probe directories. The native write command requires `--confirm-real-codex`, resolves the Windows npm launcher in memory to preserve `shell=False`, and never persists the executable path. Write execution uses a unique managed cwd, `workspace-write`, approval policy `never`, ephemeral sessions, ignored user config, and no additional writable directories.

The guard rejects the repository root, active workspace, filesystem root, home, Desktop, OneDrive root, containment failures, and link/reparse escapes. It accepts only the exact smoke filename and content and treats marker mutation, active-workspace mutation, or any extra file as failure. Prompts and stdout/stderr stay in memory and are reduced to bounded classifications and counts.

The real smoke ended `CODEX_NATIVE_PERMISSION_BLOCKED` with zero changes. Consequently no Codex adapter, review, patch, apply, build, or flash authority was created. Dangerous sandbox/approval and hook-trust bypasses remain prohibited. AGY remains paused; Claude and OpenCode remain disabled.

## Phase 2.5.8.6.5 AGY scratch import boundary

AGY scratch import is QA-only and requires explicit real-run confirmation plus per-run trusted-workspace attestation. It invokes only AGY through fixed direct argv from the existing managed trusted cwd. The runtime instruction and raw stdout/stderr are memory-only.

ForgeX resolves a fixed home-relative provider scratch root, requires it to pre-exist, and does not list or recursively scan it. The expected filename is generated before execution and includes a random run ID and nonce. ForgeX rejects pre-existing artifacts and reads only that exact path after regular-file, `.txt`, 16 KiB, symlink/reparse, realpath, and root-containment checks. Exact marker, run ID, and nonce content are mandatory.

A passing smoke import requires unchanged active and managed workspaces and an unchanged trusted marker. It may create only a metadata-only review record containing the filename, content hash, byte size, source/type, and classification. This record contains no workspace changes and grants no patch export, apply, build, or flash authority. The importer does not read credentials or add Codex, Claude, or OpenCode execution.

## Core Rule

ForgeX may invoke official local tools that the user has installed and authenticated. ForgeX must not capture, store, inspect, or proxy subscription credentials.

## Account and Credential Boundaries

ForgeX must not:

- store ChatGPT session tokens
- store Claude session tokens
- store Gemini session tokens
- scrape browser cookies
- read private auth files for token extraction
- reverse-engineer OAuth flows
- ask users to paste subscription session secrets
- forward API keys to bridge tools unless the user explicitly configures API-provider mode

## Command Allowlist

Bridge execution should use a strict command allowlist:

- known executable names
- exact binary path after detection
- approved subcommands only
- no arbitrary shell command construction
- no user-supplied command fragments

Prefer direct process spawning with argv arrays over shell execution.

## Phase 2.5.1 Detection Boundary

Current bridge support is detection-only. ForgeX may run only PATH discovery (`where` or `which`) and bounded version commands (`codex --version`, `claude --version`, `agy --version`, or `antigravity --version`) with `shell=False`.

ForgeX must not read auth token files, browser cookies, OAuth stores, private session files, or user prompts for bridge detection. Installed tools report auth as `unknown` unless a future phase adds an official documented non-mutating status command.

## Phase 2.5.2 Auth Status Boundary

ForgeX may also run bounded help commands and provider-declared non-mutating status commands when help output confirms the command exists. Raw status output is capped and is not shown in the UI by default.

If a status command is not clearly safe, ForgeX returns `auth_status: unknown`. ForgeX must not launch login, trigger OAuth, run an interactive tool mode, or inspect credential files to improve auth status.

AGY/Antigravity detection must never run `agy` or `antigravity` alone because those commands may open an interactive TUI.

## Workspace Sandboxing

Every bridge run must have:

- active workspace root
- resolved canonical root path
- allowed path list
- path containment checks
- blocked absolute output paths
- blocked `..` path escapes
- symlink policy for writes
- no writes outside the workspace

## Environment Filtering

Bridge process environments should be filtered.

Allowed:

- minimal system PATH required to start the official tool
- HOME/USERPROFILE if required by the official tool
- terminal compatibility variables when needed

Blocked by default:

- ForgeX API keys
- provider API keys
- unrelated secret environment variables
- CI tokens
- cloud credentials

## User Approval

Bridge runs should require user consent before command execution. File-mutating runs should require a second approval before applying detected changes when the tool supports dry-run or when ForgeX can stage diffs.

Required approval surfaces:

- command/tool name
- workspace root
- mode
- timeout
- expected file access
- billing/subscription note

## Diff Review

If a bridge edits files:

1. snapshot workspace files before run
2. run bridge inside workspace
3. detect changed files
4. generate diff
5. show diff in Forge panel
6. allow apply/reject

If a bridge cannot support reversible edits, ForgeX should warn before execution and still provide changed-file detection afterward.

## Phase 2.5.3 Review Foundation

ForgeX includes a detection-only review foundation before bridge execution is enabled. The review service snapshots workspace files, compares current files against the snapshot, generates capped unified diff previews, and records approve/reject decisions.

This foundation does not run bridge commands and does not apply or revert files. It rejects absolute paths, `../` escapes, and paths outside the active workspace root.

Audit logs store workspace hashes and changed-file counts, not raw credential paths, tokens, cookies, full diffs, or raw bridge output.

## Phase 2.5.3.1 Persistent Review State

Bridge review snapshots and sessions are persisted locally before any bridge execution is enabled. Snapshot records store metadata only: workspace hash, file hashes, file sizes, and mtimes. Review records store capped diff previews and approval status.

Raw workspace paths are not written to bridge review persistence records. Full snapshot file contents are not persisted. Pending reviews expire after 24 hours and cannot be approved after expiration.

Persistence remains local to ForgeX managed app state and does not add cloud sync, bridge routing, prompt execution, or bridge file editing.

## Phase 2.5.4 AGY Sandbox Dry-Run Boundary

AGY sandbox execution is available only when `FORGEX_ENABLE_AGY_BRIDGE=1`. The prototype may run `agy -p <prompt>` only inside a temporary sandbox copy under ForgeX managed app state.

ForgeX must not run plain `agy`, must not open AGY interactive mode, must not use `--dangerously-skip-permissions`, and must not run AGY in the active workspace. The active workspace is snapshotted and copied, but AGY can mutate only the sandbox copy.

AGY instructions are runtime-only and passed to the fixed, non-shell invocation without writing a prompt file into the sandbox. Stdout/stderr previews are capped internally and are not exposed by the generic API. Audit logs record lifecycle events with workspace hashes and do not store prompts, tokens, cookies, raw workspace paths, or provider output.

Approve/reject remains review-state only. No AGY changes are applied to the active workspace in this phase.

## Phase 2.5.4.1 Patch Export Boundary

ForgeX can export a persisted bridge review diff as a `.patch` file under local app state. Patch exports use relative paths and must not include raw workspace absolute paths.

Patch export, patch view, and patch copy do not apply changes to the active workspace. Unsupported binary or large file previews are represented by comments, not raw file content.

Audit logs record patch export/view/copy metadata, including review ID, provider ID, changed-file count, workspace hash, and patch size. Audit logs must not include full patch content, tokens, cookies, prompts, credential paths, or raw workspace paths.

## Phase 2.5.4.2 Patch Integrity Boundary

Patch exports now include a SHA-256 metadata sidecar. ForgeX verifies integrity by comparing the current patch file hash to the recorded export hash and reports `valid`, `modified`, `missing`, or `unknown`.

The open patch folder action resolves the patch from the managed patch directory and review ID only. It must not accept arbitrary paths and must not execute patch files.

Integrity metadata and folder opening still do not apply patches or modify the active workspace.

## Phase 2.5.4.3 Patch History Boundary

Patch history is a metadata-only index stored under the managed ForgeX patch directory. It records patch IDs, review IDs, provider IDs, patch size, SHA-256, integrity status, changed-file relative paths, workspace hash, review status at export, and `apply_enabled: false`.

The patch index must not store patch content or raw workspace paths. Patch delete and cleanup may remove only managed exported patch files and sidecar metadata. They must not delete review sessions, audit logs, sandboxes, or active workspace files.

Patch history actions remain inspection and cleanup operations only. Apply remains disabled.

## Phase 2.5.5 Patch Apply Preflight Boundary

Phase 2.5.5 is a documentation-only safety design for a future apply system. It does not add active-workspace apply, bridge routing, AGY routing, Codex execution, Claude execution, credential reads, or credential storage.

Future apply must be gated by patch integrity verification, approved and unexpired review state, workspace identity checks, path containment, active workspace drift detection, dry-run patch simulation, explicit user confirmation, pre-apply rollback snapshot creation, result verification, rollback metadata, and audit logging.

The current implementation must continue to treat exported patches as inspection artifacts with `apply_enabled: false`. Any future preflight route may inspect trusted ForgeX metadata and simulate apply, but real apply must remain blocked until a later feature-flagged implementation phase.

Future patch apply audit events must not store full patch content, tokens, cookies, raw secrets, credential paths, or full workspace paths.

## Phase 2.5.5.1 Read-Only Patch Preflight Boundary

ForgeX exposes `POST /models/bridges/patches/{patch_id}/preflight` as a read-only safety report. The route may read managed patch metadata, the exported patch file, review metadata, and current target file hashes under the active workspace root. It must not write active workspace files, create rollback/apply records, execute bridge prompts, or enable any apply path.

Preflight returns `can_apply` as a safety assessment only. `apply_enabled` remains `false` in every response.

Preflight audit events are limited to patch ID, review ID, provider ID, conflict count, warning count, `can_apply`, and workspace hash. Audit must not include full patch content, raw workspace paths, tokens, cookies, raw secrets, or credential material.

## Phase 2.5.5.2 Patch Preflight UI Boundary

ForgeX presents preflight results in Bridge Review and Patch History using a compact safety report. The UI may send only the active ForgeX workspace root selected/opened by the app. It must not prompt for arbitrary workspace paths, persist raw workspace paths for preflight, or log patch content.

Output panel messages for preflight are limited to short status summaries such as start, patch integrity status, and result counts. They must not include patch content, raw secrets, tokens, cookies, credential material, or full workspace paths.

Apply remains disabled in the UI even when preflight reports a safe candidate.

## Phase 2.5.5.3 Rollback Snapshot Boundary

ForgeX can create rollback snapshots for patches that pass preflight. Snapshot creation may copy current active-workspace target files into ForgeX-managed app state under `patch-rollback/<rollback_id>/files/`.

Snapshot creation must not modify active workspace files, apply patches, restore files, create apply records, execute bridge prompts, or enable AGY/Codex/Claude execution.

Rollback metadata must use relative paths and workspace hashes. Audit logs must not include file contents, raw workspace paths, tokens, cookies, raw secrets, or credential material.

Restore remains disabled. Apply remains disabled.

## Phase 2.5.5.4 Rollback Restore Preflight Boundary

ForgeX can run a read-only restore preflight for a rollback snapshot. The route may read rollback metadata, backup file hashes, and current active-workspace file hashes. It must not restore files, delete files, apply patches, write active workspace files, create apply records, execute bridge prompts, or enable AGY/Codex/Claude execution.

Restore preflight audit events must not include file contents, raw workspace paths, tokens, cookies, raw secrets, or credential material.

`restore_enabled` remains `false`. Apply remains disabled.

## Phase 2.5.5.5 Bridge Patch Safety QA Boundary

ForgeX has end-to-end QA for the bridge patch safety chain without invoking real AGY, Codex, Claude, bridge routing, PlatformIO builds, or network access.

The QA verifies:

- approved review to patch export to patch preflight
- rollback snapshot creation after clean preflight
- restore preflight after snapshot creation
- blocked states for unapproved reviews, modified patches, workspace drift, missing backups, unsafe paths, and ignored paths
- audit logs omit raw workspace paths, patch content, file contents, tokens, cookies, session data, and Google credential text
- cleanup operations do not cross storage boundaries
- active workspace hashes are unchanged after safety operations

Path safety treats Windows drive-qualified paths, drive-relative paths, backslash escapes, and ignored folder names case-insensitively.

Apply remains disabled. Restore remains disabled.

## Phase 2.5.6 Feature-Flagged Rollback Restore Boundary

ForgeX can restore files from rollback snapshots only when `FORGEX_ENABLE_ROLLBACK_RESTORE=1`.

Restore execution must:

- require exact confirmation `RESTORE`
- rerun restore preflight before writing
- reject failed preflight
- restore only paths listed in rollback metadata
- reject ignored folders, unsafe paths, and symlink escapes
- verify backup hashes before writing
- verify restored hashes after writing
- remove created-file rollback targets only as single files
- store restore result metadata under managed ForgeX state

Restore execution must not:

- apply patches
- create patch apply records
- run AGY, Codex, Claude, or bridge prompts
- read tokens, cookies, sessions, or credential files
- write outside the active workspace
- delete directories recursively
- log file contents, patch contents, raw workspace paths, tokens, cookies, or credential material

`apply_enabled` remains `false`.

## Logging and Telemetry

## Phase 2.5.8.1 Generic Contract Boundary

The provider-neutral contract layer is architecture only. It has no generic start endpoint and is not connected to AGY, Codex, Claude Code, or OpenCode. Registration and routing are separate; routing is hard-disabled.

Generic requests carry project and sandbox identities but no command, executable, arbitrary environment, or public filesystem path. Instructions are runtime-only, excluded from `repr`, persistence, audit metadata, and events; only SHA-256 and length may be stored. Provider execution requires a verified sandbox context whose internal root is disjoint from the active workspace.

Generic events are bounded and sanitized before persistence. Generic artifacts are contained references into existing managed review/patch ownership and do not contain patch or file bytes. Cancellation is idempotent, terminal history is immutable, and unexpected restart reconciliation produces `interrupted` rather than resuming work.

AGY adaptation is implemented in Phase 2.5.8.2 without routing. Streaming is deferred. Codex, Claude Code, and OpenCode execution remain deferred.

## Phase 2.5.8.2 AGY Adapter Boundary

ForgeX registers an `AGYBridgeProvider` adapter with generic provider ID `agy`, but adapter execution and generic routing remain disabled. Existing AGY endpoints continue to use the legacy runner directly.

The adapter does not resolve executables, construct argv, spawn or terminate processes, filter the environment, create/clean sandboxes, parse raw output, create reviews, or apply patches. Those responsibilities remain with the existing detector, AGY runner, sandbox service, review service, and patch safety pipeline.

The adapter passes the validated managed sandbox—not the active workspace—as the legacy runner source. Runtime instructions remain excluded from `repr`, events, errors, detection output, and persistence. Generic detection discards executable paths and raw detector output. Cancellation delegates only to the process owner for the mapped legacy run; the adapter contains no process-name or broad termination logic.

Review artifacts reuse existing review IDs and carry no apply authority. No generic run endpoint, Codex/Claude/OpenCode execution, auto-apply, auto-build, or auto-flash is added.

## Phase 2.5.8.3 Coordinator Boundary

The internal generic coordinator is composed under a default-deny routing policy. Registration and availability cannot authorize execution: global permission and an explicit provider permission are also required, and production grants neither.

Run records are durable before provider invocation. Instructions remain memory-only; events use fixed sanitized lifecycle messages. Revision compare-and-set prevents stale lifecycle writes. Sandbox containment is revalidated by the existing sandbox owner, and only the provider owner may cancel its process.

Internal event queues and replay are bounded. Overflow requires persisted replay. Artifact references must match run/sandbox ownership and managed storage containment; they contain no bytes and cannot authorize apply. Restart reconciliation marks incomplete work `interrupted` without resuming it.

No public generic run/cancel endpoint or event transport exists. AGY generic execution, generic routing, Codex, Claude Code, OpenCode, auto-apply, auto-build, and auto-flash remain disabled.

## Phase 2.5.8.4 Cutover Security Boundary

Generic AGY cutover requires the existing AGY flag, global generic-routing flag, AGY generic-provider flag, and explicit cutover flag. Cutover defaults false. Partial combinations fail closed. The compatibility decision is made once before sandbox creation and stored for generic runs; flag changes cannot redirect cancellation or restart lookup.

Generic mode creates one sandbox through the existing owner. The existing runner reuses it and remains the only command/process/termination owner. There is no dual execution or automatic legacy fallback. Compatibility audit metadata contains hashes/IDs and safe mode/status codes only.

The existing AGY endpoint remains the sole public execution surface. Codex, Claude Code, OpenCode, auto-apply, auto-build, and auto-flash remain disabled.

## Logging and Telemetry

Telemetry may store:

- provider ID
- bridge tool name
- version
- run ID
- task type
- workspace hash or project ID
- status
- duration
- exit code
- changed file paths
- stdout/stderr previews with caps

Telemetry must not store:

- session tokens
- cookies
- raw auth files
- API keys
- unbounded model output
- full private file contents unless user opts into diagnostics export

## Timeouts and Cancellation

Bridge runs must support:

- default timeout
- user-configurable max timeout
- process-tree cancellation
- cleanup of temp prompt files
- status update on cancellation

Cancelled bridge runs must be marked cancelled, not failed.

## Audit Log

ForgeX should maintain a local audit log of bridge actions:

- who/what started the run locally
- command identity
- workspace root
- mode
- started/completed timestamps
- cancellation/timeout
- changed files
- approval decisions

The audit log should remain local unless a future explicit export feature is added.
# Phase 2.5.7 Patch Apply Security Addendum

Patch apply does not add bridge routing and does not invoke AGY, Codex, Claude, or any bridge prompt outside the existing sandbox flow.

The apply path must not read tokens, cookies, browser sessions, Google credentials, or provider credential files. Audit logs record IDs, counts, provider ID, rollback ID, apply ID, and workspace root hash only. They must not store patch content, file content, or raw workspace paths.

Patch apply remains disabled unless both `FORGEX_ENABLE_PATCH_APPLY=1` and `FORGEX_ENABLE_ROLLBACK_RESTORE=1` are set.
# Phase 2.5.7.1 Observability Security

Apply history and detail UI surfaces metadata only. It does not expose raw workspace paths, patch text, file contents, credentials, cookies, tokens, or sessions.

The stabilization phase adds no bridge routing, no Codex execution, no Claude execution, no AGY execution outside sandbox mode, no auto-build, and no auto-flash.

# Phase 2.5.7.2 Desktop QA Security Note

The desktop QA polish phase did not add bridge routing, Codex execution, Claude execution, AGY execution outside sandbox dry-run mode, auto-build, or auto-flash. Apply History and Apply Detail remain metadata-only UI surfaces and must not display raw workspace paths, patch content, file content, tokens, cookies, sessions, or credential material.

The interactive desktop QA pass is documented as blocked for this session because the Electron window could not be controlled or inspected after launch.

# Phase 2.5.7.2.1 QA Harness Security

The QA harness sets missing development QA flags, starts or reuses local backend/frontend processes, launches Electron, prints local health URLs, and writes `.promptforge/state/qa-session.json`.

The session file stores local URLs, launch timestamp, Electron launch status, and feature flag booleans only. It must not store secrets, full environment variables, raw workspace paths, patch content, file content, tokens, cookies, sessions, or credential material.

The QA Diagnostics UI is read-only. Bridge routing, Codex execution, Claude execution, auto-build after apply, and auto-flash after apply remain disabled.

# Phase 2.5.7.3 Packaged QA Security

Packaged QA uses `.promptforge/qa/phase-2-5-7-3/` for managed state, settings, model-router data, Electron user data, package staging, and logs. Cleanup is containment checked and fixtures require `FORGEX_QA_MODE=1`.

Production apply and restore defaults remain disabled. Bridge routing, Codex execution, Claude execution, OpenCode execution, auto-build after apply, and auto-flash after apply are explicitly reported as disabled.

Packaged fixtures contain sanitized IDs, relative QA filenames, hashes, statuses, and normalized messages only. They do not create normal-state audit records or store raw workspace paths, patch content, file content, credentials, tokens, cookies, sessions, or environment dumps.
# Phase 2.5.8.5 generic API boundary

The generic API is a local desktop capability, not an internet API. ForgeX continues to bind backend and packaged frontend services to loopback. No wildcard CORS policy is introduced. Generic execution rejects non-loopback web origins while permitting same-origin proxy requests, loopback packaged origins, and the existing desktop file-origin behavior.

The start body has a 32 KiB request limit and a strict allowlist. `project_id` must be a stable identifier; path-like substitution is rejected. Instruction text is capped at 16,384 characters, is excluded from API logs and public errors, and is not serialized to run/event/audit records. Its SHA-256 digest is retained internally only to make idempotency conflicts deterministic.

One centralized policy requires five independently disabled-by-default flags. Listing providers cannot mutate flags. Query parameters cannot activate execution. Once a generic run is persisted, its `execution_mode` fixes cancellation ownership even if flags later change.

Only AGY is returned as supported. Detector responses omit executable paths, credential locations, identities, tokens, environment, and raw command output. Codex, Claude, and OpenCode have no selectable generic provider entry.

# Phase 2.5.8.5.1 packaged readiness security

Packaged QA explicitly sets the generic API, routing, AGY provider, and AGY cutover flags to `0`; inherited operator environment cannot enable execution. Apply and restore remain separately feature-flagged. No QA fixture can execute a provider, create a real run, write normal application state, apply, build, or flash.

Corrupt generic metadata cannot grant authority or take the whole backend offline. Health and Bridge Safety remain available while generic history access returns a sanitized fail-closed error. Composition does not detect or execute AGY.

Readiness evidence stores loopback URLs and ports, a process role and PID, sanitized package/resource identities, startup phase, safe failure reason, bounded sanitized backend previews, boolean results, and relative screenshot labels. It excludes full commands, environment dumps, prompts, patch/file content, raw filesystem roots, credentials, cookies, and tokens.

The live AGY helper exits before provider checks or execution when `--confirm-real-agy` is absent. Phase 2.5.8.5.1 remained blocked by operator authorization and executed zero providers.

# Phase 2.5.8.6 live validation security

Check-only uses the platform locator and local package/file version metadata. When the official Windows binary has no usable version metadata, it may run only the resolved executable with the fixed `--version` argument, `shell=False`, a five-second timeout, and strict bounded-version validation. It never submits an instruction or starts a provider run. It prints and persists classifications, never executable paths, account identity, credential locations, environment dumps, or raw provider output. Authentication requires an explicit operator attestation and is never inferred from credential files.

The live path accepts no command, executable, argument list, environment map, or workspace path from the caller. It uses the fixed AGY provider, fixed throwaway workspace, fixed instruction, fixed non-shell runner invocation, and bounded timeout. Real execution additionally requires all six gates plus `--confirm-real-agy`.

Workspace safety uses a relative file/hash baseline and exact realpath containment. Review validation accepts only the proposed `AGY_GENERIC_SMOKE.txt` change. Patch export, integrity, and preflight are read-only; apply remains separately gated and is never invoked. The legacy runner no longer writes raw instructions to a sandbox prompt file.

The Phase 2.5.8.6 readiness result reached `READY`. Exactly one provider process ran in one managed sandbox after explicit confirmation. The active workspace remained unchanged; no patch was applied, no build or flash ran, and no legacy fallback occurred. The run created an empty review, so patch export and preflight were not attempted.

Live SSE heartbeat waits catch `asyncio.TimeoutError` for Python 3.10 compatibility. Public replay remains limited to sanitized event records, and provider completion is independent of event-stream transport failure. The managed audit for the live attempt contained metadata only and passed checks for paths, prompt/output fields, patch/file content, and credential markers.

## Phase 2.5.8.6.1 positive-artifact security

The runner may inspect provider output only in memory to reduce it to a bounded classification. New executions persist neither stdout nor stderr. Runtime diagnostics contain only booleans, enums, counts, instruction length, and instruction SHA-256.

The managed sandbox remains the subprocess cwd and the instruction remains a fixed argv value with `shell=False`. Diff settling is bounded and occurs after process termination. A zero-change exit is a safe failure with no review, patch, or apply authority.

ForgeX does not solve AGY workspace trust by sending interactive approval input, modifying global settings, granting wildcard file permissions, or using `--dangerously-skip-permissions`. Until AGY provides an official scoped headless trust mechanism, the positive artifact path remains incomplete.

## Phase 2.5.8.6.2 scoped AGY trust boundary

AGY 1.0.14 requires per-folder interactive trust/write approval; unique temporary sandboxes cannot be safely authorized headlessly. ForgeX uses no global bypass. Instead, QA trusted mode owns one stable workspace under managed state per throwaway workspace. The root is marked, a direct contained child, recursively symlink-checked, and rejected if it equals or overlaps the active workspace or equals the repository, home, Desktop, OneDrive, or filesystem root.

Trusted mode requires QA mode, all generic AGY gates, and its own disabled-by-default flag. Preparation is an explicit non-executing operator action. Because trust status is not machine-readable, every real QA run requires a non-persistent operator attestation in addition to real-AGY confirmation.

Reset is allowed only while holding an exclusive ownership-token lock. Deletion targets must be direct children of the validated root; ignored directories are not copied. The lock remains held through diff and review. Internal marker/prompt names are excluded from review. AGY retains its fixed non-shell invocation, filtered environment, runtime-only instruction, sanitized output handling, bounded settle scan, zero-change failure, and no auto-apply/build/flash behavior.

The July 2026 attested validation reached `READY` and authorized exactly one AGY process. That process produced no filesystem changes and was safely classified as `no_changes_produced`. No review, patch, apply, build, flash, or legacy fallback authority resulted. A relative hash comparison found the active workspace unchanged. Codex, Claude, and OpenCode execution remained disabled.

This result proves the attestation gate, managed-workspace containment, and zero-change fail-closed path. It does not prove that AGY can create the expected artifact under the approved scoped trust, so positive review, patch integrity, and preflight validation remain incomplete.

## Phase 2.5.8.6.3 native AGY security boundary

Native AGY QA requires an exact confirmation flag and a separate per-run trusted-workspace attestation. The command resets and revalidates only the stable ForgeX-managed workspace, rejects the repository, active workspace, home, Desktop, OneDrive root, filesystem root, and symbolic-link escapes, and uses fixed direct `-p` argv with `shell: false`. Unsafe permission bypasses are rejected.

Baselines contain relative names and hashes only; the internal marker is tracked separately. Runtime instructions and raw provider output are not persisted or printed. Results contain bounded classifications and counts only. Native files do not enter review or patch processing, and no apply, build, flash, Codex, Claude, or OpenCode path is enabled. The first authorized run was safely classified `NATIVE_AUTH_BLOCKED`, with zero changed files and an unchanged active workspace.

## Phase 2.5.8.6.4 auth/session investigation boundary

Auth-only QA is non-writing and mutually exclusive with native write confirmation. It requires the same trusted-workspace attestation and containment guards, executes no instruction, reads no credential or account files, and does not automate login. When official help exposes no safe status command, it runs only the fixed bounded version probe and returns `NATIVE_AUTH_STATUS_UNAVAILABLE` after verifying zero workspace changes.

The Windows installation fallback scans only PATH entries for the fixed AGY or optional alias filename. It does not execute arbitrary input, inspect credentials, or expose the resolved path. Raw help and process output are not persisted. No apply, build, flash, Codex, Claude, or OpenCode authority is added.
## Phase 2.5.8.9 ForgeX-owned tool runtime security

AGY and Codex local CLI execution/routing are paused by provider evidence policy. Claude CLI is disabled and OpenCode is reference-only. The ForgeX tool runtime exposes no shell, network, dependency-install, direct apply, build, or flash tool.

Provider plans are memory-only. Runtime events and results contain classifications, booleans, counts, tool names, and internal IDs only; they exclude task text, raw provider output, file content, secrets, and absolute paths. Credential-like files cannot be read.

All filesystem calls are rooted in a managed sandbox and require validated relative paths, allowed extensions, bounded UTF-8 text, resolved containment, and no symbolic-link or reparse traversal. Review authority is created only after an exact expected diff, unchanged active-workspace snapshot, and unchanged sandbox marker. Review creation grants no apply authority.
## Phase 2.5.8.10 OpenAI API planner boundary

The OpenAI adapter is disabled and non-routeable in normal UI/API selection. Only the guarded QA command can reach it, after explicit confirmation and two independent feature flags. Missing authorization is classified before a request, sandbox tool execution, or review creation.

The adapter uses a fixed HTTPS endpoint, bounded timeout and response size, strict structured output, and no streaming. It neither reads credential files nor exposes authorization data in safe metadata. Request instructions and response text remain in memory and are reduced to a ToolPlan plus sanitized provider metadata.

The API is not a ForgeX runtime tool: models still receive no shell, network tool, install tool, active-workspace write, apply, build, or flash authority. All returned paths and content pass the existing ForgeX tool policy and exact-diff gate.
## Phase 2.5.8.10A operator authorization validation

Authorization validation is presence-only and process-environment-only. The safe result records `api_key_detected`, `api_key_printed`, and `api_key_persisted` booleans; it never serializes the authorization value. Missing authorization produces zero outbound requests, zero tool executions, and no review.

The adapter performs at most one non-streaming request with no retry. Request and response bodies remain memory-only. Provider metadata adds only outbound request count to the existing provider/model/run/tool/classification fields. Normal UI routing remains disabled.
## Phase 2.5.8.12 API planner safety

The API planner layer preserves the ForgeX-owned execution boundary:

- API providers are disabled by default.
- Real outbound calls require global API runtime flags, provider-specific flags, key presence, model configuration, and explicit confirmation.
- Detection does not call provider APIs.
- API keys are read only from process environment variables and are never printed or persisted.
- Raw prompts, raw provider responses, request bodies, response bodies, headers, account identity, and credential paths are not persisted.
- ToolPlan output is strict JSON and is revalidated by ForgeX before execution.
- Only managed-sandbox `write_file` is accepted for the smoke path.
- Active workspace writes, shell, network-as-tool, dependency install, patch apply, build, and flash remain denied.
