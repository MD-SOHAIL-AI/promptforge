import { SingleFlight } from "./single-flight";
import { RequestDeadlineError, withRequestDeadline } from "./request-deadline";
import { rendererPolicy } from "./runtime-policy.generated";
import type { ProjectProfile } from "@/types/engine-contracts";
const agentReads = new SingleFlight();

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
  ModelRole,
  ModelRoleBindingsResponse,
  ModelRoutesResponse,
  ModelSelectionResponse,
  MonitorStatusResponse,
  ProductAgentProvider,
  ProductAgentRun,
  ProjectFilesResponse,
  ProjectImportResponse,
  ProjectListResponse,
  ProjectResponse,
  ProviderHealthResponse,
  ProviderModelsResponse,
  TerminalOutputResponse,
  TerminalProfilesResponse,
  TerminalSessionResponse,
  WorkspaceLogsResponse,
  WorkspaceSearchResponse,
} from "@/types";
import { apiErrorCode, toErrorMessage } from "@/lib/errors";
import { emitRuntimeDiagnostic, newRequestId, serializeError } from "@/lib/diagnostics";

const API_BASE = "/api/forgex";
export interface BuildOperation {
  operation_id: string; project_id: string; environment: string | null;
  source_revision: string; state: "queued" | "running" | "passed" | "failed" | "stale" | "interrupted" | "unavailable";
  updated_at: string; result: BuildResponse | null; message?: string;
}

export async function observeBuildCompletion(operationId: string, signal?: AbortSignal): Promise<BuildResponse & { operation_state: BuildOperation["state"] }> {
  const started = performance.now();
  while (performance.now() - started < 300_000) {
    const operation = await request<BuildOperation>(`/build/operations/${encodeURIComponent(operationId)}`, {signal});
    if (!["queued", "running"].includes(operation.state)) {
      if (operation.result) return {...operation.result, operation_state: operation.state};
      throw new ForgeXApiError(operation.message ?? "Saved build result is unavailable.", 0, "BUILD_RESULT_UNAVAILABLE", operation);
    }
    await withRequestDeadline(() => new Promise<void>(resolve => setTimeout(resolve, 1000)), {timeoutMs: 1500, signal});
  }
  throw new ForgeXApiError("Build observation timed out. Refresh the saved operation to retrieve its result.", 0, "REQUEST_RESULT_UNKNOWN", {operation_id: operationId});
}

export class ForgeXApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
    public readonly body?: unknown,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = "ForgeXApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const requestId = newRequestId("renderer-http");
  const started = performance.now();
  const method = init?.method ?? "GET";
  const url = `${API_BASE}${path}`;
  emitRuntimeDiagnostic({
    component: "renderer",
    event: "renderer.http.started",
    level: "DEBUG",
    request_id: requestId,
    method,
    path,
  });
  try {
    return await withRequestDeadline(async (signal) => {
    const response = await fetch(url, {
      ...init,
      signal,
      headers: withRequestHeaders(init?.headers, requestId),
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
      const apiError = new ForgeXApiError(
        errorBodyMessage(record) ?? `ForgeX API request failed (${response.status})`,
        response.status,
        apiErrorCode(record),
        record,
        requestId,
      );
      emitRuntimeDiagnostic({
        component: "renderer",
        event: "renderer.http.failed",
        level: "ERROR",
        request_id: requestId,
        method,
        path,
        status: response.status,
        duration_ms: Math.max(0, Math.round(performance.now() - started)),
        error: serializeError(apiError),
      });
      throw apiError;
    }

    const parsed = (await response.json()) as T;
    if (signal.aborted) throw new RequestDeadlineError(method !== "GET");
    emitRuntimeDiagnostic({
      component: "renderer",
      event: "renderer.http.completed",
      level: "DEBUG",
      request_id: requestId,
      method,
      path,
      status: response.status,
      duration_ms: Math.max(0, Math.round(performance.now() - started)),
    });
    return parsed;
    }, { signal: init?.signal, mutation: method !== "GET",
      timeoutMs: path === "/build" || path === "/flash" ? 300_000 : 1000 * (method === "GET" ? rendererPolicy.read_seconds : rendererPolicy.mutation_seconds) });
  } catch (cause) {
    if (cause instanceof ForgeXApiError) throw cause;
    emitRuntimeDiagnostic({
      component: "renderer",
      event: "renderer.http.failed",
      level: "ERROR",
      request_id: requestId,
      method,
      path,
      duration_ms: Math.max(0, Math.round(performance.now() - started)),
      error: serializeError(cause),
    });
    if (cause instanceof RequestDeadlineError) {
      throw new ForgeXApiError(cause.message, 0, cause.mutation ? "REQUEST_RESULT_UNKNOWN" : "REQUEST_TIMED_OUT", undefined, requestId);
    }
    throw cause;
  }
}

