export type StageKey = "planning" | "generation" | "build" | "flash" | "monitor";
export type StageStatus =
  | "pending"
  | "active"
  | "success"
  | "failed"
  | "cancelled"
  | "blocked"
  | "skipped"
  | "waiting_for_device";

export interface WorkflowStage {
  key: StageKey;
  label: string;
  description: string;
  status: StageStatus;
  durationMs?: number;
}

export interface HealthResponse {
  status: "healthy" | "degraded" | string;
  service?: string;
  version: string;
  timestamp: string;
  services: {
    workflow: boolean;
    planner: boolean;
    coordinator: boolean;
  };
}

export interface ProjectResponse {
  project_id: string;
  project_name: string;
  target_board: string;
  framework: string;
  project_path: string;
  external?: boolean;
  project_type?: string;
  created_at: string;
  updated_at: string;
  file_count: number;
  metadata: Record<string, unknown>;
  files: ProjectFileResponse[];
}

export interface PlatformIOEnvironment {
  name: string;
  platform: string;
  board: string;
  framework: string;
  monitor_speed?: number | null;
  upload_speed?: number | null;
  lib_deps: string[];
}

export interface ProjectImportResponse {
  project_id: string;
  name: string;
  path: string;
  external: boolean;
  project_type: string;
  board: string;
  framework: string;
  platform: string;
  environment?: string | null;
  monitor_speed?: number | null;
  upload_speed?: number | null;
  has_platformio_ini: boolean;
  files_loaded: boolean;
  platformio: {
    environments: PlatformIOEnvironment[];
  };
  metadata: Record<string, unknown>;
}

export interface ProjectFileResponse {
  path: string;
  content: string;
  file_type: string;
}

export interface ProjectListResponse {
  projects: ProjectResponse[];
  count: number;
}

export interface WorkspaceEntry {
  path: string;
  kind: "file" | "folder";
  file_type?: string | null;
}

export interface ProjectFilesResponse {
  project_id: string;
  project_name: string;
  entries: WorkspaceEntry[];
}

export interface FileContentResponse {
  project_id: string;
  path: string;
  content: string;
  file_type: string;
}

export interface BuildHistoryItem {
  execution_id: string;
  timestamp: string;
  board: string;
  status: string;
  duration_ms?: number | null;
}

export interface BuildHistoryResponse {
  builds: BuildHistoryItem[];
  count: number;
}

export interface BuildResultResponse {
  success: boolean;
  status: string;
  timestamp: number;
  duration_ms: number;
  message: string;
  metadata: Record<string, unknown>;
  firmware_path?: string | null;
  build_size_bytes: number;
  platform: string;
  board: string;
  toolchain_version: string;
  warnings_count: number;
  process_exit_code?: number | null;
  process_timed_out?: boolean | null;
  failure?: ExecutionFailure | null;
}

export interface BuildResponse {
  project_id: string;
  result: BuildResultResponse;
}

export interface FlashResultResponse {
  success: boolean;
  status: string;
  timestamp: number;
  duration_ms: number;
  message: string;
  metadata: Record<string, unknown>;
  port: string;
  board: string;
  flash_duration_ms: number;
  verification_status: string;
  bytes_written: number;
  tool: string;
  tool_version: string;
  process_exit_code?: number | null;
  process_timed_out?: boolean | null;
  failure?: ExecutionFailure | null;
}

export interface FlashResponse {
  project_id: string;
  build: BuildResultResponse;
  flash: FlashResultResponse | null;
}

export interface MonitorStatusResponse {
  state: string;
  connected: boolean;
  port: string | null;
  baudrate: number;
  metrics: Record<string, unknown>;
}

export interface DetectedBoard {
  board_type: string;
  port: string;
  vid?: number | null;
  pid?: number | null;
  manufacturer?: string | null;
  description?: string | null;
  serial_number?: string | null;
}

export interface DetectedBoardsResponse {
  boards: DetectedBoard[];
  count: number;
}

export interface WorkspaceLog {
  execution_id: string;
  log_type: string;
  path: string;
  content: string;
}

export interface WorkspaceLogsResponse {
  logs: WorkspaceLog[];
  count: number;
}

export interface ModelProviderResponse {
  provider_id: string;
  display_name: string;
  enabled: boolean;
  configured: boolean;
  auth_type: "api_key" | "cli_session" | "none";
  local: boolean;
  default_model: string;
  base_url?: string;
  api_key_masked?: string;
  masked_api_key?: string | null;
  credential_configured?: boolean;
  provider_type?: "api_provider" | "agent_provider" | "template_provider";
  health_status?: string | null;
  last_checked_at?: string | null;
  last_error?: string | null;
  models_cached?: number;
}

export interface ModelProvidersResponse {
  providers: ModelProviderResponse[];
}

