import type {
  PlatformIOEnvironment,
  ProjectResponse,
  WorkspaceFile,
  WorkflowStage,
} from "@/types";

export const EMPTY_FILE: WorkspaceFile = {
  path: "workspace",
  language: "text",
  content: "",
};

export const LAST_PROJECT_KEY = "forgex.workspace.lastProjectId";
export const LAST_WORKSPACE_PATH_KEY = "forgex.workspace.lastPath";

export const initialStages: WorkflowStage[] = [
  {
    key: "planning",
    label: "Planning",
    description: "Planner translating the prompt into an executable firmware task.",
    status: "pending",
  },
  {
    key: "generation",
    label: "Generation",
    description: "Firmware sources and PlatformIO metadata are produced.",
    status: "pending",
  },
  {
    key: "build",
    label: "Build",
    description: "PlatformIO compilation, dependency resolution, and artifact capture.",
    status: "pending",
  },
  {
    key: "flash",
    label: "Flash",
    description: "Board detection, validation, and firmware upload.",
    status: "pending",
  },
  {
    key: "monitor",
    label: "Monitor",
    description: "Serial monitor session for runtime telemetry.",
    status: "pending",
  },
];

export function languageFor(path: string, fileType?: string | null) {
  const type = fileType?.toLowerCase();
  const normalized = path.toLowerCase();
  if (
    type === "cpp" ||
    type === "c" ||
    type === "h" ||
    type === "hpp" ||
    normalized.endsWith(".cpp") ||
    normalized.endsWith(".c") ||
    normalized.endsWith(".h") ||
    normalized.endsWith(".hpp") ||
    normalized.endsWith(".ino")
  ) {
    return "cpp";
  }
  if (type === "ini" || normalized.endsWith(".ini")) {
    return "ini";
  }
  if (type === "md" || normalized.endsWith(".md")) {
    return "markdown";
  }
  if (type === "json" || normalized.endsWith(".json")) {
    return "json";
  }
  return "text";
}

export function latestProject(projects: ProjectResponse[]) {
  return [...projects].sort(
    (left, right) =>
      Date.parse(right.updated_at || right.created_at) - Date.parse(left.updated_at || left.created_at),
  )[0] ?? null;
}

export function tabStorageKey(projectId?: string) {
  return projectId ? `promptforge.tabs.${projectId}` : "promptforge.tabs";
}

export function toFile(projectId: string, path: string, content: string, fileType?: string | null, revision?: string | null): WorkspaceFile {
  return {
    projectId,
    path,
    content,
    fileType: fileType ?? undefined,
    language: languageFor(path, fileType),
    revision,
  };
}

export function platformioEnvironments(project: ProjectResponse | null): PlatformIOEnvironment[] {
  const platformio = project?.metadata?.platformio;
  if (!platformio || typeof platformio !== "object" || !("environments" in platformio)) {
    return [];
  }
  const environments = (platformio as { environments?: unknown }).environments;
  return Array.isArray(environments) ? (environments as PlatformIOEnvironment[]) : [];
}

export function activeEnvironment(project: ProjectResponse | null): PlatformIOEnvironment | null {
  const environments = platformioEnvironments(project);
  if (environments.length === 0) return null;
  const selected = typeof project?.metadata?.active_environment === "string" ? project.metadata.active_environment : null;
  return environments.find((environment) => environment.name === selected) ?? environments.find((environment) => environment.board) ?? environments[0] ?? null;
}

export function activeEnvironmentName(project: ProjectResponse | null) {
  return activeEnvironment(project)?.name ?? null;
}

export function projectBoardId(project: ProjectResponse | null) {
  const environmentBoard = activeEnvironment(project)?.board;
  if (environmentBoard) return environmentBoard;
  const metadataBoard = project?.metadata?.board;
  if (typeof metadataBoard === "string" && metadataBoard && metadataBoard !== "UNKNOWN") return metadataBoard;
  return project?.target_board && project.target_board !== "UNKNOWN" ? project.target_board : null;
}

export function hasPlatformIO(project: ProjectResponse | null) {
  if (!project) return false;
  if (project.metadata?.has_platformio_ini === false) return false;
  if (project.project_type === "generic") return false;
  return true;
}

export function baudFromProject(project: ProjectResponse | null, field: "monitor_speed" | "upload_speed", fallback: number) {
  const environment = activeEnvironment(project);
  const value = environment?.[field];
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback;
}

export function validateWorkspaceRelativePath(value: string) {
  const path = value.trim().replaceAll("\\", "/");
  if (!path) return "Enter a path.";
  if (path.startsWith("/") || /^[a-zA-Z]:\//.test(path)) return "Use a path inside the active project.";
  const parts = path.split("/").filter(Boolean);
  if (parts.length === 0) return "Enter a path.";
  if (parts.some((part) => part === "..")) return "Parent directory segments are not allowed.";
  if (parts.some((part) => part === ".")) return "Current directory segments are not allowed.";
  return null;
}

export function normalizeWorkspaceRelativePath(value: string) {
  return value.trim().replaceAll("\\", "/").replace(/^\/+/, "");
}
