import type {
  ApiErrorBody,
  AGYScratchImportResult,
  AGYAssistedRunResult,
  BuildResponse,
  BuildHistoryResponse,
  CancelExecutionResponse,
  CodexLoginLaunchResponse,
  CodexOAuthStatusResponse,
  CodexOAuthSmokeResponse,
  CodexStatusDiagnosticsResponse,
  DetectedBoardsResponse,
  ExecuteResponse,
  FileContentResponse,
  FlashResponse,
  ForgeXSettingsExportResponse,
  ForgeXSettingsResponse,
  ForgeXSettingValue,
  ForgeXSettingsSchemaResponse,
  GenerationDiagnosticsRunsResponse,
  HealthResponse,
  ModelProvidersResponse,
  ModelBridgesResponse,
  ModelProviderResponse,
  ModelUsageResponse,
  ModelRoutesResponse,
  ProviderModelsResponse,
  ProviderHealthResponse,
  MonitorStatusResponse,
  ProjectImportResponse,
  ProjectFilesResponse,
  RollbackSnapshotCreateResponse,
  RollbackSnapshotsResponse,
  BridgeSafetyStatusResponse,
  RollbackRestoreResult,
  RollbackRestoresResponse,
  RollbackRestorePreflightResult,
  TerminalOutputResponse,
  TerminalSessionResponse,
  ProjectListResponse,
  WorkspaceLogsResponse,
  BridgeApprovalDecisionResponse,
  BridgePatchExportResponse,
  BridgePatchesResponse,
  PatchPreflightResult,
  PatchApplyResult,
  PatchAppliesResponse,
  BridgeReviewResponse,
  BridgeReviewsResponse,
  BridgeSandboxRunResponse,
  BridgeSandboxStatusResponse,
  BridgeSnapshotResponse,
  GenericProviderListResponse,
  GenericAgentQaFixtureResponse,
  GenericRunCancelResponse,
  GenericRunDetailResponse,
  GenericRunStartResponse,
  ProductAgentProvider,
  ProductAgentRun,
} from "@/types";
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
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    let body: ApiErrorBody | unknown = {};
    try {
      body = await response.json();
    } catch {
      // The server may be offline or return a non-JSON proxy response.
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
    request<FileContentResponse>(
      `/files/content?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(path)}`,
    ),
  createFile: (body: {
    project_id: string;
    path: string;
    kind?: "file" | "folder";
    content?: string;
    file_type?: string;
  }) =>
    request<ProjectFilesResponse>("/files", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateFile: (body: {
    project_id: string;
    path: string;
    content?: string;
    new_path?: string;
    kind?: "file" | "folder";
    file_type?: string;
  }) =>
    request<FileContentResponse | ProjectFilesResponse>("/files", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteFile: (projectId: string, path: string) =>
    request<{ project_id: string; path: string; deleted: boolean; deleted_count: number }>(
      `/files?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(path)}`,
      { method: "DELETE" },
    ),
  deleteProject: (projectId: string) =>
    request<{ project_id: string; deleted: boolean }>(`/projects/${encodeURIComponent(projectId)}`, {
      method: "DELETE",
    }),
  buildHistory: () => request<BuildHistoryResponse>("/build-history"),
  detectedBoards: () => request<DetectedBoardsResponse>("/devices/boards"),
  buildProject: (body: { project_id: string; environment?: string | null }) =>
    request<BuildResponse>("/build", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  flashProject: (body: {
    project_id: string;
    board_type: string;
    port: string;
    environment?: string | null;
    baudrate?: number;
    verify?: boolean;
    timeout_s?: number;
  }) =>
    request<FlashResponse>("/flash", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  startMonitor: (body: { project_id?: string | null; port?: string | null; baudrate?: number; timeout_s?: number }) =>
    request<MonitorStatusResponse>("/monitor/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  stopMonitor: () =>
    request<MonitorStatusResponse>("/monitor/stop", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  monitorStatus: () => request<MonitorStatusResponse>("/monitor/status"),
  logs: (executionId?: string) =>
    request<WorkspaceLogsResponse>(
      executionId ? `/logs?execution_id=${encodeURIComponent(executionId)}` : "/logs",
    ),
  startTerminal: (body: { project_id: string }) =>
    request<TerminalSessionResponse>("/terminal/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  terminalOutput: (sessionId: string, after = 0) =>
    request<TerminalOutputResponse>(
      `/terminal/sessions/${encodeURIComponent(sessionId)}/output?after=${encodeURIComponent(String(after))}`,
    ),
  writeTerminal: (sessionId: string, data: string) =>
    request<TerminalSessionResponse>(`/terminal/sessions/${encodeURIComponent(sessionId)}/input`, {
      method: "POST",
      body: JSON.stringify({ data }),
    }),
  clearTerminal: (sessionId: string) =>
    request<TerminalSessionResponse>(`/terminal/sessions/${encodeURIComponent(sessionId)}/clear`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  resizeTerminal: (sessionId: string, cols: number, rows: number) =>
    request<TerminalSessionResponse>(`/terminal/sessions/${encodeURIComponent(sessionId)}/resize`, {
      method: "POST",
      body: JSON.stringify({ cols, rows }),
    }),
  stopTerminal: (sessionId: string) =>
    request<{ session_id: string; closed: boolean }>(`/terminal/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    }),
  modelProviders: () => request<ModelProvidersResponse>("/models/providers"),
  modelBridges: () => request<ModelBridgesResponse>("/models/bridges"),
  refreshModelBridges: () =>
    request<ModelBridgesResponse>("/models/bridges/refresh", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  antigravitySandboxStatus: () =>
    request<BridgeSandboxStatusResponse>("/models/bridges/antigravity/sandbox-status"),
  bridgeSafetyStatus: () => request<BridgeSafetyStatusResponse>("/models/bridges/safety-status"),
  startAntigravitySandboxRun: (body: { workspace_root: string; prompt: string; timeout_seconds?: number }) =>
    request<{ run: BridgeSandboxRunResponse }>("/models/bridges/antigravity/sandbox-run", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  bridgeRuns: () => request<{ runs: BridgeSandboxRunResponse[] }>("/models/bridges/runs"),
  bridgeRun: (runId: string) =>
    request<{ run: BridgeSandboxRunResponse }>(`/models/bridges/runs/${encodeURIComponent(runId)}`),
  cancelBridgeRun: (runId: string) =>
    request<{ run: BridgeSandboxRunResponse }>(`/models/bridges/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  genericProviders: () => request<GenericProviderListResponse>("/models/bridges/providers"),
  genericAgentQaFixture: (state: string) =>
    request<GenericAgentQaFixtureResponse>(`/models/bridges/generic/qa-fixtures/${encodeURIComponent(state)}`),
  startGenericRun: (body: {
    provider_id: "agy";
    project_id: string;
    instruction: string;
    timeout_seconds: number;
    idempotency_key: string;
  }) =>
    request<GenericRunStartResponse>("/models/bridges/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  genericRun: (runId: string) =>
    request<GenericRunDetailResponse>(`/models/bridges/generic/runs/${encodeURIComponent(runId)}`),
  genericRuns: (projectId: string, limit = 25) =>
    request<{ runs: GenericRunDetailResponse[]; count: number; limit: number; offset: number }>(
      `/models/bridges/generic/runs?project_id=${encodeURIComponent(projectId)}&limit=${encodeURIComponent(String(limit))}`,
    ),
  cancelGenericRun: (runId: string) =>
    request<GenericRunCancelResponse>(`/models/bridges/generic/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  genericRunEventsUrl: (runId: string) =>
    `${API_BASE}/models/bridges/runs/${encodeURIComponent(runId)}/events`,
  agentRuntimeProviders: () =>
    request<{ enabled: boolean; providers: ProductAgentProvider[] }>("/agent-runtime/providers"),
  codexOAuthStatus: () =>
    request<CodexOAuthStatusResponse>("/agent-runtime/providers/codex-oauth/status"),
  codexStatusDiagnostics: () =>
    request<CodexStatusDiagnosticsResponse>("/agent-runtime/providers/codex-oauth/status-diagnostics"),
  launchCodexOAuthLogin: () =>
    request<CodexLoginLaunchResponse>("/agent-runtime/providers/codex-oauth/login/launch", {
      method: "POST",
      body: JSON.stringify({ confirm_launch_codex_login: true }),
    }),
  runCodexOAuthSmoke: () =>
    request<CodexOAuthSmokeResponse>("/agent-runtime/providers/codex-oauth/standalone-smoke", {
      method: "POST",
      body: JSON.stringify({ confirm_real_codex: true }),
    }),
  importAGYScratchProject: (sourcePath: string) =>
    request<AGYScratchImportResult>("/agent-runtime/agy-scratch-import", {
      method: "POST",
      body: JSON.stringify({ source_path: sourcePath }),
    }),
  runAGYAssisted: () =>
    request<AGYAssistedRunResult>("/agent-runtime/agy-assisted-runs", {
      method: "POST",
      body: JSON.stringify({ template: "esp32-platformio-blink", prompt: "Create an ESP32 blink project" }),
    }),
  startAgentRuntimeRun: (body: { project_id: string; instruction: string; provider_id?: string; timeout_seconds?: number; idempotency_key?: string }) =>
    request<{ run: ProductAgentRun }>("/agent-runtime/runs", { method: "POST", body: JSON.stringify(body) }),
  agentRuntimeRun: (runId: string) =>
    request<{ run: ProductAgentRun }>(`/agent-runtime/runs/${encodeURIComponent(runId)}`),
  cancelAgentRuntimeRun: (runId: string) =>
    request<{ run: ProductAgentRun }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST", body: JSON.stringify({}) }),
  resolveAgentRuntimeApproval: (runId: string, approvalId: string, decision: "approve_once" | "approve_session" | "decline" | "cancel") =>
    request<{ approval: Record<string, string> }>(`/agent-runtime/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`, { method: "POST", body: JSON.stringify({ decision }) }),
  agentRuntimeEventsUrl: (runId: string) =>
    `${API_BASE}/agent-runtime/runs/${encodeURIComponent(runId)}/events`,
  createBridgeSnapshot: (body: { workspace_root: string }) =>
    request<{ snapshot: BridgeSnapshotResponse }>("/models/bridges/reviews/snapshot", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createBridgeReviewDiff: (body: { provider_id: string; workspace_root: string; snapshot_id: string }) =>
    request<{ review: BridgeReviewResponse }>("/models/bridges/reviews/diff", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  bridgeReview: (reviewId: string) =>
    request<{ review: BridgeReviewResponse }>(`/models/bridges/reviews/${encodeURIComponent(reviewId)}`),
  exportBridgeReviewPatch: (reviewId: string) =>
    request<{ patch: BridgePatchExportResponse }>(
      `/models/bridges/reviews/${encodeURIComponent(reviewId)}/export-patch`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  bridgeReviewPatch: (reviewId: string) =>
    fetch(`${API_BASE}/models/bridges/reviews/${encodeURIComponent(reviewId)}/patch`, {
      cache: "no-store",
    }).then(async (response) => {
      if (!response.ok) {
        let body: unknown = {};
        try {
          body = await response.json();
        } catch {
          // Non-JSON patch proxy errors still produce a readable Error.
        }
        const record = body && typeof body === "object" ? (body as ApiErrorBody & { detail?: unknown; error?: unknown }) : {};
        throw new PromptForgeApiError(
          errorBodyMessage(record) ?? `ForgeX API request failed (${response.status})`,
          response.status,
          record.code,
        );
      }
      return response.text();
    }),
  bridgeReviewPatchMetadata: (reviewId: string) =>
    request<{ patch: BridgePatchExportResponse }>(
      `/models/bridges/reviews/${encodeURIComponent(reviewId)}/patch-metadata`,
    ),
  verifyBridgeReviewPatch: (reviewId: string) =>
    request<{ patch: BridgePatchExportResponse }>(
      `/models/bridges/reviews/${encodeURIComponent(reviewId)}/verify-patch`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  markBridgeReviewPatchCopied: (reviewId: string) =>
    request<{ patch: BridgePatchExportResponse }>(
      `/models/bridges/reviews/${encodeURIComponent(reviewId)}/patch-copied`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  openBridgeReviewPatchFolder: (reviewId: string) =>
    request<{ opened: boolean; patch_path: string; review_id: string }>(
      `/models/bridges/reviews/${encodeURIComponent(reviewId)}/open-patch-folder`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  bridgePatches: (filters: { providerId?: string; reviewId?: string; integrityStatus?: string } = {}) => {
    const params = new URLSearchParams();
    if (filters.providerId) params.set("provider_id", filters.providerId);
    if (filters.reviewId) params.set("review_id", filters.reviewId);
    if (filters.integrityStatus) params.set("integrity_status", filters.integrityStatus);
    const query = params.toString();
    return request<BridgePatchesResponse>(`/models/bridges/patches${query ? `?${query}` : ""}`);
  },
  deleteBridgePatch: (patchId: string) =>
    request<{ deleted: boolean; patch: BridgePatchExportResponse }>(
      `/models/bridges/patches/${encodeURIComponent(patchId)}`,
      { method: "DELETE" },
    ),
  preflightPatch: (patchId: string, workspaceRoot?: string | null) =>
    request<PatchPreflightResult>(`/models/bridges/patches/${encodeURIComponent(patchId)}/preflight`, {
      method: "POST",
      body: JSON.stringify({ workspace_root: workspaceRoot || undefined }),
    }),
  applyPatch: (patchId: string, workspaceRoot: string, confirmation: string) =>
    request<PatchApplyResult>(`/models/bridges/patches/${encodeURIComponent(patchId)}/apply`, {
      method: "POST",
      body: JSON.stringify({ workspace_root: workspaceRoot, confirmation }),
    }),
  patchApplies: () => request<PatchAppliesResponse>("/models/bridges/patch-applies"),
  listPatchApplies: () => request<PatchAppliesResponse>("/models/bridges/patch-applies"),
  patchApply: (applyId: string) =>
    request<{ apply: PatchApplyResult }>(`/models/bridges/patch-applies/${encodeURIComponent(applyId)}`),
  getPatchApply: (applyId: string) =>
    request<{ apply: PatchApplyResult }>(`/models/bridges/patch-applies/${encodeURIComponent(applyId)}`),
  createRollbackSnapshot: (patchId: string, workspaceRoot: string) =>
    request<RollbackSnapshotCreateResponse>(
      `/models/bridges/patches/${encodeURIComponent(patchId)}/rollback-snapshot`,
      { method: "POST", body: JSON.stringify({ workspace_root: workspaceRoot }) },
    ),
  rollbackSnapshots: () => request<RollbackSnapshotsResponse>("/models/bridges/rollback-snapshots"),
  rollbackSnapshot: (rollbackId: string) =>
    request<{ snapshot: import("@/types").RollbackSnapshot }>(`/models/bridges/rollback-snapshots/${encodeURIComponent(rollbackId)}`),
  deleteRollbackSnapshot: (rollbackId: string) =>
    request<{ deleted: boolean; snapshot: import("@/types").RollbackSnapshot }>(
      `/models/bridges/rollback-snapshots/${encodeURIComponent(rollbackId)}`,
      { method: "DELETE" },
    ),
  cleanupRollbackSnapshots: (body: { older_than_days?: number } = {}) =>
    request<{ cleanup: { removed: number; snapshots: import("@/types").RollbackSnapshot[] } }>("/models/bridges/rollback-snapshots/cleanup", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  restorePreflightRollbackSnapshot: (rollbackId: string, workspaceRoot: string) =>
    request<RollbackRestorePreflightResult>(
      `/models/bridges/rollback-snapshots/${encodeURIComponent(rollbackId)}/restore-preflight`,
      { method: "POST", body: JSON.stringify({ workspace_root: workspaceRoot }) },
    ),
  restoreRollbackSnapshot: (rollbackId: string, workspaceRoot: string, confirmation: string) =>
    request<RollbackRestoreResult>(
      `/models/bridges/rollback-snapshots/${encodeURIComponent(rollbackId)}/restore`,
      { method: "POST", body: JSON.stringify({ workspace_root: workspaceRoot, confirmation }) },
    ),
  rollbackRestores: () => request<RollbackRestoresResponse>("/models/bridges/rollback-restores"),
  rollbackRestore: (restoreId: string) =>
    request<{ restore: RollbackRestoreResult }>(`/models/bridges/rollback-restores/${encodeURIComponent(restoreId)}`),
  cleanupBridgePatches: (body: { older_than_days?: number; include_missing?: boolean } = {}) =>
    request<{ cleanup: { removed: number; patches: BridgePatchExportResponse[] } }>("/models/bridges/patches/cleanup", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  bridgeReviews: (filters: { status?: string; providerId?: string } = {}) => {
    const params = new URLSearchParams();
    if (filters.status) params.set("status", filters.status);
    if (filters.providerId) params.set("provider_id", filters.providerId);
    const query = params.toString();
    return request<BridgeReviewsResponse>(`/models/bridges/reviews${query ? `?${query}` : ""}`);
  },
  cleanupBridgeReviews: () =>
    request<{ cleanup: { removed: number; remaining: number }; counts: BridgeReviewsResponse["counts"] }>(
      "/models/bridges/reviews/cleanup",
      { method: "POST", body: JSON.stringify({}) },
    ),
  approveBridgeReview: (reviewId: string) =>
    request<{ review: BridgeReviewResponse; decision: BridgeApprovalDecisionResponse }>(
      `/models/bridges/reviews/${encodeURIComponent(reviewId)}/approve`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  rejectBridgeReview: (reviewId: string) =>
    request<{ review: BridgeReviewResponse; decision: BridgeApprovalDecisionResponse }>(
      `/models/bridges/reviews/${encodeURIComponent(reviewId)}/reject`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  modelRoutes: () => request<ModelRoutesResponse>("/models/routes"),
  configureModelProvider: (
    providerId: string,
    body: { api_key?: string | null; base_url?: string; default_model?: string; enabled?: boolean },
  ) =>
    request<{ provider: ModelProviderResponse }>(`/models/providers/${encodeURIComponent(providerId)}/configure`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  testModelProvider: (providerId: string, modelId?: string) =>
    request<ProviderHealthResponse>(`/models/providers/${encodeURIComponent(providerId)}/health`, {
      method: "POST",
      body: JSON.stringify({ model_id: modelId }),
    }),
  providerModels: (providerId: string) =>
    request<ProviderModelsResponse>(`/models/providers/${encodeURIComponent(providerId)}/models`),
  saveModelRoute: (body: {
    task_type: string;
    provider_id: string;
    model_id: string;
    fallback_enabled?: boolean;
    fallback_provider_id?: string | null;
    local_only?: boolean;
  }) =>
    request<{ route: import("@/types").ModelRouteResponse }>("/models/routes", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  modelUsage: (limit = 25) =>
    request<ModelUsageResponse>(`/models/usage?limit=${encodeURIComponent(String(limit))}`),
  chunkedGenerationRuns: (limit = 25, filters: { executionId?: string; projectId?: string } = {}) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (filters.executionId) params.set("execution_id", filters.executionId);
    if (filters.projectId) params.set("project_id", filters.projectId);
    return request<GenerationDiagnosticsRunsResponse>(`/models/chunked-generation-runs?${params.toString()}`);
  },
  clearGenerationDiagnostics: () =>
    request<{ cleared: boolean }>("/models/generation-diagnostics/clear", {
      method: "POST",
    }),
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
  ) =>
    request<ExecuteResponse>("/execute", {
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
    request<CancelExecutionResponse>(`/execute/${encodeURIComponent(taskId)}/cancel`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
};

function errorBodyMessage(body: ApiErrorBody & { detail?: unknown; error?: unknown }) {
  return (
    stringField(body.message) ??
    stringField(body.detail) ??
    stringField(body.error) ??
    toErrorMessage(body, "")
  ) || null;
}

function stringField(value: unknown) {
  return typeof value === "string" && value.trim() ? value : null;
}
