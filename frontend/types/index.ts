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

export interface SerialMonitorEvent {
  sequence: number;
  timestamp: number;
  line: string;
  source: "DEVICE" | "RUNTIME" | "OVERFLOW" | "RECONNECT" | string;
  port: string;
  metadata: Record<string, unknown>;
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
  auth_type: "api_key" | "none";
  local: boolean;
  default_model: string;
  base_url?: string;
  api_key_masked?: string;
  masked_api_key?: string | null;
  credential_configured?: boolean;
  provider_type?: "api_provider";
  health_status?: string | null;
  last_checked_at?: string | null;
  last_error?: string | null;
  models_cached?: number;
}

export interface ModelProvidersResponse {
  providers: ModelProviderResponse[];
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
  supported_parameters?: string[];
  agent_compatible?: boolean | null;
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
  | "PROJECT_VALIDATION_STARTED"
  | "PROJECT_VALIDATION_FAILED"
  | "PROJECT_VALIDATION_COMPLETED"
  | "PROJECT_REPAIR_STARTED"
  | "PROJECT_REPAIR_COMPLETED"
  | "PROJECT_REPAIR_FAILED"
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
  cols: number;
  rows: number;
  profile_id: string;
  title: string;
  process_capability: "conpty" | "pty" | "pipe";
  lifecycle_status: "running" | "stopping" | "exited" | "closed";
  exit_code: number | null;
}

export interface TerminalProfileResponse {
  profile_id: string;
  name: string;
  shell: string;
  available: boolean;
  default: boolean;
  process_capability: "conpty" | "pipe";
}

export interface TerminalProfilesResponse {
  profiles: TerminalProfileResponse[];
  default_profile_id: string;
}

export interface TerminalOutputEvent {
  sequence: number;
  stream: "stdout" | "stderr" | "system";
  data: string;
  timestamp: number;
  dropped?: number;
}

export interface TerminalOutputResponse extends TerminalSessionResponse {
  events: TerminalOutputEvent[];
}

export interface ApiErrorBody {
  code?: string;
  message?: string;
  details?: Record<string, unknown>;
}

export type ProductAgentStatus = "queued" | "running" | "cancelling" | "awaiting_flash_confirmation" | "completed" | "failed" | "cancelled" | "blocked" | "timed_out";
export type ProductAgentStageStatus = "pending" | "running" | "waiting" | "completed" | "failed" | "cancelled";

export interface ProductValidationIssue {
  validator: string;
  rule_id: string;
  severity: string;
  category: string;
  message: string;
  recommendation?: string | null;
  affected_files?: string[];
  repairable: boolean;
  intent_conflict: boolean;
}

export interface ProductValidationReport {
  valid: boolean;
  issues: ProductValidationIssue[];
  suggested_alternatives: string[];
  repairable: boolean;
  requires_user_decision: boolean;
  message: string;
}

export interface ProductAgentRun {
  run_id: string;
  project_id: string;
  provider_id: string;
  model_id?: string | null;
  status: ProductAgentStatus;
  classification?: string | null;
  change_set_id?: string | null;
  autonomy?: "staged_changes" | "build_then_confirm_flash" | string;
  stage_statuses?: Record<string, ProductAgentStageStatus | string>;
  stage_messages?: Record<string, string>;
  active_workflow_task_id?: string | null;
  build_result?: Record<string, unknown> | null;
  flash_result?: Record<string, unknown> | null;
  monitor_result?: Record<string, unknown> | null;
  flash_confirmation_required?: boolean;
  flash_port?: string | null;
  flash_board_type?: string | null;
  flash_environment?: string | null;
  start_monitor_after_flash?: boolean;
  approval_id?: string | null;
  approval_expires_at?: string | null;
  created_file_count: number;
  modified_file_count: number;
  deleted_file_count: number;
  tool_execution_count: number;
  active_workspace_unchanged: boolean;
  cancellable: boolean;
  created_at: string;
  updated_at: string;
  execution_mode?: "tool_planner";
  assistant_message?: string | null;
  validation_report?: ProductValidationReport | null;
  repair_attempt_count?: number;
  agent_plan?: Array<{ text: string; status: "pending" | "in_progress" | "completed" | string }>;
  pending_decision?: boolean;
  suggested_alternatives?: string[];
  provider_diagnostics?: {
    actual_provider_id?: string | null;
    model_id?: string | null;
    fallback_reason?: string | null;
    outbound_request_count: number;
    request_reached_provider: boolean;
    http_status?: number | null;
    provider_request_id?: string | null;
  };
  summary?: ForgeXRunSummary;
}