export type BridgeAuthStatus = "authenticated" | "unauthenticated" | "unknown" | "not_installed" | "error";
export type BridgeStatusConfidence = "high" | "medium" | "low";
export type BridgeSetupAction = "open_docs" | "run_official_login_manually" | "none";

export interface BridgeCapabilitiesResponse {
  detect: boolean;
  run_prompt: boolean;
  stream_prompt: boolean;
  edit_files: boolean;
  diff_review: boolean;
}

export interface BridgeDetectionResponse {
  provider_id: string;
  display_name: string;
  type: "tool_bridge";
  provider_type: "tool_bridge";
  provider_kind: "local_cli";
  auth_mode: string;
  execution_mode: string;
  workspace_mode: string;
  production_eligible: boolean;
  qa_only: boolean;
  detection_classification: string;
  auth_classification: string;
  executable_found: boolean;
  version_detected: boolean;
  login_command: string | null;
  smoke_ready: boolean;
  smoke_classification: string;
  installed: boolean;
  version: string | null;
  executable_path: string | null;
  auth_status: BridgeAuthStatus;
  auth_message: string;
  status_confidence: BridgeStatusConfidence;
  setup_hint: string | null;
  setup_action: BridgeSetupAction;
  safe_status_checked: boolean;
  checked_commands: string[];
  capabilities: BridgeCapabilitiesResponse;
  warnings: string[];
  can_run: boolean;
  reason: string;
  last_checked_at: string;
}

export interface ModelBridgesResponse {
  bridges: BridgeDetectionResponse[];
}

export interface CodexOAuthStatusResponse {
  provider_id: "codex_cli_oauth_bridge";
  codex_installed: boolean;
  codex_version: string | null;
  auth_status: "signed_in" | "signed_out" | "unknown";
  auth_classification: string;
  bridge_classification: string;
  oauth_bridge_ready: boolean;
  production_routing_enabled: false;
  tokens_read: false;
  auth_files_read: false;
  qa_only: true;
  sandbox_smoke_enabled: boolean;
  status_runner: "resolved_executable_runner" | "windows_cmd_parity_runner" | "direct_codex_runner";
  env_profile: "codex_safe_user_env" | "inherited_minus_secrets_env";
  safe_env_profile: "codex_safe_user_env" | "inherited_minus_secrets_env";
  cwd_kind: "neutral_temp";
  raw_output_persisted: false;
}

export interface CodexStatusDiagnosticsResponse {
  classification: string;
  runners: Array<{
    runner_name: string;
    codex_found: boolean;
    codex_version_detected: boolean;
    auth_status: "signed_in" | "signed_out" | "unknown";
    exit_code_category: "success" | "nonzero" | "timeout" | "failed";
    env_profile: "aligned";
    cwd_kind: "neutral_temp";
    raw_output_persisted: false;
    auth_files_read: false;
    tokens_read: false;
  }>;
  production_routing_enabled: false;
}

export interface CodexOAuthSmokeResponse {
  classification: string;
  provider_id: "codex_cli_oauth_bridge";
  auth_status: "signed_in" | "signed_out" | "unknown";
  oauth_bridge_ready: boolean;
  review_created: boolean;
  review_id: string | null;
  created_file_count: number;
  modified_file_count: number;
  deleted_file_count: number;
  expected_file_created?: boolean;
  expected_content_valid?: boolean;
  content_validation_mode?: "normalized_single_line";
  normalization_applied?: "none" | "eof_newline_or_bom_or_crlf_only";
  normalized_content_matches?: boolean;
  first_difference_kind?: "bom" | "line_ending" | "trailing_newline" | "leading_whitespace" | "trailing_whitespace" | "content_text" | "length" | "unknown";
  prompt_variant?: "strict_single_line_v2";
  marker_unchanged?: boolean;
  active_workspace_unchanged: boolean;
  production_routing_enabled: false;
  tokens_read: false;
  auth_files_read: false;
}

export interface CodexLoginLaunchResponse {
  classification: string;
  provider_id: "codex_cli_oauth_bridge";
  launched: boolean;
  tokens_read: false;
  auth_files_read: false;
  production_routing_enabled: false;
}

export type BridgeReviewStatus = "pending" | "approved" | "rejected" | "expired";
export type BridgeChangeType = "created" | "modified" | "deleted";

export interface BridgeChangedFileResponse {
  path: string;
  change_type: BridgeChangeType;
  safe: boolean;
  previous_hash?: string | null;
  new_hash?: string | null;
  diff_preview?: string | null;
  preview_supported: boolean;
  warning?: string | null;
}

export interface BridgeReviewResponse {
  review_id: string;
  provider_id: string;
  workspace_root: string;
  workspace_root_hash: string;
  status: BridgeReviewStatus;
  created_at: string;
  expires_at: string;
  changed_files: BridgeChangedFileResponse[];
  summary: string;
  decision?: "approved" | "rejected" | null;
}

