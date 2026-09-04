import type {
  ApiErrorBody,
  BuildHistoryResponse,
  BuildResponse,
  CancelExecutionResponse,
  ChangeSetResponse,
  DetectedBoardsResponse,
  ExecuteResponse,
  FileContentResponse,
  FlashResponse,
  ForgeAgentSession,
  ForgeAgentActivityEvent,
  ForgeXSettingsExportResponse,
  ForgeXSettingsResponse,
  ForgeXSettingValue,
  ForgeXSettingsSchemaResponse,
  HealthResponse,
  ModelProviderResponse,
  ModelProvidersResponse,
  ModelRoutesResponse,
  ModelSelectionResponse,
  MonitorStatusResponse,
  ProductAgentProvider,
  ProductAgentRun,
  ProjectFilesResponse,
  ProjectImportResponse,
  ProjectListResponse,
  ProviderHealthResponse,
  ProviderModelsResponse,
  TerminalOutputResponse,
  TerminalProfilesResponse,
  TerminalSessionResponse,
  WorkspaceLogsResponse,
} from "@/types";
import type { ForgeComposerContext } from "@/components/nexus/universal-composer";
import { toErrorMessage } from "@/lib/errors";

const API_BASE = "/api/promptforge";

export class PromptForgeApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
  ) {
    super(message);
    this.name = "PromptForgeApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (!response.ok) {
    let body: ApiErrorBody | unknown = {};
    try {
      body = await response.json();
    } catch {
      // Offline/proxy responses may not be JSON.
    }
    const record = body && typeof body === "object" ? (body as ApiErrorBody & { detail?: unknown; error?: unknown }) : {};
    throw new PromptForgeApiError(
      errorBodyMessage(record) ?? `ForgeX API request failed (${response.status})`,
      response.status,
      record.code,
    );
  }

  return (await response.json()) as T;
}

