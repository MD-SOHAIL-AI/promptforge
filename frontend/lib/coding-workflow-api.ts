import type {
  ApiErrorBody,
  CodingWorkflowActionResult,
  CodingWorkflowApiGenerateRequest,
  CodingWorkflowApiStatus,
  CodingWorkflowContextFilesRequest,
  CodingWorkflowContextFilesResponse,
  CodingWorkflowContextPreview,
  CodingWorkflowContextPreviewRequest,
  CodingWorkflowEventsResponse,
  CodingWorkflowGenerateRequest,
  CodingWorkflowProviderMode,
  CodingWorkflowRepairBuildRequest,
  CodingWorkflowRunResponse,
  CodingWorkflowRunsResponse,
  CodingWorkflowStaleRunsResponse,
} from "@/types";
import { toErrorMessage } from "@/lib/errors";

const API_BASE = "/api/promptforge";
const WORKFLOW_BASE = "/models/coding-workflow";
const ENABLE_MESSAGE =
  "Enable FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW=1 and FORGEX_ENABLE_FAKE_API_CODING_AGENT=1 to use this experimental workflow.";
const REAL_API_ENABLE_MESSAGE =
  "Enable FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW=1 and FORGEX_ENABLE_REAL_API_CODING_AGENT=1, then configure a healthy model provider in Model Settings.";

export class CodingWorkflowApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
  ) {
    super(message);
    this.name = "CodingWorkflowApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${WORKFLOW_BASE}${path}`, {
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
      // Keep proxy/offline errors readable without exposing raw internals.
    }
    const record = body && typeof body === "object" ? (body as ApiErrorBody & { detail?: unknown; error?: unknown }) : {};
    throw new CodingWorkflowApiError(
      workflowErrorMessage(record) ?? `Coding workflow request failed (${response.status})`,
      response.status,
      record.code,
    );
  }

  return (await response.json()) as T;
}