export interface BridgeSnapshotResponse {
  snapshot_id: string;
  workspace_root: string;
  workspace_root_hash: string;
  files: Record<string, { path: string; hash: string; size: number; mtime: string }>;
  created_at: string;
}

export interface BridgeReviewCountsResponse {
  pending: number;
  approved: number;
  rejected: number;
  expired: number;
}

export interface BridgeReviewsResponse {
  reviews: BridgeReviewResponse[];
  counts: BridgeReviewCountsResponse;
}

export interface BridgeApprovalDecisionResponse {
  review_id: string;
  decision: "approved" | "rejected";
  decided_at: string;
}

export interface BridgePatchExportResponse {
  review_id: string;
  patch_id: string;
  provider_id: string;
  patch_path: string;
  patch_size: number;
  patch_sha256: string;
  file_count: number;
  changed_file_count: number;
  created_files: string[];
  modified_files: string[];
  deleted_files: string[];
  workspace_root_hash: string;
  review_status_at_export: BridgeReviewStatus;
  apply_enabled: boolean;
  integrity_status: "valid" | "missing" | "modified" | "unknown";
  created_at: string;
  download_url: string;
}

export interface BridgePatchesResponse {
  patches: BridgePatchExportResponse[];
  count: number;
}

export type PatchPreflightConflictType =
  | "clean"
  | "workspace_drift"
  | "patch_modified"
  | "patch_missing"
  | "review_missing"
  | "review_not_approved"
  | "review_expired"
  | "provider_mismatch"
  | "path_unsafe"
  | "target_missing"
  | "target_changed"
  | "delete_conflict"
  | "binary_unsupported"
  | "large_file_unsupported"
  | "ignored_path"
  | "parse_error"
  | "unknown";

export interface PatchPreflightConflict {
  path: string;
  type: PatchPreflightConflictType;
  severity: "error" | "warning" | "info";
  message: string;
}

export interface PatchPreflightResult {
  patch_id: string;
  review_id: string;
  provider_id: string;
  can_apply: boolean;
  apply_enabled: boolean;
  integrity_status: BridgePatchExportResponse["integrity_status"];
  review_status: BridgeReviewStatus | "unknown";
  workspace_status: "ok" | "missing" | "unknown" | string;
  conflicts: PatchPreflightConflict[];
  warnings: PatchPreflightConflict[];
  files_to_create: string[];
  files_to_modify: string[];
  files_to_delete: string[];
  checked_at: string;
}

export interface RollbackFileBackup {
  path: string;
  change_type: "create" | "modify" | "delete";
  existed_before: boolean;
  previous_hash?: string | null;
  backup_path?: string | null;
  size: number;
  mtime?: string | null;
}

export interface RollbackSnapshot {
  rollback_id: string;
  patch_id: string;
  review_id: string;
  provider_id: string;
  workspace_root_hash: string;
  created_at: string;
  status: "created" | "deleted" | string;
  files: RollbackFileBackup[];
  total_bytes: number;
  apply_id?: string | null;
  restore_enabled: boolean;
}

export interface RollbackSnapshotCreateResponse {
  rollback_id: string;
  patch_id: string;
  review_id: string;
  status: string;
  files_backed_up: number;
  total_bytes: number;
  restore_enabled: boolean;
  snapshot: RollbackSnapshot;
}

export interface RollbackSnapshotsResponse {
  snapshots: RollbackSnapshot[];
  count: number;
}

export type RollbackRestoreConflictType =
  | "snapshot_missing"
  | "metadata_missing"
  | "backup_missing"
  | "backup_modified"
  | "workspace_missing"
  | "workspace_hash_mismatch"
  | "path_unsafe"
  | "ignored_path"
  | "symlink_escape"
  | "current_file_changed"
  | "current_file_missing"
  | "created_file_changed"
  | "delete_target_changed"
  | "restore_unsupported"
  | "unknown";

export interface RollbackRestoreConflict {
  path: string;
  type: RollbackRestoreConflictType;
  severity: "error" | "warning" | "info";
  message: string;
}

export interface RollbackRestorePreflightResult {
  rollback_id: string;
  patch_id: string;
  review_id: string;
  provider_id: string;
  can_restore: boolean;
  restore_enabled: false;
  workspace_status: string;
  snapshot_status: string;
  conflicts: RollbackRestoreConflict[];
  warnings: RollbackRestoreConflict[];
  files_to_restore: string[];
  files_to_remove: string[];
  checked_at: string;
}