export interface ForgeXRunSummary {
  run_id: string;
  requested_provider: string;
  actual_provider?: string | null;
  provider_type?: "api_provider" | "template_provider" | null;
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

export interface ProductAgentEvent {
  run_id: string;
  session_id?: string;
  node_id?: string;
  message_id?: string;
  sequence: number;
  event_type: string;
  status?: ProductAgentStatus;
  classification?: string;
  stage?: string;
  message?: string;
  model_id?: string | null;
  turn?: number;
  tool?: string;
  tool_count?: number;
  tool_execution_count?: number;
  changed_file_count?: number;
  created_at?: string;
  timestamp?: string;
  safe_message?: string;
  progress?: number;
  activity?: string;
  activity_label?: string;
  activity_phase?: "active" | "completed" | string;
  delta?: string;
  index?: number;
}

export interface AgentMessageDeltaEvent extends ProductAgentEvent {
  event_type: "message.delta";
  delta: string;
  index: number;
}

export type AgentEvent = ProductAgentEvent | AgentMessageDeltaEvent;

export interface AgentGraphNode {
  node_id: string;
  role: string;
  goal: string;
  dependencies: string[];
  status: string;
  attempt: number;
  risk_level: string;
  reviewer_required: boolean;
  message?: string | null;
  artifact_ids: string[];
}

export interface AgentExecutionGraph {
  run_id: string;
  status: string;
  nodes: AgentGraphNode[];
}

export interface ProductAgentProvider {
  provider_id: string;
  display_name: string;
  kind: "fake" | "api_planner" | "template_provider" | string;
  provider_kind?: string;
  state: string;
  routeable: boolean;
  enabled_by_default?: boolean;
  supports_toolplan?: boolean;
  supports_streaming?: boolean;
  key_present?: boolean;
  model_configured?: boolean;
  model_id?: string | null;
  health_status?: string | null;
  local?: boolean;
  selected?: boolean;
}

export type ForgeAgentMessageRole = "user" | "assistant" | "tool" | "system";

export interface ForgeAgentMessage {
  message_id: string;
  role: ForgeAgentMessageRole;
  content: string;
  created_at: string;
  run_id?: string | null;
  metadata?: Record<string, string | number | boolean | null>;
  streaming?: boolean;
}

export interface ForgeAgentSession {
  session_id: string;
  project_id: string;
  title: string;
  status: string;
  active_run_id?: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
  messages?: ForgeAgentMessage[];
}

export interface ForgeAgentActivityEvent {
  sequence: number;
  event_type: "activity.started" | "activity.updated" | "activity.completed" | string;
  type?: string;
  activity: string;
  label: string;
  phase: "active" | "completed" | string;
  session_id: string;
  run_id?: string | null;
  message_id?: string | null;
  created_at: string;
  timestamp?: string;
}

export type ChangeSetStatus = "pending" | "applied" | "undone" | "discarded" | "conflicted" | "failed";
export type ChangeType = "created" | "modified" | "deleted";

export interface ChangedFileResponse {
  path: string;
  change_type: ChangeType;
  safe: boolean;
  previous_hash?: string | null;
  new_hash?: string | null;
  diff_preview?: string | null;
  preview_supported: boolean;
  warning?: string | null;
}

export interface ChangeSetResponse {
  change_set_id: string;
  provider_id: string;
  workspace_root: string;
  status: ChangeSetStatus;
  created_at: string;
  applied_at?: string | null;
  undone_at?: string | null;
  changed_files: ChangedFileResponse[];
  summary: string;
}

export interface ChangeSetsResponse {
  change_sets: ChangeSetResponse[];
  count: number;
}

export interface ModelSelectionResponse {
  provider_id: string;
  model_id: string;
  fallback_enabled: boolean;
  fallback_provider_id?: string | null;
  local_only: boolean;
}
