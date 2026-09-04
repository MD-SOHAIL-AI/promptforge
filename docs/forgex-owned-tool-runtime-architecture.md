# ForgeX-Owned Tool Runtime Architecture

## Phase 2.5.8.10 real API planner spike

The OpenAI Responses API adapter now implements the provider contract as a disabled planner-only spike. It returns a strict root-object ToolPlan and never receives filesystem or execution authority. ForgeX independently validates every call and retains exclusive ownership of sandbox writes, exact diff validation, review creation, and post-review patch sequencing.

The adapter requires two disabled feature flags and explicit QA confirmation. Authorization is read from the process environment only after those gates pass. Automated tests inject responses and never use the network. A no-key guarded run exits safely before request or sandbox tool execution.

## Ownership boundary

```text
ForgeX UI
  -> ForgeX backend runtime
  -> API-backed provider contract or fake provider
  -> structured ToolPlan (memory only)
  -> ForgeX tool policy and validation
  -> ForgeX filesystem tools
  -> managed disposable sandbox
  -> baseline/after snapshot and exact diff
  -> Bridge Review candidate
  -> existing patch export / verify / preflight
  -> user approval boundary
```

Models think. ForgeX acts.

The provider contract accepts a task, a relative sandbox manifest, the allowed tool names, and a sanitized policy summary. It returns a `ToolPlan`. It receives no shell, process, network, dependency-install, active-workspace-write, patch-apply, build, or flash tool.

## Tool contract

Provider-exposed tools in this phase are:

- `list_files`: list regular non-sensitive files below a validated sandbox directory.
- `read_file`: read bounded UTF-8 text below the sandbox; credential-like paths are denied.
- `write_file`: create a new bounded UTF-8 text file below the sandbox.
- `edit_file_simple`: replace exactly one text occurrence in an existing sandbox file.

`create_review` is a normalized runtime operation, not provider authority. ForgeX invokes it only after the after-snapshot passes the exact-diff gate.

## Permission and containment policy

Every path must be ASCII and normalized, relative, drive-free, traversal-free, non-hidden, free of reserved Windows device names, and use an allowed extension. Existing path components and the target are rejected if they are symbolic links or reparse points. The resolved target must remain under the runtime's immutable sandbox root. Parent directories must already exist.

Writes and edits are bounded text operations. Binary/NUL content and oversized content are denied. The fake smoke policy additionally requires the exact filename `FORGEX_TOOL_RUNTIME_SMOKE.txt` and exact expected text.

The runtime snapshots the active workspace before provider planning and compares it again after tool execution. It separately hashes a managed sandbox marker. A changed active workspace or marker aborts review creation.

## Review and patch flow

The runtime requires one safe created-file diff with exact content. No changes, extra changes, modified/deleted files, unsafe paths, marker changes, active-workspace changes, or invalid provider output fail closed.

A passing review records the fake provider identity, the ForgeX runtime identity, executed tool names, change counts, and `none` authority for apply, build, and flash. Existing Bridge Review, patch export, patch integrity verification, and patch preflight services remain authoritative and unchanged. The runtime has no apply path.

## Provider design

`ApiBackedProvider` describes identity, model identity, structured-tool, streaming and JSON-schema capabilities, token bounds, and `request_plan(...)`. `openai_api`, `anthropic_api`, `google_api`, and `local_model_api` are disabled, non-production placeholders. They perform no key lookup and no network request.

The in-process `fake_api_provider` performs no network or process execution. It returns the exact smoke `write_file` call and proves the orchestration boundary independently of any local CLI or external service.

## Sanitized events

Runtime events contain only event type, sequence, optional classification, and counts. They contain no task text, provider output, file content, secrets, or absolute paths.