export interface BridgeSafetyStatusResponse {
  rollback_snapshots_enabled: boolean;
  restore_preflight_enabled: boolean;
  restore_enabled: boolean;
  restore_feature_flag: boolean;
  apply_enabled: boolean;
  patch_apply_enabled: boolean;
  patch_apply_feature_flag: boolean;
  rollback_restore_enabled: boolean;
  rollback_restore_feature_flag: boolean;
  apply_requires_restore: boolean;
  agy_bridge_enabled: boolean;
  qa_mode_enabled: boolean;
  generic_bridge_contracts_available: boolean;
  generic_bridge_coordinator_available: boolean;
  generic_bridge_routing_enabled: boolean;
  agy_generic_provider_enabled: boolean;
  agy_generic_adapter_registered: boolean;
  agy_generic_adapter_enabled: boolean;
  agy_compatibility_router_available: boolean;
  agy_effective_execution_mode: "legacy" | "generic" | "blocked";
  bridge_routing_enabled: boolean;
  codex_execution_enabled: boolean;
  claude_execution_enabled: boolean;
  opencode_execution_enabled: boolean;
  generic_bridge_sandbox_required: boolean;
  generic_bridge_event_transport_internal_only: boolean;
  public_generic_run_api_enabled: boolean;
  raw_instructions_persisted: boolean;
  auto_apply_enabled: boolean;
  auto_build_after_apply: boolean;
  auto_flash_after_apply: boolean;
}

export interface PatchApplyFileResult {
  path: string;
  operation: "create" | "modify" | "delete";
  status: "success" | "failed" | "skipped";
  before_hash?: string | null;
  after_hash?: string | null;
  message: string;
}

export interface PatchApplyResult {
  apply_id: string;
  patch_id: string;
  review_id: string;
  provider_id: string;
  rollback_id?: string | null;
  workspace_root_hash: string;
  status: "blocked" | "staged" | "applying" | "applied" | "failed" | "failed_rolled_back" | "failed_rollback_failed" | string;
  started_at: string;
  completed_at: string;
  files_created: number;
  files_modified: number;
  files_deleted: number;
  files_failed: number;
  rollback_available: boolean;
  apply_enabled: boolean;
  restore_enabled: boolean;
  results: PatchApplyFileResult[];
}

export interface PatchAppliesResponse {
  applies: PatchApplyResult[];
  count: number;
}

export interface RollbackRestoreFileResult {
  path: string;
  operation: "restore_file" | "remove_created_file" | "skip";
  status: "success" | "failed" | "skipped";
  expected_hash?: string | null;
  actual_hash?: string | null;
  message: string;
}

export interface RollbackRestoreResult {
  restore_id: string;
  rollback_id: string;
  patch_id: string;
  review_id: string;
  provider_id: string;
  status: "restored" | "failed" | "blocked" | string;
  started_at: string;
  completed_at: string;
  files_restored: number;
  files_removed: number;
  files_failed: number;
  restore_enabled: boolean;
  apply_enabled: boolean;
  results: RollbackRestoreFileResult[];
}

export interface RollbackRestoresResponse {
  restores: RollbackRestoreResult[];
  count: number;
}

export type BridgeSandboxRunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "review_ready"
  | "failed_timeout";

export interface BridgeSandboxRunResponse {
  run_id: string;
  provider_id: string;
  workspace_root_hash: string;
  sandbox_root: string;
  status: BridgeSandboxRunStatus;
  started_at: string;
  completed_at?: string | null;
  exit_code?: number | null;
  stdout_preview?: string | null;
  stderr_preview?: string | null;
  changed_file_count: number;
  review_id?: string | null;
  error_message?: string | null;
}

export interface BridgeSandboxStatusResponse {
  enabled: boolean;
  feature_flag: string;
  provider_id: string;
  execution_mode?: "legacy" | "generic" | "blocked";
}

export type GenericRunStatus =
  | "queued"
  | "validating"
  | "preparing_sandbox"
  | "running"
  | "collecting_artifacts"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "blocked"
  | "failed"
  | "timed_out"
  | "interrupted";

export interface GenericRunStartResponse {
  run_id: string;
  provider_id: "agy";
  status: GenericRunStatus;
  created_at: string;
  idempotent_reuse: boolean;
  event_stream_available: boolean;
}

export interface GenericRunDetailResponse {
  run_id: string;
  provider_id: "agy";
  status: GenericRunStatus;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  failure_code?: string | null;
  safe_failure_message?: string | null;
  progress: number;
  artifact_count: number;
  review_id?: string | null;
  changed_file_count: number;
  cancellation_requested: boolean;
  cancellation_requested_at?: string | null;
  cancellable: boolean;
}

export interface GenericRunEvent {
  event_id: string;
  run_id: string;
  sequence: number;
  timestamp: string;
  event_type: string;
  status?: GenericRunStatus | null;
  safe_message?: string | null;
  progress?: number | null;
  failure_code?: string | null;
  artifact_id?: string | null;
}