export const forgeXApi = {
  health: () => request<HealthResponse>("/health"),
  runtimeDiagnostics: (signal?: AbortSignal) => request<{backend_instance_id:string; runtime_contract:string; source_fingerprint:string; data_profile:string | null; observed_at:string; packaged:boolean}>("/diagnostics/runtime", {signal}),
  runDiagnostics: (runId: string) => request<Record<string, unknown>>(`/diagnostics/runs/${encodeURIComponent(runId)}/export`),
  agentBuildEvidence: (runId: string, environment: string | null, signal?: AbortSignal) => request<{state:"idle" | "failed" | "passed" | "stale" | "unavailable"; scope:"workspace" | "saved_draft"}>(`/agent-runtime/runs/${encodeURIComponent(runId)}/build-evidence${environment ? `?environment=${encodeURIComponent(environment)}` : ""}`, {signal}),

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
  projectProfile: (projectId: string, environment?: string | null) =>
    request<ProjectProfile>(`/projects/${encodeURIComponent(projectId)}/profile${environment ? `?environment=${encodeURIComponent(environment)}` : ""}`),
  projectReadiness: (projectId: string, providerId?: string, modelId?: string, environment?: string | null, signal?: AbortSignal) => {
    const query = new URLSearchParams();
    if (providerId) query.set("provider_id", providerId);
    if (modelId) query.set("model_id", modelId);
    if (environment) query.set("environment", environment);
    return request<{observed_at: string; provider: {configured: boolean; cached_status?: string; checked_at?: string | null; fresh?: boolean} | null; toolchain: {available: boolean} | null; environment: {selection_required: boolean}; permissions: {local_builds: boolean}; capabilities: {generate: boolean; build: boolean}}>(`/projects/${encodeURIComponent(projectId)}/readiness?${query}`, {signal});
  },
  importProject: (body: { path: string }) =>
    request<ProjectImportResponse>("/projects/import", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  refreshProject: (projectId: string) =>
    request<ProjectResponse>(`/projects/${encodeURIComponent(projectId)}/refresh`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  projectFiles: (projectId: string) =>
    request<ProjectFilesResponse>(`/projects/${encodeURIComponent(projectId)}/files`),
  searchProjectFileContents: (projectId: string, query: string) =>
    request<WorkspaceSearchResponse>(
      `/projects/${encodeURIComponent(projectId)}/files/search?query=${encodeURIComponent(query)}`,
    ),
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
  forgetRecentProject: (projectId: string) =>
    request<{ project_id: string; deleted: boolean }>(`/projects/${encodeURIComponent(projectId)}/recent`, { method: "DELETE" }),
  locateRecentProject: (projectId: string, path: string) =>
    request<ProjectResponse>(`/projects/${encodeURIComponent(projectId)}/locate`, { method: "POST", body: JSON.stringify({ path }) }),

  buildHistory: () => request<BuildHistoryResponse>("/build-history"),
  detectedBoards: () => request<DetectedBoardsResponse>("/devices/boards"),
  buildProject: async (body: { project_id: string; environment?: string | null }, operationId = crypto.randomUUID(), signal?: AbortSignal) => {
    await request<BuildOperation>("/build/operations", {method: "POST", headers: {"Idempotency-Key": operationId}, body: JSON.stringify(body), signal});
    return observeBuildCompletion(operationId, signal);
  },
  buildOperation: (operationId: string, signal?: AbortSignal) => request<BuildOperation>(`/build/operations/${encodeURIComponent(operationId)}`, {signal}),
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
    body: { api_key?: string | null; base_url?: string; default_model?: string; enabled?: boolean; options?: Record<string, unknown> | null },
  ) => request<{ provider: ModelProviderResponse }>(`/models/providers/${encodeURIComponent(providerId)}/configure`, { method: "POST", body: JSON.stringify(body) }),
  createModelProvider: (body: {
    provider_id: string;
    display_name: string;
    protocol: "openai-completions" | "openai-responses" | "anthropic-messages";
    base_url: string;
    default_model: string;
    models?: string[];
    local?: boolean;
    api_key?: string;
  }) => request<{ provider: ModelProviderResponse }>("/models/providers", { method: "POST", body: JSON.stringify(body) }),
  deleteModelProvider: (providerId: string) =>
    request<{ provider_id: string; deleted: boolean }>(`/models/providers/${encodeURIComponent(providerId)}`, { method: "DELETE" }),
  testModelProvider: (providerId: string) =>
    request<ProviderHealthResponse>(`/models/providers/${encodeURIComponent(providerId)}/health`, { method: "POST" }),
  providerModels: (providerId: string) =>
    request<ProviderModelsResponse>(`/models/providers/${encodeURIComponent(providerId)}/models`),
  modelRoleBindings: (sessionId?: string) =>
    request<ModelRoleBindingsResponse>(
      `/v2/models/role-bindings${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`,
    ),
  saveModelRoleBinding: (body: {
    provider_id: string;
    model_id: string;
    role: ModelRole;
    session_id?: string;
  }) => request<{ provider_id: string; model_id: string; role: ModelRole; session_id: string | null }>(
    "/v2/models/role-bindings",
    { method: "PUT", body: JSON.stringify(body) },
  ),
  clearModelRoleBinding: (role: ModelRole, sessionId?: string) =>
    request<{ removed: boolean }>(
      `/v2/models/role-bindings/${encodeURIComponent(role)}${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`,
      { method: "DELETE" },
    ),

  agentRuntimeProviders: () => request<{ enabled: boolean; providers: ProductAgentProvider[] }>("/agent-runtime/providers"),
  startAgentRuntimeRun: (body: {
    project_id: string;
    instruction: string;
    provider_id: string;
    autonomy?: "staged_changes" | "build_only" | "simulate" | "build_then_confirm_flash";
    execution_profile?: "standard" | "advanced";
    board_port?: string | null;
    board_type?: string | null;
    environment?: string | null;
    start_monitor_after_flash?: boolean;
  }) =>
    request<{ run: ProductAgentRun }>("/agent-runtime/runs", { method: "POST", body: JSON.stringify(body) }),
  agentRuntimeRun: (runId: string, signal?: AbortSignal) => {
    const pending = agentReads.run(`run:${runId}`, () => request<{ run: ProductAgentRun }>(`/agent-runtime/runs/${encodeURIComponent(runId)}`));
    return signal ? withRequestDeadline(() => pending, {signal, timeoutMs:1000 * rendererPolicy.read_seconds}) : pending;
  },
  cancelAgentRuntimeRun: (runId: string) =>
    request<{ run: ProductAgentRun }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST", body: JSON.stringify({}) }),
  retryAgentRuntimeRun: (runId: string) =>
    request<{ run: ProductAgentRun }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/retry`, { method: "POST", body: JSON.stringify({}) }),
  resumeAgentRuntimeRun: (runId: string, checkpointId: string, idempotencyKey: string, selection?: {provider_id: string; model_id: string}) =>
    request<{ run: ProductAgentRun }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/resume`, {
      method: "POST", body: JSON.stringify({ checkpoint_id: checkpointId, idempotency_key: idempotencyKey, ...selection }),
    }),
  verifyAgentRuntimeDraft: (runId: string, idempotencyKey: string) =>
    request<{run: ProductAgentRun}>(`/agent-runtime/runs/${encodeURIComponent(runId)}/verify-draft`, {
      method: "POST", body: JSON.stringify({idempotency_key: idempotencyKey}),
    }),
  discardAgentRuntimeDraft: (runId: string) =>
    request<{run: ProductAgentRun}>(`/agent-runtime/runs/${encodeURIComponent(runId)}/discard-draft`, { method: "POST", body: JSON.stringify({}) }),
  agentJobAction: (jobId: string, action: "pause" | "resume" | "budget" | "recovery-policy", body: Record<string, unknown> = {}) =>
    request<{job?: import("@/types").AgentJob; run?: ProductAgentRun}>(`/agent-runtime/jobs/${encodeURIComponent(jobId)}/${action}`, {method: "POST", body: JSON.stringify(body)}),
  agentJob: (jobId: string) => request<{job: import("@/types").AgentJob; recovery: ProductAgentRun["recovery"]}>(`/agent-runtime/jobs/${encodeURIComponent(jobId)}`),
  projectAgentTrust: (projectId: string) => request<{trusted: boolean}>(`/agent-runtime/projects/${encodeURIComponent(projectId)}/trust`),
  setProjectAgentTrust: (projectId: string, trusted: boolean) =>
    request<{trusted: boolean}>(`/agent-runtime/projects/${encodeURIComponent(projectId)}/trust`, {method: "POST", body: JSON.stringify({trusted})}),
  steerAgentRuntimeRun: (runId: string, content: string) =>
    request<{ run: ProductAgentRun; steering_queued: boolean }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/steer`, { method: "POST", body: JSON.stringify({ content }) }),
  agentRuntimeGuidanceQueue: (runId: string) =>
    agentReads.run(`queue:${runId}`, () =>
      request<{ items: import("@/types").AgentGuidanceQueueItem[] }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/queue`),
    ),
  editAgentRuntimeGuidance: (runId: string, itemId: string, content: string) =>
    request<{ item: import("@/types").AgentGuidanceQueueItem }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/queue/${encodeURIComponent(itemId)}`, { method: "PATCH", body: JSON.stringify({ content }) }),
  removeAgentRuntimeGuidance: (runId: string, itemId: string) =>
    request<{ item: import("@/types").AgentGuidanceQueueItem }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/queue/${encodeURIComponent(itemId)}`, { method: "DELETE" }),
  applyAgentRuntimeChanges: (runId: string) =>
    request<{ run: ProductAgentRun }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/apply-changes`, { method: "POST", body: JSON.stringify({}) }),
  confirmAgentRuntimeFlash: (
    runId: string,
    body: { port?: string | null; board_type?: string | null; environment?: string | null; start_monitor_after_flash?: boolean | null },
  ) =>
    request<{ run: ProductAgentRun }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/confirm-flash`, { method: "POST", body: JSON.stringify(body) }),
  agentRuntimeEventsUrl: (runId: string) => `${API_BASE}/agent-runtime/runs/${encodeURIComponent(runId)}/events`,
  agentRuntimeGraph: (runId: string) =>
    agentReads.run(`graph:${runId}`, () => request<{ graph: import("@/types").AgentExecutionGraph }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/graph`)),
  agentRuntimeSessionActivityUrl: (sessionId: string, afterSequence = 0) =>
    `${API_BASE}/agent-runtime/sessions/${encodeURIComponent(sessionId)}/activity?after_sequence=${encodeURIComponent(String(Math.max(0, afterSequence)))}`,
  agentRuntimeSessionEventsUrl: (sessionId: string, afterSequence = 0) =>
    `${API_BASE}/agent-runtime/sessions/${encodeURIComponent(sessionId)}/events?after_sequence=${encodeURIComponent(String(Math.max(0, afterSequence)))}`,
  agentRuntimeChanges: (runId: string) =>
    request<{ change_set: ChangeSetResponse }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/changes`),
  chatSessions: () => agentReads.run("chat:sessions", () => request<{ sessions: import("@/types").ForgeChatSession[] }>("/v1/chat/sessions")),
  createChatSession: () => request<{ session: import("@/types").ForgeChatSession }>("/v1/chat/sessions", { method: "POST", body: JSON.stringify({}) }),
  chatSession: (id: string) => agentReads.run(`chat:session:${id}`, () => request<{ session: import("@/types").ForgeChatSession }>(`/v1/chat/sessions/${encodeURIComponent(id)}`)),
  sendChatMessage: (id: string, body: { content: string; provider_id?: string; model_id?: string; context_project_id?: string; context_paths?: string[]; retry?: boolean }) => request<{ session: import("@/types").ForgeChatSession }>(`/v1/chat/sessions/${encodeURIComponent(id)}/messages`, { method: "POST", body: JSON.stringify(body) }),
  chatSessionEventsUrl: (id: string) => `${API_BASE}/v1/chat/sessions/${encodeURIComponent(id)}/events`,
  cancelChatSession: (id: string) => request<{ session: import("@/types").ForgeChatSession }>(`/v1/chat/sessions/${encodeURIComponent(id)}/cancel`, { method: "POST", body: JSON.stringify({}) }),
  configureChat: (id: string, body: { title?: string; provider_id?: string; model_id?: string }) => request<{ session: import("@/types").ForgeChatSession }>(`/v1/chat/sessions/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteChat: (id: string) => request<{ deleted: boolean }>(`/v1/chat/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  configureConversation: (id: string, body: { title?: string; provider_id?: string; model_id?: string }) => request<{ session: import("@/types").ForgeChatSession }>(`/agent-runtime/sessions/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteConversation: (id: string) => request<{ deleted: boolean }>(`/agent-runtime/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  agentRuntimeSessions: (projectId?: string | null) =>
    agentReads.run(`sessions:${projectId ?? ""}`, () =>
      request<{ sessions: ForgeAgentSession[]; count: number }>(
        projectId ? `/agent-runtime/sessions?project_id=${encodeURIComponent(projectId)}` : "/agent-runtime/sessions",
      ),
    ),
  createAgentRuntimeSession: (body: { project_id: string; title?: string | null }) =>
    request<{ session: ForgeAgentSession }>("/agent-runtime/sessions", { method: "POST", body: JSON.stringify(body) }),
  agentRuntimeSession: (sessionId: string) =>
    agentReads.run(`session:${sessionId}`, () =>
      request<{ session: ForgeAgentSession }>(`/agent-runtime/sessions/${encodeURIComponent(sessionId)}`),
    ),
  sendAgentRuntimeSessionMessage: (
    sessionId: string,
    body: {
      content: string;
      provider_id?: string | null;
      model_id?: string | null;
      autonomy?: "auto" | "read_only" | "staged_changes" | "review_changes" | "plan_only" | "build_only_strict" | "build_only" | "simulate" | "build_then_confirm_flash";
      execution_profile?: "standard" | "advanced";
      board_port?: string | null;
      board_type?: string | null;
      environment?: string | null;
      start_monitor_after_flash?: boolean;
      approved_plan_id?: string | null;
      approved_plan_revision?: number | null;
      approved_plan_hash?: string | null;
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

function withRequestHeaders(headers: HeadersInit | undefined, requestId: string): Headers {
  const next = new Headers(headers);
  if (!next.has("Content-Type")) next.set("Content-Type", "application/json");
  next.set("X-Request-ID", requestId);
  return next;
}

// Backward-compatible exports while legacy workspace hooks are retired.
export const promptForgeApi = forgeXApi;
export { ForgeXApiError as PromptForgeApiError };