export const codingWorkflowApi = {
  generateFakeCodingWorkflowReview: (body: CodingWorkflowGenerateRequest) =>
    request<CodingWorkflowActionResult>("/fake/generate-review", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  generateRealApiCodingWorkflowReview: (body: CodingWorkflowApiGenerateRequest) =>
    request<CodingWorkflowActionResult>("/api/generate-review", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getRealApiCodingWorkflowStatus: (params?: { provider_id?: string | null; model?: string | null }) => {
    const query = new URLSearchParams();
    if (params?.provider_id?.trim()) query.set("provider_id", params.provider_id.trim());
    if (params?.model?.trim()) query.set("model", params.model.trim());
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return request<CodingWorkflowApiStatus>(`/api/status${suffix}`);
  },
  generateCodingWorkflowReview: (providerMode: CodingWorkflowProviderMode, body: CodingWorkflowGenerateRequest | CodingWorkflowApiGenerateRequest) =>
    providerMode === "api"
      ? codingWorkflowApi.generateRealApiCodingWorkflowReview(body as CodingWorkflowApiGenerateRequest)
      : codingWorkflowApi.generateFakeCodingWorkflowReview(body as CodingWorkflowGenerateRequest),
  approveCodingWorkflowApply: (runId: string, body: { approval_confirmed: true; approved_by?: string | null; workspace_path?: string | null }) =>
    request<CodingWorkflowActionResult>(`/${encodeURIComponent(runId)}/approve-apply`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runCodingWorkflowBuild: (runId: string, body: { build_confirmed: true; workspace_path?: string | null; environment?: string | null }) =>
    request<CodingWorkflowActionResult>(`/${encodeURIComponent(runId)}/build`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runCodingWorkflowFlash: (runId: string, body: { flash_confirmed: true; workspace_path?: string | null; port: string; board_id: string }) =>
    request<CodingWorkflowActionResult>(`/${encodeURIComponent(runId)}/flash`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runCodingWorkflowMonitor: (runId: string, body: {
    monitor_confirmed: true;
    port?: string | null;
    baud_rate?: number | null;
    duration_seconds?: number | null;
    max_output_bytes?: number | null;
  }) =>
    request<CodingWorkflowActionResult>(`/${encodeURIComponent(runId)}/monitor`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  generateBuildRepairReview: (runId: string, body: CodingWorkflowRepairBuildRequest) =>
    request<CodingWorkflowActionResult>(`/${encodeURIComponent(runId)}/repair/build`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelCodingWorkflow: (runId: string, body: { cancel_confirmed: true; reason?: string | null }) =>
    request<CodingWorkflowActionResult>(`/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listStaleCodingWorkflowRuns: (thresholdSeconds = 1800, limit = 25) =>
    request<CodingWorkflowStaleRunsResponse>(`/recovery/stale?threshold_seconds=${encodeURIComponent(String(thresholdSeconds))}&limit=${encodeURIComponent(String(limit))}`),
  markStaleCodingWorkflowFailed: (runId: string, body: { recovery_confirmed: true; reason?: string | null }, thresholdSeconds = 1800) =>
    request<CodingWorkflowActionResult>(`/${encodeURIComponent(runId)}/recovery/mark-failed?threshold_seconds=${encodeURIComponent(String(thresholdSeconds))}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  clearStaleCodingWorkflowLock: (runId: string, body: { clear_lock_confirmed: true; reason?: string | null }) =>
    request<CodingWorkflowActionResult>(`/${encodeURIComponent(runId)}/recovery/clear-lock`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  previewCodingWorkflowContext: (body: CodingWorkflowContextPreviewRequest) =>
    request<CodingWorkflowContextPreview>("/context/preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listCodingWorkflowContextFiles: (body: CodingWorkflowContextFilesRequest) =>
    request<CodingWorkflowContextFilesResponse>("/context/files", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listCodingWorkflowRuns: (limit = 25) =>
    request<CodingWorkflowRunsResponse>(`?limit=${encodeURIComponent(String(limit))}`),
  getCodingWorkflowRun: (runId: string) =>
    request<CodingWorkflowRunResponse>(`/${encodeURIComponent(runId)}`),
  getCodingWorkflowEvents: (runId: string) =>
    request<CodingWorkflowEventsResponse>(`/${encodeURIComponent(runId)}/events`),
};

export function codingWorkflowErrorMessage(error: unknown): string {
  if (error instanceof CodingWorkflowApiError) return error.message;
  return toErrorMessage(error, "Coding workflow request failed");
}

function workflowErrorMessage(body: ApiErrorBody & { detail?: unknown; error?: unknown }) {
  const code = typeof body.code === "string" ? body.code : "";
  if (code === "UNIFIED_CODING_WORKFLOW_DISABLED") return `Workflow disabled. ${ENABLE_MESSAGE}`;
  if (code === "FAKE_API_CODING_AGENT_DISABLED") return `Fake provider disabled. ${ENABLE_MESSAGE}`;
  if (code === "REAL_API_CODING_AGENT_DISABLED") return `Real API coding agent is disabled. ${REAL_API_ENABLE_MESSAGE}`;
  if (code === "REAL_API_CODING_AGENT_CONFIRMATION_REQUIRED") return "Confirm live Real API usage before generating a review.";
  if (code === "REAL_API_CODING_AGENT_UNAVAILABLE") return "No healthy model provider is available. Configure a provider in Model Settings.";
  if (code === "API_CODING_MODEL_OUTPUT_INVALID") return "The model did not return valid ForgeX coding-agent JSON. Try a different model, simplify the request, or use project_summary context.";
  if (code === "API_CODING_AGENT_CONTRACT_INVALID") return "The model did not return valid ForgeX coding-agent JSON. Try a different model, simplify the request, or use project_summary context.";
  if (code === "CODING_PROVIDER_UNSAFE_OUTPUT") return "The generated proposal failed ForgeX safety validation.";
  if (code === "CODING_PROVIDER_OVERSIZED_OUTPUT") return "The generated proposal exceeded ForgeX size limits.";
  if (code === "API_CODING_CONTEXT_INVALID") return "The request context is empty or invalid. Select files or use project summary.";
  if (code === "API_CODING_CONTEXT_EMPTY") return "The request context is empty or invalid. Select files or use project summary.";
  if (code === "CODING_WORKFLOW_CONTEXT_MODE_INVALID") return "Invalid context mode.";
  if (code === "CODING_WORKFLOW_WORKSPACE_INVALID") return "Invalid context path.";
  if (code === "CODING_WORKFLOW_WORKSPACE_REQUIRED") return "Open or select a workspace before previewing context.";
  if (code === "API_CODING_MODEL_CALL_FAILED") return "The model provider request failed safely. Check Model Settings and try again.";
  if (code === "CODING_WORKFLOW_REPAIR_DISABLED") return "Repair loop disabled. Enable FORGEX_ENABLE_CODING_AGENT_REPAIR_LOOP=1.";
  if (code === "CODING_WORKFLOW_REPAIR_NOT_ALLOWED") return "Repair is only available after build failure.";
  if (code === "CODING_WORKFLOW_REPAIR_REQUIRES_BUILD_FAILURE") return "Repair is only available after build failure.";
  if (code === "CODING_WORKFLOW_REPAIR_LIMIT_EXCEEDED") return "Repair attempt limit exceeded.";
  if (code === "CODING_WORKFLOW_PATCH_APPLY_DISABLED") return "Patch apply is disabled. Enable FORGEX_ENABLE_PATCH_APPLY=1 and restart the backend.";
  if (code === "CODING_WORKFLOW_ROLLBACK_RESTORE_DISABLED") return "Rollback restore is disabled. Enable FORGEX_ENABLE_ROLLBACK_RESTORE=1 and restart the backend.";
  if (code === "CODING_WORKFLOW_OPERATION_IN_PROGRESS") return "A workflow operation is already in progress. Refreshing run state.";
  if (code === "CODING_WORKFLOW_CANCEL_CONFIRMATION_REQUIRED") return "Cancellation confirmation required.";
  if (code === "CODING_WORKFLOW_CANCEL_NOT_ALLOWED") return "This workflow cannot be cancelled from its current state.";
  if (code === "CODING_WORKFLOW_RECOVERY_CONFIRMATION_REQUIRED") return "Recovery confirmation required.";
  if (code === "CODING_WORKFLOW_RECOVERY_NOT_ALLOWED") return "Only stale in-progress workflows can be manually marked failed.";
  if (code === "CODING_WORKFLOW_LOCK_CLEAR_CONFIRMATION_REQUIRED") return "Stale lock clear confirmation required.";
  if (code === "CODING_WORKFLOW_LOCK_CLEAR_NOT_ALLOWED") return "Only stale workflow locks can be manually cleared.";
  if (code.includes("APPROVAL_REQUIRED")) return "Approval confirmation required.";
  if (code.includes("CONFIRMATION_REQUIRED")) return "Explicit confirmation required for this workflow stage.";
  if (code.includes("NOT_AWAITING")) return "Wrong workflow stage for this action.";
  if (code.includes("NOT_FOUND")) return "Coding workflow run was not found.";
  if (code.includes("BUILD_FAILED")) return "Build failed safely.";
  if (code.includes("FLASH_FAILED")) return "Flash failed safely.";
  if (code.includes("MONITOR_FAILED")) return "Monitor failed safely.";
  return stringField(body.message) ?? stringField(body.detail) ?? stringField(body.error) ?? toErrorMessage(body, "");
}

function stringField(value: unknown) {
  return typeof value === "string" && value.trim() ? value : null;
}