export interface GenericProviderResponse {
  provider_id: "agy";
  display_name: string;
  installed: boolean;
  available: boolean;
  authentication_status: BridgeAuthStatus;
  capabilities: {
    edit_files: boolean;
    streaming_events: boolean;
    cancellation: boolean;
    timeout: boolean;
    artifacts: boolean;
    sandbox_required: true;
  };
  execution_enabled: boolean;
  disabled_reason?: string | null;
}

export interface GenericProviderListResponse {
  providers: GenericProviderResponse[];
}

export interface GenericAgentQaFixtureResponse {
  qa_mode: true;
  fixture_state: string;
  providers: GenericProviderResponse[];
  run?: GenericRunDetailResponse | null;
  events: GenericRunEvent[];
  connection_state: "idle" | "connecting" | "connected" | "reconnecting" | "polling" | "resync_required";
  submitting: boolean;
  error?: string | null;
}

export interface GenericRunCancelResponse {
  run_id: string;
  disposition: "accepted" | "already_requested" | "already_terminal" | "not_found" | "rejected";
  status?: GenericRunStatus | null;
  cancellation_requested: boolean;
}

export interface ModelRouteResponse {
  task_type: string;
  provider_id: string;
  model_id: string;
  fallback_enabled: boolean;
  fallback_provider_id?: string | null;
  local_only: boolean;
}

export interface ModelRoutesResponse {
  routes: ModelRouteResponse[];
}

export interface ModelInfoResponse {
  provider_id: string;
  model_id: string;
  display_name: string;
  context_window?: number | null;
  free?: boolean | null;
  local: boolean;
}

export interface ProviderModelsResponse {
  models: ModelInfoResponse[];
  count?: number;
  source?: string;
  error?: string | null;
}

export interface ProviderHealthResponse {
  health: {
    provider_id: string;
    ok: boolean;
    status: string;
    latency_ms?: number | null;
    error_code?: string | null;
    message?: string | null;
    checked_at?: string | null;
  };
}

export interface ModelUsageRecord {
  provider_id: string;
  model_id: string;
  task_type: string;
  latency_ms: number;
  success: boolean;
  error_code?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
  estimated_cost?: number | null;
  created_at: string;
}

export interface ModelUsageResponse {
  usage: ModelUsageRecord[];
  count: number;
}

export interface GenerationFileProgress {
  path: string;
  status: "pending" | "generating" | "validating" | "written" | "failed" | "skipped" | string;
  provider_id?: string | null;
  model_id?: string | null;
  repair_used?: boolean;
  fallback_used?: boolean;
  attempt_count?: number;
  bytes?: number;
  bytes_written?: number;
  previous_hash?: string | null;
  new_hash?: string | null;
  errors?: string[];
  warnings?: string[];
}

export interface GenerationProgress {
  mode: "one_shot" | "chunked" | string;
  status: "idle" | "running" | "success" | "incomplete" | "failed" | string;
  provider_id?: string | null;
  model_id?: string | null;
  content_verified?: boolean;
  no_op?: boolean;
  current_file?: string | null;
  files_total: number;
  files_written: number;
  repairs: number;
  fallbacks: number;
  warnings: string[];
  failed_files: string[];
  pending_files: string[];
  file_statuses: GenerationFileProgress[];
}

export interface GenerationDiagnosticsRun {
  run_id?: string;
  execution_id: string;
  task_id?: string;
  project_id?: string | null;
  workspace_root?: string;
  generation_mode?: string;
  strategy?: string;
  provider_id?: string;
  model_id?: string;
  started_at?: string;
  completed_at?: string;
  status: string;
  required_files?: string[];
  generated_files?: string[];
  failed_files?: string[];
  pending_files?: string[];
  file_statuses?: GenerationFileProgress[];
  repair_count?: number;
  fallback_count?: number;
  warning_count?: number;
  build_started?: boolean;
  build_success?: boolean;
}

export interface GenerationDiagnosticsRunsResponse {
  runs: GenerationDiagnosticsRun[];
  count: number;
}

export type ForgeXSettingValue = string | number | boolean | string[];

export interface ForgeXSettingsResponse {
  settings: Record<string, ForgeXSettingValue>;
}

export interface ForgeXSettingSchemaItem {
  type: "select" | "boolean" | "number" | "string" | "string_list";
  label: string;
  category: string;
  default: ForgeXSettingValue;
  options?: string[];
  min?: number;
  max?: number;
}

export interface ForgeXSettingsSchemaResponse {
  schema: Record<string, ForgeXSettingSchemaItem>;
}

export interface ForgeXSettingsExportResponse {
  settings: Record<string, ForgeXSettingValue>;
  secrets_included: boolean;
}

