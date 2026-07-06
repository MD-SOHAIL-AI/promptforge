# Bridge Provider Contract

## Purpose

Bridge providers expose official local tools to ForgeX through a provider-like contract. They are not API providers and do not own authentication. They detect, invoke, stream, cancel, and report local tool runs.

## Suggested Interface

```python
class BridgeProvider:
    provider_id: str
    display_name: str

    def detect_installation(self) -> BridgeDetectionResult:
        ...

    def get_auth_status(self) -> BridgeAuthStatus:
        ...

    def list_models_or_modes(self) -> list[BridgeModel]:
        ...

    def run_prompt(self, request: BridgeRequest) -> BridgeResponse:
        ...

    def stream_prompt(self, request: BridgeRequest) -> Iterator[BridgeEvent]:
        ...

    def cancel(self, run_id: str) -> None:
        ...
```

## Detection Result

```json
{
  "installed": true,
  "executable_path": "C:/Users/example/AppData/Local/Programs/tool/tool.exe",
  "version": "1.2.3",
  "source": "path|configured_path|well_known_location",
  "message": "Tool detected"
}
```

## Auth Status

```json
{
  "authenticated": true,
  "status": "authenticated|unauthenticated|unknown|error",
  "message": "Signed in through official tool",
  "setup_url": null
}
```

Auth checks must not reveal or read private tokens. They should use official status commands if available.

## Bridge Model/Mode

```json
{
  "id": "default",
  "display_name": "Default subscription mode",
  "mode": "ask|edit|plan",
  "supports_streaming": true,
  "supports_workspace_edits": true,
  "requires_user_approval": true
}
```

## Request Shape

```json
{
  "run_id": "bridge-run-...",
  "task_type": "code_generation",
  "workspace_root": "...",
  "prompt": "...",
  "allowed_paths": ["..."],
  "timeout_seconds": 300,
  "mode": "ask|edit|plan",
  "requires_user_approval": true,
  "dry_run": true,
  "metadata": {
    "project_id": "...",
    "execution_id": "..."
  }
}
```

## Response Shape

```json
{
  "success": true,
  "status": "completed",
  "files_changed": [],
  "stdout_preview": "...",
  "stderr_preview": "...",
  "exit_code": 0,
  "duration_ms": 12000,
  "usage": null,
  "diagnostics": {
    "tool_version": "1.2.3",
    "mode": "edit"
  }
}
```

## Stream Events

```json
{
  "run_id": "bridge-run-...",
  "event_type": "started|stdout|stderr|file_changed|approval_required|completed|failed|cancelled|timeout",
  "message": "...",
  "timestamp": "...",
  "file_path": "src/main.cpp"
}
```

## Required Behavior

- Detection must be side-effect free.
- Auth status must not extract credentials.
- Prompt execution must run in the active workspace.
- Prompt execution must use command allowlists.
- Cancellation must stop the process tree.
- Output previews must be capped.
- Changed files must be detected by workspace diffing.
- File changes require review for edit mode.

## Review Foundation Contract

Future bridge execution must create or reuse a workspace snapshot before running any external tool. After the tool exits, ForgeX must create a `BridgeReviewSession` from detected changes and require an explicit approve/reject decision before continuing workflow state.

In Phase 2.5.3, approve/reject records review state only. It does not apply bridge output and does not revert local files.

In Phase 2.5.3.1, review sessions and snapshot metadata persist in local app state. Providers must treat persisted pending reviews as authoritative until they expire, are approved, or are rejected. Persisted records store workspace hashes instead of raw workspace paths.

Phase 2.5.4 introduces an AGY-specific sandbox dry-run route. It is not a generic bridge execution contract. Future providers must still implement their own execution allowlists and must pass through the shared snapshot, sandbox, diff, review, and audit sequence before any active-workspace apply feature is considered.

Phase 2.5.4.1 adds patch export for review sessions. Providers may expose reviewed changes as relative-path patches, but exporting or copying a patch must not apply changes. Any future apply phase requires a separate explicit contract, user approval, workspace containment, and tests.

Phase 2.5.4.2 adds patch integrity metadata. Provider review exports must record SHA-256, patch size, changed-file lists, workspace hash, review status at export, and `apply_enabled: false`. Integrity verification and folder opening are inspection-only operations.

Phase 2.5.4.3 adds patch history and cleanup. Provider patch exports must be listable from a metadata-only index that does not store patch content. Delete and cleanup operations may remove only managed exported patch files and metadata. They must not delete review sessions, audit logs, sandboxes, or active workspace files.

Phase 2.5.5 defines the future patch apply preflight contract but does not implement apply. Providers must not apply exported patches directly. A future provider-compatible apply flow must use ForgeX-owned preflight APIs for integrity verification, review approval checks, workspace identity validation, path containment, drift detection, dry-run simulation, user confirmation, rollback snapshot creation, result verification, and audit logging. Provider patch records remain `apply_enabled: false` until a later safe-apply phase explicitly changes that gate.

Future apply and rollback APIs must use trusted ForgeX patch IDs, review IDs, provider IDs, and relative paths. They must not accept arbitrary patch paths, backup paths, raw workspace roots, tokens, cookies, or credential material from provider implementations.

## Phase 2.5.1 Detection Result

The implemented detection-only result shape is:

```json
{
  "provider_id": "codex_bridge",
  "display_name": "OpenAI Codex",
  "type": "tool_bridge",
  "installed": true,
  "version": "codex 1.2.3",
  "auth_status": "unknown",
  "executable_path": "~/AppData/Local/Programs/Codex/codex.exe",
  "capabilities": {
    "detect": true,
    "run_prompt": false,
    "stream_prompt": false,
    "edit_files": false,
    "diff_review": false
  },
  "can_run": false,
  "reason": "Prompt execution is not enabled in this phase"
}
```

The active Google bridge provider is `antigravity_cli_bridge` with display name `Google Antigravity / AGY CLI`. Legacy `gemini_cli_bridge` is not an active bridge card.

## Model Router Compatibility

Bridge providers can be represented as provider records:

```json
{
  "provider_id": "codex_bridge",
  "provider_type": "tool_bridge",
  "display_name": "Codex Bridge",
  "enabled": true,
  "configured": true,
  "local": true,
  "auth_type": "external_tool"
}
```
