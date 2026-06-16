import type {
  ApiErrorBody,
  BuildHistoryResponse,
  ExecuteResponse,
  FileContentResponse,
  HealthResponse,
  ProjectFilesResponse,
  ProjectListResponse,
  WorkspaceLogsResponse,
} from "@/types";

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
    let body: ApiErrorBody = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // The server may be offline or return a non-JSON proxy response.
    }
    throw new PromptForgeApiError(
      body.message ?? `PromptForge API request failed (${response.status})`,
      response.status,
      body.code,
    );
  }

  return (await response.json()) as T;
}

export const promptForgeApi = {
  health: () => request<HealthResponse>("/health"),
  projects: () => request<ProjectListResponse>("/projects"),
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
  logs: (executionId?: string) =>
    request<WorkspaceLogsResponse>(
      executionId ? `/logs?execution_id=${encodeURIComponent(executionId)}` : "/logs",
    ),
  execute: (prompt: string, taskId: string, signal?: AbortSignal) =>
    request<ExecuteResponse>("/execute", {
      method: "POST",
      body: JSON.stringify({ prompt, task_id: taskId }),
      signal,
    }),
};