export interface ExecutionFailure {
  step: string;
  message: string;
  category: string;
  recoverable: boolean;
  fatal: boolean;
  tool_name?: string | null;
  exception_type?: string | null;
}

export interface ExecutionStep {
  step: string;
  success: boolean;
  tool_name?: string | null;
  execution_time_ms: number;
  result?: Record<string, unknown> | unknown[] | null;
  failure?: ExecutionFailure | null;
}

export interface ExecuteResponse {
  status: string;
  task_id: string;
  execution_time_ms: number;
  steps: ExecutionStep[];
  failures: ExecutionFailure[];
}

export type ExecutionEventName =
  | "TASK_CREATED"
  | "PLAN_GENERATED"
  | "PLAN_FAILED"
  | "CODE_GENERATION_STARTED"
  | "CODE_GENERATION_COMPLETED"
  | "CODE_GENERATION_FAILED"
  | "GENERATION_STRATEGY_SELECTED"
  | "GENERATION_STARTED"
  | "GENERATION_FAILED"
  | "REQUIREMENTS_EXTRACTION_STARTED"
  | "REQUIREMENTS_EXTRACTED"
  | "MANIFEST_GENERATION_STARTED"
  | "MANIFEST_CREATED"
  | "FILE_GENERATION_STARTED"
  | "FILE_GENERATION_REPAIR_STARTED"
  | "FILE_GENERATION_FALLBACK_STARTED"
  | "FILE_GENERATION_VALIDATED"
  | "FILE_WRITTEN"
  | "FILE_FAILED"
  | "GENERATION_INCOMPLETE"
  | "GENERATION_COMPLETED"
  | "BUILD_BLOCKED"
  | "BUILD_STARTED"
  | "BUILD_COMPLETED"
  | "BUILD_FAILED"
  | "BUILD_REPAIR_STARTED"
  | "BUILD_REPAIR_COMPLETED"
  | "BUILD_REPAIR_FAILED"
  | "FLASH_STARTED"
  | "FLASH_COMPLETED"
  | "FLASH_FAILED"
  | "MONITOR_STARTED"
  | "MONITOR_COMPLETED"
  | "MONITOR_FAILED"
  | "WORKFLOW_COMPLETED"
  | "WORKFLOW_FAILED"
  | "WORKFLOW_CANCELLED";

export interface ExecutionEvent {
  sequence: number;
  event: ExecutionEventName;
  timestamp: string;
  task_id: string;
  execution_id: string;
  workflow_correlation_id: string;
  payload: Record<string, unknown>;
}

export interface WorkspaceFile {
  projectId?: string;
  path: string;
  content: string;
  language: string;
  fileType?: string;
}

export interface EditorTab extends WorkspaceFile {
  dirty: boolean;
}

export interface ConsoleEntry {
  id: string;
  timestamp: string;
  channel: "system" | "workflow" | "build" | "serial" | "error";
  message: string;
}

export interface CancelExecutionResponse {
  status: string;
  task_id: string;
  execution_id: string;
}

export interface TerminalSessionResponse {
  session_id: string;
  shell: string;
  cwd: string;
  closed: boolean;
}

export interface TerminalOutputEvent {
  sequence: number;
  stream: "stdout" | "stderr" | "system";
  data: string;
  timestamp: number;
}

export interface TerminalOutputResponse extends TerminalSessionResponse {
  events: TerminalOutputEvent[];
}

export type CodingWorkflowStatus =
  | "awaiting_apply"
  | "applying"
  | "awaiting_build"
  | "building"
  | "awaiting_flash"
  | "flashing"
  | "awaiting_monitor"
  | "monitoring"
  | "repairing"
  | "cancelling"
  | "completed"
  | "failed"
  | "cancelled"
  | "rejected"
  | string;

export type CodingWorkflowNextAction =
  | "await_user_approval"
  | "run_build"
  | "confirm_flash"
  | "open_monitor"
  | null;

export type CodingWorkflowProviderMode = "fake" | "api";

export type CodingWorkflowApiStatusReason =
  | "UNIFIED_CODING_WORKFLOW_DISABLED"
  | "REAL_API_CODING_AGENT_DISABLED"
  | "MODEL_ROUTER_UNAVAILABLE"
  | "MODEL_PROVIDER_NOT_CONFIGURED"
  | "MODEL_PROVIDER_UNHEALTHY"
  | "MODEL_NOT_AVAILABLE"
  | "REAL_API_CODING_AGENT_READY"
  | string;

export interface CodingWorkflowApiStatus {
  unified_workflow_enabled: boolean;
  real_api_coding_agent_enabled: boolean;
  coding_agent_repair_loop_enabled?: boolean;
  ready: boolean;
  provider_id: string | null;
  model: string | null;
  model_router_available: boolean;
  reason: CodingWorkflowApiStatusReason | null;
  safe_message: string;
}

