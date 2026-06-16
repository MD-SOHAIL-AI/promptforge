export type StageKey = "planning" | "generation" | "build" | "flash" | "monitor";
export type StageStatus = "pending" | "active" | "success" | "failed";

export interface WorkflowStage {
  key: StageKey;
  label: string;
  description: string;
  status: StageStatus;
  durationMs?: number;
}

export interface HealthResponse {
  status: "healthy" | "degraded" | string;
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
  created_at: string;
  updated_at: string;
  file_count: number;
  metadata: Record<string, unknown>;
  files: ProjectFileResponse[];
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
  | "BUILD_STARTED"
  | "BUILD_COMPLETED"
  | "BUILD_FAILED"
  | "FLASH_STARTED"
  | "FLASH_COMPLETED"
  | "FLASH_FAILED"
  | "MONITOR_STARTED"
  | "MONITOR_COMPLETED"
  | "MONITOR_FAILED"
  | "WORKFLOW_COMPLETED"
  | "WORKFLOW_FAILED";

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

export interface ApiErrorBody {
  code?: string;
  message?: string;
  details?: Record<string, unknown>;
}