export const promptForgeApi = {
  health: () => request<HealthResponse>("/health"),

  settings: () => request<ForgeXSettingsResponse>("/settings"),
  settingsSchema: () => request<ForgeXSettingsSchemaResponse>("/settings/schema"),
  patchSettings: (settings: Record<string, ForgeXSettingValue>) =>
    request<ForgeXSettingsResponse>("/settings", {
      method: "PATCH",
      body: JSON.stringify({ settings }),
    }),
  resetSettings: () =>
    request<ForgeXSettingsResponse>("/settings/reset", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  exportSettings: () =>
    request<ForgeXSettingsExportResponse>("/settings/export", {
      method: "POST",
      body: JSON.stringify({}),
    }),

  projects: () => request<ProjectListResponse>("/projects"),
  importProject: (body: { path: string }) =>
    request<ProjectImportResponse>("/projects/import", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  projectFiles: (projectId: string) =>
    request<ProjectFilesResponse>(`/projects/${encodeURIComponent(projectId)}/files`),
  fileContent: (projectId: string, path: string) =>
    request<FileContentResponse>(`/files/content?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(path)}`),
  createFile: (body: {
    project_id: string;
    path: string;
    kind?: "file" | "folder";
    content?: string;
    file_type?: string;
  }) => request<ProjectFilesResponse>("/files", { method: "POST", body: JSON.stringify(body) }),
  updateFile: (body: {
    project_id: string;
    path: string;
    content?: string;
    new_path?: string;
    kind?: "file" | "folder";
    file_type?: string;
  }) => request<FileContentResponse | ProjectFilesResponse>("/files", { method: "PUT", body: JSON.stringify(body) }),
  deleteFile: (projectId: string, path: string) =>
    request<{ project_id: string; path: string; deleted: boolean; deleted_count: number }>(
      `/files?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(path)}`,
      { method: "DELETE" },
    ),
  deleteProject: (projectId: string) =>
    request<{ project_id: string; deleted: boolean }>(`/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" }),

  buildHistory: () => request<BuildHistoryResponse>("/build-history"),
  detectedBoards: () => request<DetectedBoardsResponse>("/devices/boards"),
  buildProject: (body: { project_id: string; environment?: string | null }) =>
    request<BuildResponse>("/build", { method: "POST", body: JSON.stringify(body) }),
  flashProject: (body: {
    project_id: string;
    board_type: string;
    port: string;
    environment?: string | null;
    baudrate?: number;
    verify?: boolean;
    timeout_s?: number;
  }) => request<FlashResponse>("/flash", { method: "POST", body: JSON.stringify(body) }),
  startMonitor: (body: { project_id?: string | null; port?: string | null; baudrate?: number; timeout_s?: number }) =>
    request<MonitorStatusResponse>("/monitor/start", { method: "POST", body: JSON.stringify(body) }),
  stopMonitor: () => request<MonitorStatusResponse>("/monitor/stop", { method: "POST", body: JSON.stringify({}) }),
  monitorStatus: () => request<MonitorStatusResponse>("/monitor/status"),
  logs: (executionId?: string) =>
    request<WorkspaceLogsResponse>(executionId ? `/logs?execution_id=${encodeURIComponent(executionId)}` : "/logs"),

  terminalProfiles: () => request<TerminalProfilesResponse>("/terminal/profiles"),
  startTerminal: (body: {
    project_id: string;
    profile_id?: string;
    title?: string;
    cols?: number;
    rows?: number;
  }) =>
    request<TerminalSessionResponse>("/terminal/sessions", { method: "POST", body: JSON.stringify(body) }),
  terminalOutput: (sessionId: string, after = 0) =>
    request<TerminalOutputResponse>(`/terminal/sessions/${encodeURIComponent(sessionId)}/output?after=${encodeURIComponent(String(after))}`),
  writeTerminal: (sessionId: string, data: string) =>
    request<TerminalSessionResponse>(`/terminal/sessions/${encodeURIComponent(sessionId)}/input`, { method: "POST", body: JSON.stringify({ data }) }),
  clearTerminal: (sessionId: string) =>
    request<TerminalSessionResponse>(`/terminal/sessions/${encodeURIComponent(sessionId)}/clear`, { method: "POST", body: JSON.stringify({}) }),
  resizeTerminal: (sessionId: string, cols: number, rows: number) =>
    request<TerminalSessionResponse>(`/terminal/sessions/${encodeURIComponent(sessionId)}/resize`, { method: "POST", body: JSON.stringify({ cols, rows }) }),
  renameTerminal: (sessionId: string, title: string) =>
    request<TerminalSessionResponse>(`/terminal/sessions/${encodeURIComponent(sessionId)}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  stopTerminal: (sessionId: string) =>
    request<{ session_id: string; closed: boolean }>(`/terminal/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),

  modelProviders: () => request<ModelProvidersResponse>("/models/providers"),
  modelRoutes: () => request<ModelRoutesResponse>("/models/routes"),
  modelSelection: () => request<{ selection: ModelSelectionResponse }>("/models/selection"),
  saveModelSelection: (body: {
    provider_id: string;
    model_id: string;
    fallback_enabled?: boolean;
    fallback_provider_id?: string | null;
  }) => request<{ selection: ModelSelectionResponse }>("/models/selection", { method: "PUT", body: JSON.stringify(body) }),
  configureModelProvider: (
    providerId: string,
    body: { api_key?: string | null; base_url?: string; default_model?: string; enabled?: boolean },
  ) => request<{ provider: ModelProviderResponse }>(`/models/providers/${encodeURIComponent(providerId)}/configure`, { method: "POST", body: JSON.stringify(body) }),
  testModelProvider: (providerId: string) =>
    request<ProviderHealthResponse>(`/models/providers/${encodeURIComponent(providerId)}/health`, { method: "POST" }),
  providerModels: (providerId: string) =>
    request<ProviderModelsResponse>(`/models/providers/${encodeURIComponent(providerId)}/models`),

  agentRuntimeProviders: () => request<{ enabled: boolean; providers: ProductAgentProvider[] }>("/agent-runtime/providers"),
  startAgentRuntimeRun: (body: {
    project_id: string;
    instruction: string;
    provider_id: string;
    autonomy?: "staged_changes" | "build_only" | "build_then_confirm_flash";
    board_port?: string | null;
    board_type?: string | null;
    environment?: string | null;
    start_monitor_after_flash?: boolean;
  }) =>
    request<{ run: ProductAgentRun }>("/agent-runtime/runs", { method: "POST", body: JSON.stringify(body) }),
  agentRuntimeRun: (runId: string) =>
    request<{ run: ProductAgentRun }>(`/agent-runtime/runs/${encodeURIComponent(runId)}`),
  cancelAgentRuntimeRun: (runId: string) =>
    request<{ run: ProductAgentRun }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST", body: JSON.stringify({}) }),
  applyAgentRuntimeChanges: (runId: string) =>
    request<{ run: ProductAgentRun }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/apply-changes`, { method: "POST", body: JSON.stringify({}) }),
  confirmAgentRuntimeFlash: (
    runId: string,
    body: { port?: string | null; board_type?: string | null; environment?: string | null; start_monitor_after_flash?: boolean | null },
  ) =>
    request<{ run: ProductAgentRun }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/confirm-flash`, { method: "POST", body: JSON.stringify(body) }),
  agentRuntimeEventsUrl: (runId: string) => `${API_BASE}/agent-runtime/runs/${encodeURIComponent(runId)}/events`,
  agentRuntimeGraph: (runId: string) =>
    request<{ graph: import("@/types").AgentExecutionGraph }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/graph`),
  agentRuntimeSessionActivityUrl: (sessionId: string, afterSequence = 0) =>
    `${API_BASE}/agent-runtime/sessions/${encodeURIComponent(sessionId)}/activity?after_sequence=${encodeURIComponent(String(Math.max(0, afterSequence)))}`,
  agentRuntimeSessionEventsUrl: (sessionId: string, afterSequence = 0) =>
    `${API_BASE}/agent-runtime/sessions/${encodeURIComponent(sessionId)}/events?after_sequence=${encodeURIComponent(String(Math.max(0, afterSequence)))}`,
  agentRuntimeChanges: (runId: string) =>
    request<{ change_set: ChangeSetResponse }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/changes`),
  agentRuntimeSessions: (projectId?: string | null) =>
    request<{ sessions: ForgeAgentSession[]; count: number }>(
      projectId ? `/agent-runtime/sessions?project_id=${encodeURIComponent(projectId)}` : "/agent-runtime/sessions",
    ),
  createAgentRuntimeSession: (body: { project_id: string; title?: string | null }) =>
    request<{ session: ForgeAgentSession }>("/agent-runtime/sessions", { method: "POST", body: JSON.stringify(body) }),
  agentRuntimeSession: (sessionId: string) =>
    request<{ session: ForgeAgentSession }>(`/agent-runtime/sessions/${encodeURIComponent(sessionId)}`),
  sendAgentRuntimeSessionMessage: (
    sessionId: string,
    body: {
      content: string;
      provider_id?: string | null;
      autonomy?: "auto" | "staged_changes" | "plan_only" | "build_only" | "build_then_confirm_flash";
      board_port?: string | null;
      board_type?: string | null;
      environment?: string | null;
      start_monitor_after_flash?: boolean;
      context?: ForgeComposerContext;
    },
  ) =>
    request<{ intent?: string; decision?: Record<string, unknown>; activity_events?: ForgeAgentActivityEvent[]; session: ForgeAgentSession; run: ProductAgentRun | null }>(`/agent-runtime/sessions/${encodeURIComponent(sessionId)}/messages`, { method: "POST", body: JSON.stringify(body) }),
  cancelAgentRuntimeSession: (sessionId: string) =>
    request<{ activity_events?: ForgeAgentActivityEvent[]; session: ForgeAgentSession; run: ProductAgentRun | null }>(`/agent-runtime/sessions/${encodeURIComponent(sessionId)}/cancel`, { method: "POST", body: JSON.stringify({}) }),

  changeSet: (changeSetId: string) =>
    request<{ change_set: ChangeSetResponse }>(`/changes/${encodeURIComponent(changeSetId)}`),
  applyChangeSet: (changeSetId: string) =>
    request<{ change_set: ChangeSetResponse }>(`/changes/${encodeURIComponent(changeSetId)}/apply`, { method: "POST", body: JSON.stringify({}) }),
  discardChangeSet: (changeSetId: string) =>
    request<{ change_set: ChangeSetResponse }>(`/changes/${encodeURIComponent(changeSetId)}/discard`, { method: "POST", body: JSON.stringify({}) }),
  undoChangeSet: (changeSetId: string) =>
    request<{ change_set: ChangeSetResponse }>(`/changes/${encodeURIComponent(changeSetId)}/undo`, { method: "POST", body: JSON.stringify({}) }),

  execute: (
    prompt: string,
    taskId: string,
    options: {
      projectId?: string | null;
      workspaceRoot?: string | null;
      selectedBoard?: string | null;
      selectedFramework?: string | null;
      generationMode?: "new_project" | "modify_existing_project" | "generate_into_open_folder";
    } = {},
    signal?: AbortSignal,
  ) => request<ExecuteResponse>("/execute", {
    method: "POST",
    body: JSON.stringify({
      prompt,
      task_id: taskId,
      project_id: options.projectId ?? undefined,
      workspace_root: options.workspaceRoot ?? undefined,
      selected_board: options.selectedBoard ?? undefined,
      selected_framework: options.selectedFramework ?? undefined,
      generation_mode: options.generationMode ?? undefined,
    }),
    signal,
  }),
  cancelExecution: (taskId: string) =>
    request<CancelExecutionResponse>(`/execute/${encodeURIComponent(taskId)}/cancel`, { method: "POST", body: JSON.stringify({}) }),
};

function errorBodyMessage(body: ApiErrorBody & { detail?: unknown; error?: unknown }) {
  return (
    stringField(body.message)
    ?? stringField(body.detail)
    ?? stringField(body.error)
    ?? toErrorMessage(body, "")
  ) || null;
}

function stringField(value: unknown) {
  return typeof value === "string" && value.trim() ? value : null;
}