export interface CodingWorkflowEvent {
  schema_version?: string;
  event_id: string;
  run_id: string;
  sequence: number;
  event_type: string;
  stage: string;
  status: string;
  safe_message: string;
  created_at?: string;
  timestamp?: string;
  metadata?: Record<string, unknown>;
}

export interface CodingWorkflowRun {
  schema_version?: string;
  run_id: string;
  task_id?: string | null;
  project_id?: string | null;
  provider_id: string;
  provider_type: string;
  status: CodingWorkflowStatus;
  generation_status?: string | null;
  review_id?: string | null;
  next_action: CodingWorkflowNextAction;
  files_changed: string[];
  created_at?: string;
  updated_at?: string;
  in_progress_stage?: string | null;
  locked?: boolean;
  lock_stale?: boolean;
  lock_operation?: string | null;
  started_at?: string | null;
  stale_candidate?: boolean;
  safe_summary?: string | null;
  failure_code?: string | null;
  safe_message?: string | null;
  metadata?: Record<string, unknown>;
}

export interface CodingWorkflowGenerateRequest {
  prompt: string;
  workspace_path?: string | null;
  context_mode?: "selected_files" | "file_tree_only" | string;
}

export interface CodingWorkflowApiGenerateRequest {
  prompt: string;
  workspace_path?: string | null;
  context_mode?: "selected_files" | "project_summary" | string;
  selected_files?: string[] | null;
  provider_id?: string | null;
  model?: string | null;
  live_api_confirmed?: boolean;
}

export interface CodingWorkflowRepairBuildRequest {
  workspace_path?: string | null;
  selected_files?: string[] | null;
  provider_id?: string | null;
  model?: string | null;
}

export interface CodingWorkflowContextPreviewRequest {
  workspace_path?: string | null;
  prompt?: string | null;
  context_mode: "selected_files" | "project_summary" | string;
  selected_files?: string[] | null;
  max_files?: number;
  max_file_bytes?: number;
  max_total_bytes?: number;
}

export interface CodingWorkflowContextIncludedFile {
  path: string;
  size_bytes: number;
  truncated: boolean;
  kind: string;
}

export interface CodingWorkflowContextExcludedFile {
  path: string;
  reason: string;
}

export interface CodingWorkflowContextPreview {
  context_mode: string;
  workspace_label: string;
  included_files: CodingWorkflowContextIncludedFile[];
  excluded_files: CodingWorkflowContextExcludedFile[];
  tree_summary: string[];
  total_bytes: number;
  truncated: boolean;
  limits: {
    max_files: number;
    max_file_bytes: number;
    max_total_bytes: number;
    max_tree_entries?: number;
  };
}

export interface CodingWorkflowContextFilesRequest {
  workspace_path?: string | null;
  max_files?: number;
  max_file_bytes?: number;
}

export interface CodingWorkflowContextFileItem {
  path: string;
  kind: string;
  size_bytes: number;
  selectable: boolean;
  reason: string | null;
}

export interface CodingWorkflowContextFilesResponse {
  workspace_label: string;
  files: CodingWorkflowContextFileItem[];
  truncated: boolean;
  limits: {
    max_files: number;
    max_file_bytes: number;
  };
}

export interface CodingWorkflowActionResult {
  status: CodingWorkflowStatus;
  run_id: string;
  repair_of_run_id?: string | null;
  review_id?: string | null;
  generation_status?: string | null;
  provider_id?: string;
  provider_type?: string;
  summary?: string;
  files_changed?: string[];
  next_action?: CodingWorkflowNextAction;
  events?: CodingWorkflowEvent[];
  build_status?: string | null;
  flash_status?: string | null;
  monitor_status?: string | null;
  artifact_reference?: string | null;
  environment?: string | null;
  board?: string | null;
  port?: string | null;
  baud_rate?: number | null;
  duration_ms?: number | null;
  output_byte_count?: number;
  output_preview?: string;
  truncated?: boolean;
  failure_code?: string | null;
  safe_message?: string;
}

export interface CodingWorkflowStaleRun {
  run_id: string;
  status: string;
  stage: string;
  started_at: string;
  age_seconds: number;
  suggested_action: string;
}

export interface CodingWorkflowStaleLock {
  run_id: string;
  operation: string;
  created_at: string;
  expires_at: string;
  pid?: number | null;
  suggested_action: string;
}

export interface CodingWorkflowStaleRunsResponse {
  runs: CodingWorkflowStaleRun[];
  count: number;
  stale_locks?: CodingWorkflowStaleLock[];
  stale_lock_count?: number;
  threshold_seconds: number;
}

export interface CodingWorkflowRunsResponse {
  runs: CodingWorkflowRun[];
  count: number;
}

export interface CodingWorkflowRunResponse {
  run: CodingWorkflowRun;
}

export interface CodingWorkflowEventsResponse {
  events: CodingWorkflowEvent[];
  count: number;
}

export interface ApiErrorBody {
  code?: string;
  message?: string;
  details?: Record<string, unknown>;
}

export type ProductAgentStatus = "queued" | "validating" | "preparing_sandbox" | "running" | "collecting_artifacts" | "cancelling" | "completed" | "failed" | "cancelled" | "blocked" | "timed_out" | "interrupted";

export interface ProductAgentRun {
  run_id: string;
  project_id: string;
  provider_id: string;
  status: ProductAgentStatus;
  classification?: string | null;
  review_id?: string | null;
  created_file_count: number;
  modified_file_count: number;
  deleted_file_count: number;
  tool_execution_count: number;
  active_workspace_unchanged: boolean;
  cancellable: boolean;
  created_at: string;
  updated_at: string;
  execution_mode?: "tool_planner" | "sandbox_agent";
  assistant_message?: string | null;
  provider_diagnostics?: {
    outbound_request_count: number;
    request_reached_provider: boolean;
    http_status?: number | null;
    provider_request_id?: string | null;
  };
  pending_approvals?: ProductAgentApproval[];
  summary?: ForgeXRunSummary;
}

export interface ForgeXRunSummary {
  run_id: string;
  requested_provider: string;
  actual_provider?: string | null;
  provider_type?: "api_provider" | "agent_provider" | "template_provider" | null;
  selected_model?: string | null;
  auth_status: string;
  generation_source?: string | null;
  workspace_mode: "modify_existing_project" | "generate_into_open_folder";
  project_type: string;
  changed_files: string[];
  unchanged_files: string[];
  created_files: string[];
  modified_files: string[];
  deleted_files: string[];
  no_op_status: boolean;
  content_verification_status: string;
  generation_status: string;
  build_status: string;
  flash_status: string;
  monitor_status: string;
  fallback_used: boolean;
  fallback_reason?: string | null;
  errors: Array<{ code: string; message: string }>;
  warnings: string[];
  next_suggested_action?: string | null;
}

export interface ProductAgentApproval {
  approval_id: string;
  run_id: string;
  kind: "command" | "network" | string;
  safe_message: string;
}

export interface ProductAgentEvent {
  run_id: string;
  sequence: number;
  event_type: string;
  status?: ProductAgentStatus;
  classification?: string;
  turn?: number;
  tool?: string;
  tool_count?: number;
  tool_execution_count?: number;
  changed_file_count?: number;
  created_at?: string;
  timestamp?: string;
  safe_message?: string;
  progress?: number;
}

export interface ProductAgentProvider {
  provider_id: string;
  display_name?: string;
  kind: string;
  provider_kind?: string;
  state: string;
  routeable: boolean;
  enabled_by_default?: boolean;
  provider_flag?: string | null;
  provider_flag_enabled?: boolean;
  api_key_env?: string | null;
  model_env?: string | null;
  default_model?: string | null;
  base_url_configured?: boolean;
  supports_toolplan?: boolean;
  supports_streaming?: boolean;
  key_present?: boolean;
  model_configured?: boolean;
  model_id?: string | null;
  last_classification?: string | null;
  review_eligible?: boolean;
  production_eligible?: boolean;
  product_routing_enabled?: boolean;
  qa_only?: boolean;
  execution_mode?: string | null;
  workspace_mode?: string | null;
  auth_mode?: string | null;
  paused_reason?: string | null;
  detected?: boolean;
  authenticated?: boolean;
  auth_type?: "api_key" | "cli_session" | "none";
  generation_source?: string;
}

export interface AGYScratchImportResult {
  classification: string;
  source_inside_scratch_root: boolean;
  source_name: string;
  file_count: number;
  total_bytes: number;
  blocked_files_count: number;
  managed_sandbox_created: boolean;
  created_file_count: number;
  modified_file_count: number;
  deleted_file_count: number;
  review_created: boolean;
  review_id?: string | null;
  active_workspace_unchanged: boolean;
  warnings: string[];
  auto_apply: false;
  auto_build: false;
  auto_flash: false;
}

export interface AGYAssistedRunResult {
  classification: string;
  agy_found: boolean;
  agy_version?: string | null;
  instruction_type: "agy_esp32_platformio_project";
  run_id: string;
  expected_folder_name: string;
  expected_source_name: string;
  expected_folder_found: boolean;
  file_count: number;
  total_bytes: number;
  project_type: string;
  review_created: boolean;
  review_id?: string | null;
  active_workspace_unchanged: boolean;
  manual_import_fallback_available: boolean;
  auto_apply: false;
  auto_build: false;
  auto_flash: false;
}
