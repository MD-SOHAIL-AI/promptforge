"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { promptForgeApi } from "@/lib/api";
import { toErrorMessage } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useForgeXDialogs } from "@/components/ide/dialogs/forgex-dialog-provider";
import { useSerialMonitor } from "@/hooks/use-serial-monitor";
import {
  applyStageEvent,
  cancelActiveStages,
  isTerminalEvent,
  settleActiveStages,
  terminalStatus,
  workflowCancelled,
  workflowFailed,
} from "@/lib/workflow-stage-state";
import { createExecutionSocket } from "@/lib/websocket";
import type {
  BuildHistoryItem,
  BuildResponse,
  ConsoleEntry,
  DetectedBoard,
  EditorTab,
  ExecuteResponse,
  FlashResponse,
  ExecutionEvent,
  GenerationFileProgress,
  GenerationProgress,
  HealthResponse,
  MonitorStatusResponse,
  PlatformIOEnvironment,
  ProjectListResponse,
  ProjectResponse,
  WorkspaceEntry,
  WorkspaceFile,
  WorkspaceLog,
  WorkflowStage,
} from "@/types";

const EMPTY_FILE: WorkspaceFile = {
  path: "workspace",
  language: "text",
  content: "",
};

const LAST_PROJECT_KEY = "forgex.workspace.lastProjectId";
const LAST_WORKSPACE_PATH_KEY = "forgex.workspace.lastPath";

const initialStages: WorkflowStage[] = [
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

export type WorkspaceView = "workspace" | "projects" | "builds" | "logs";
export type GenerationMode = "new_project" | "modify_existing_project" | "generate_into_open_folder";

export interface ExecuteOptions {
  selectedBoard?: string | null;
  selectedFramework?: string | null;
  generationMode?: GenerationMode;
}

function languageFor(path: string, fileType?: string | null) {
  const type = fileType?.toLowerCase();
  if (type === "cpp" || type === "c" || type === "h" || type === "hpp" || path.endsWith(".ino")) {
    return "cpp";
  }
  if (type === "ini" || path.endsWith(".ini")) {
    return "ini";
  }
  if (type === "md" || path.endsWith(".md")) {
    return "markdown";
  }
  if (type === "json" || path.endsWith(".json")) {
    return "json";
  }
  return "text";
}

function consoleChannel(event: ExecutionEvent): ConsoleEntry["channel"] {
  if (event.event.endsWith("_FAILED") || event.payload.success === false) return "error";
  if (event.event.startsWith("BUILD")) return "build";
  if (event.event.startsWith("MONITOR")) return "serial";
  return "workflow";
}

function isLiveGenerationEvent(event: ExecutionEvent) {
  return (
    event.event.startsWith("GENERATION_") ||
    event.event.startsWith("REQUIREMENTS_") ||
    event.event.startsWith("MANIFEST_") ||
    event.event.startsWith("FILE_")
  );
}

function eventMessage(event: ExecutionEvent) {
  if (isLiveGenerationEvent(event)) {
    const message = typeof event.payload.message === "string" ? event.payload.message : event.event.replaceAll("_", " ");
    return `[generation] ${message}`;
  }
  const base = event.event.replaceAll("_", " ");
  const status = typeof event.payload.status === "string" ? ` ${event.payload.status}` : "";
  const failure = event.payload.failure;
  const failureRecord = failure && typeof failure === "object" ? (failure as Record<string, unknown>) : null;
  const failureMessage =
    failureRecord && typeof failureRecord.message === "string"
      ? failureRecord.message
      : null;
  const payloadMessage = typeof event.payload.message === "string" ? event.payload.message : null;
  const detail = failureMessage ?? payloadMessage;
  const command = Array.isArray(event.payload.command)
    ? event.payload.command.filter((item): item is string => typeof item === "string").join(" ")
    : "";
  const source = [event.payload.provider_id, event.payload.model_id]
    .filter((item): item is string => typeof item === "string" && item.length > 0)
    .join(" / ");
  const context = [command ? `command: ${command}` : "", source ? `source: ${source}` : ""].filter(Boolean).join("; ");
  const message = detail ? `${base}${status}: ${detail}` : `${base}${status}`;
  return context ? `${message} (${context})` : message;
}

function mergeFileStatus(
  current: GenerationFileProgress[],
  patch: GenerationFileProgress,
): GenerationFileProgress[] {
  const index = current.findIndex((item) => item.path === patch.path);
  if (index < 0) return [...current, patch];
  return current.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item));
}

function liveGenerationProgressFromEvent(
  current: GenerationProgress | null,
  event: ExecutionEvent,
): GenerationProgress | null {
  if (!isLiveGenerationEvent(event)) return current;
  const filePath = typeof event.payload.file_path === "string" ? event.payload.file_path : null;
  const provider = typeof event.payload.provider_id === "string" ? event.payload.provider_id : current?.provider_id ?? null;
  const model = typeof event.payload.model_id === "string" ? event.payload.model_id : current?.model_id ?? null;
  const filesTotal = typeof event.payload.files_total === "number" ? event.payload.files_total : current?.files_total ?? 0;
  const filePaths = Array.isArray(event.payload.file_paths)
    ? event.payload.file_paths.filter((value): value is string => typeof value === "string")
    : [];
  const base: GenerationProgress = current ?? {
    mode: typeof event.payload.generation_mode === "string" ? event.payload.generation_mode : "chunked",
    status: "running",
    provider_id: provider,
    model_id: model,
    current_file: null,
    files_total: filesTotal || filePaths.length,
    files_written: 0,
    repairs: 0,
    fallbacks: 0,
    warnings: [],
    failed_files: [],
    pending_files: filePaths,
    file_statuses: filePaths.map((path) => ({ path, status: "pending" })),
  };

  let next: GenerationProgress = {
    ...base,
    mode: typeof event.payload.generation_mode === "string" ? event.payload.generation_mode : base.mode,
    provider_id: provider,
    model_id: model,
    files_total: filesTotal || base.files_total || filePaths.length,
    status: event.event === "GENERATION_INCOMPLETE" ? "incomplete" : event.event === "GENERATION_FAILED" ? "failed" : event.event === "GENERATION_COMPLETED" ? "success" : "running",
  };

  if (event.event === "MANIFEST_CREATED" && filePaths.length > 0) {
    next = {
      ...next,
      files_total: filePaths.length,
      pending_files: filePaths,
      file_statuses: filePaths.map((path) => ({ path, status: "pending" })),
    };
  }

  if (filePath) {
    const fileStatus =
      event.event === "FILE_WRITTEN"
        ? "written"
        : event.event === "FILE_FAILED"
          ? "failed"
          : event.event === "FILE_GENERATION_VALIDATED"
            ? "validated"
            : event.event === "FILE_GENERATION_REPAIR_STARTED"
              ? "repairing"
              : event.event === "FILE_GENERATION_FALLBACK_STARTED"
                ? "fallback"
                : "generating";
    next = {
      ...next,
      current_file: fileStatus === "written" ? next.pending_files.find((path) => path !== filePath) ?? null : filePath,
      file_statuses: mergeFileStatus(next.file_statuses, {
        path: filePath,
        status: fileStatus,
        provider_id: provider,
        model_id: model,
        repair_used: event.payload.repair_used === true,
        fallback_used: event.payload.fallback_used === true,
        attempt_count: typeof event.payload.attempt_number === "number" ? event.payload.attempt_number : undefined,
        bytes_written: typeof event.payload.bytes_written === "number" ? event.payload.bytes_written : undefined,
        errors: Array.isArray(event.payload.errors) ? event.payload.errors.filter((value): value is string => typeof value === "string") : undefined,
      }),
    };
  }

  const failedFiles = Array.isArray(event.payload.failed_files)
    ? event.payload.failed_files.filter((value): value is string => typeof value === "string")
    : next.file_statuses.filter((item) => item.status === "failed").map((item) => item.path);
  const pendingFiles = Array.isArray(event.payload.pending_files)
    ? event.payload.pending_files.filter((value): value is string => typeof value === "string")
    : next.file_statuses.filter((item) => item.status !== "written" && item.status !== "failed").map((item) => item.path);
  const filesWritten = next.file_statuses.filter((item) => item.status === "written").length;
  return {
    ...next,
    files_written: filesWritten,
    repairs: next.file_statuses.filter((item) => item.repair_used).length,
    fallbacks: next.file_statuses.filter((item) => item.fallback_used).length,
    failed_files: failedFiles,
    pending_files: pendingFiles,
    current_file: event.event === "FILE_WRITTEN" ? pendingFiles[0] ?? null : next.current_file,
  };
}

function generationReportMessages(event: ExecutionEvent): string[] {
  if (!event.event.startsWith("CODE_GENERATION")) return [];
  const report = event.payload.generation_report;
  if (!report || typeof report !== "object") return [];
  const data = report as Record<string, unknown>;
  const messages: string[] = [];
  const provider = typeof data.provider_id === "string" ? data.provider_id : event.payload.provider_id;
  const model = typeof data.model_id === "string" ? data.model_id : event.payload.model_id;
  const attempts = typeof data.attempt_count === "number" ? data.attempt_count : event.payload.attempt_count;
  const repair = data.repair_used === true || event.payload.repair_used === true;
  const fallback = data.fallback_used === true || event.payload.fallback_used === true;
  if (provider || model || attempts) {
    messages.push(
      `Generation report: ${provider || "provider unknown"} / ${model || "model unknown"}; attempts: ${attempts || 0}; repair: ${repair ? "yes" : "no"}; fallback: ${fallback ? "yes" : "no"}`,
    );
  }
  const mode = typeof data.generation_mode === "string" ? data.generation_mode : undefined;
  const chunked = data.chunked_generation && typeof data.chunked_generation === "object"
    ? (data.chunked_generation as Record<string, unknown>)
    : null;
  if (mode === "chunked" || chunked) {
    const statuses = Array.isArray(chunked?.file_statuses) ? chunked.file_statuses : [];
    const written = statuses.filter((item) => item && typeof item === "object" && (item as Record<string, unknown>).status === "written").length;
    messages.push(`Generation mode: Chunked; files generated: ${written}/${statuses.length}`);
    for (const status of statuses.slice(-4)) {
      if (!status || typeof status !== "object") continue;
      const item = status as Record<string, unknown>;
      const path = typeof item.path === "string" ? item.path : "file";
      const state = typeof item.status === "string" ? item.status : "updated";
      const fileFallback = item.fallback_used === true ? " via fallback" : "";
      const fileRepair = item.repair_used === true ? " after repair" : "";
      messages.push(`${path} ${state}${fileRepair}${fileFallback}`);
    }
  }
  const warnings = Array.isArray(data.warnings) ? data.warnings : event.payload.warnings;
  if (Array.isArray(warnings)) {
    for (const warning of warnings) {
      if (typeof warning === "string" && warning.trim()) {
        messages.push(`Generation warning: ${warning}`);
      }
    }
  }
  const attemptRecords = Array.isArray(data.attempts) ? data.attempts : [];
  for (const attempt of attemptRecords.slice(-3)) {
    if (!attempt || typeof attempt !== "object") continue;
    const item = attempt as Record<string, unknown>;
    const type = typeof item.attempt_type === "string" ? item.attempt_type : "attempt";
    const success = item.success === true ? "succeeded" : "failed";
    const error = typeof item.error_message === "string" && item.error_message ? `: ${item.error_message}` : "";
    messages.push(`Generation ${type} ${success}${error}`);
  }
  return messages;
}

function generationProgressFromEvent(event: ExecutionEvent): GenerationProgress | null {
  if (!event.event.startsWith("CODE_GENERATION")) return null;
  const report = event.payload.generation_report;
  if (!report || typeof report !== "object") return null;
  const data = report as Record<string, unknown>;
  const chunked = data.chunked_generation && typeof data.chunked_generation === "object"
    ? (data.chunked_generation as Record<string, unknown>)
    : null;
  const artifact = event.payload.artifact_summary && typeof event.payload.artifact_summary === "object"
    ? (event.payload.artifact_summary as Record<string, unknown>)
    : null;
  const mode = typeof data.generation_mode === "string" ? data.generation_mode : chunked ? "chunked" : "one_shot";
  const rawStatuses = Array.isArray(chunked?.file_statuses) ? chunked.file_statuses : [];
  let fileStatuses: GenerationFileProgress[] = rawStatuses
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => ({
      path: typeof item.path === "string" ? item.path : "file",
      status: typeof item.status === "string" ? item.status : "pending",
      provider_id: typeof item.provider_id === "string" ? item.provider_id : null,
      model_id: typeof item.model_id === "string" ? item.model_id : null,
      repair_used: item.repair_used === true,
      fallback_used: item.fallback_used === true,
      attempt_count: typeof item.attempt_count === "number" ? item.attempt_count : undefined,
      bytes: typeof item.bytes === "number" ? item.bytes : undefined,
      bytes_written: typeof item.bytes_written === "number" ? item.bytes_written : undefined,
      previous_hash: typeof item.previous_hash === "string" ? item.previous_hash : null,
      new_hash: typeof item.new_hash === "string" ? item.new_hash : null,
      errors: Array.isArray(item.errors) ? item.errors.filter((value): value is string => typeof value === "string") : undefined,
      warnings: Array.isArray(item.warnings) ? item.warnings.filter((value): value is string => typeof value === "string") : undefined,
    }));
  if (fileStatuses.length === 0 && artifact) {
    const artifactPaths = [
      ...(Array.isArray(artifact.created_files) ? artifact.created_files : []),
      ...(Array.isArray(artifact.updated_files) ? artifact.updated_files : []),
      ...(Array.isArray(artifact.unchanged_files) ? artifact.unchanged_files : []),
    ].filter((value): value is string => typeof value === "string");
    fileStatuses = [...new Set(artifactPaths)].map((path) => ({ path, status: "written" }));
  }
  const failedFiles = Array.isArray(chunked?.failed_files) ? chunked.failed_files.filter((value): value is string => typeof value === "string") : [];
  const pendingFiles = Array.isArray(chunked?.pending_files) ? chunked.pending_files.filter((value): value is string => typeof value === "string") : [];
  const warnings = Array.isArray(data.warnings) ? data.warnings.filter((value): value is string => typeof value === "string") : [];
  const filesWritten = fileStatuses.filter((item) => item.status === "written").length;
  const current = fileStatuses.find((item) => item.status !== "written" && item.status !== "failed")?.path ?? pendingFiles[0] ?? null;
  const status = event.event === "CODE_GENERATION_FAILED"
    ? "failed"
    : failedFiles.length > 0 || pendingFiles.length > 0
      ? "incomplete"
      : event.event === "CODE_GENERATION_COMPLETED"
        ? "success"
        : "running";
  return {
    mode,
    status,
    provider_id: typeof data.provider_id === "string" ? data.provider_id : typeof event.payload.provider_id === "string" ? event.payload.provider_id : null,
    model_id: typeof data.model_id === "string" ? data.model_id : typeof event.payload.model_id === "string" ? event.payload.model_id : null,
    content_verified: artifact?.content_verified === true,
    no_op: artifact?.no_op === true,
    current_file: current,
    files_total: fileStatuses.length,
    files_written: filesWritten,
    repairs: fileStatuses.filter((item) => item.repair_used).length,
    fallbacks: fileStatuses.filter((item) => item.fallback_used).length,
    warnings,
    failed_files: failedFiles,
    pending_files: pendingFiles,
    file_statuses: fileStatuses,
  };
}

function latestProject(projects: ProjectResponse[]) {
  return [...projects].sort(
    (left, right) =>
      Date.parse(right.updated_at || right.created_at) - Date.parse(left.updated_at || left.created_at),
  )[0] ?? null;
}

function tabStorageKey(projectId?: string) {
  return projectId ? `promptforge.tabs.${projectId}` : "promptforge.tabs";
}

function toFile(projectId: string, path: string, content: string, fileType?: string | null): WorkspaceFile {
  return {
    projectId,
    path,
    content,
    fileType: fileType ?? undefined,
    language: languageFor(path, fileType),
  };
}

function platformioEnvironments(project: ProjectResponse | null): PlatformIOEnvironment[] {
  const platformio = project?.metadata?.platformio;
  if (!platformio || typeof platformio !== "object" || !("environments" in platformio)) {
    return [];
  }
  const environments = (platformio as { environments?: unknown }).environments;
  return Array.isArray(environments) ? (environments as PlatformIOEnvironment[]) : [];
}

function activeEnvironment(project: ProjectResponse | null): PlatformIOEnvironment | null {
  const environments = platformioEnvironments(project);
  if (environments.length === 0) return null;
  const selected = typeof project?.metadata?.active_environment === "string" ? project.metadata.active_environment : null;
  return environments.find((environment) => environment.name === selected) ?? environments.find((environment) => environment.board) ?? environments[0] ?? null;
}

function activeEnvironmentName(project: ProjectResponse | null) {
  return activeEnvironment(project)?.name ?? null;
}

function projectBoardId(project: ProjectResponse | null) {
  const environmentBoard = activeEnvironment(project)?.board;
  if (environmentBoard) return environmentBoard;
  const metadataBoard = project?.metadata?.board;
  if (typeof metadataBoard === "string" && metadataBoard && metadataBoard !== "UNKNOWN") return metadataBoard;
  return project?.target_board && project.target_board !== "UNKNOWN" ? project.target_board : null;
}

function hasPlatformIO(project: ProjectResponse | null) {
  if (!project) return false;
  if (project.metadata?.has_platformio_ini === false) return false;
  if (project.project_type === "generic") return false;
  return true;
}

function baudFromProject(project: ProjectResponse | null, field: "monitor_speed" | "upload_speed", fallback: number) {
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

function normalizeWorkspaceRelativePath(value: string) {
  return value.trim().replaceAll("\\", "/").replace(/^\/+/, "");
}

export function usePromptForgeWorkspace() {
  const dialogs = useForgeXDialogs();
  const serialMonitor = useSerialMonitor();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [projects, setProjects] = useState<ProjectResponse[]>([]);
  const [activeProject, setActiveProject] = useState<ProjectResponse | null>(null);
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [recentlyAddedPaths, setRecentlyAddedPaths] = useState<string[]>([]);
  const [stages, setStages] = useState<WorkflowStage[]>(initialStages);
  const [tabs, setTabs] = useState<EditorTab[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [logs, setLogs] = useState<ConsoleEntry[]>([]);
  const [workspaceLogs, setWorkspaceLogs] = useState<WorkspaceLog[]>([]);
  const [buildHistory, setBuildHistory] = useState<BuildHistoryItem[]>([]);
  const [detectedBoards, setDetectedBoards] = useState<DetectedBoard[]>([]);
  const [monitorStatus, setMonitorStatus] = useState<MonitorStatusResponse | null>(null);
  const [toolAction, setToolAction] = useState<"build" | "flash" | "monitor" | null>(null);
  const [desktopWorkspacePath, setDesktopWorkspacePathState] = useState<string | null>(null);
  const [lastBuildResult, setLastBuildResult] = useState<BuildResponse | null>(null);
  const [lastFlashResult, setLastFlashResult] = useState<FlashResponse | null>(null);
  const [activeView, setActiveView] = useState<WorkspaceView>("workspace");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [socketState, setSocketState] = useState<"idle" | "connecting" | "reconnecting" | "open" | "closed">("idle");
  const [result, setResult] = useState<ExecuteResponse | null>(null);
  const [isExecuting, setIsExecuting] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [generationProgress, setGenerationProgress] = useState<GenerationProgress | null>(null);
  const restoredProjectRef = useRef<string | null>(null);
  const closeExecutionSocketRef = useRef<(() => void) | null>(null);
  const entriesRef = useRef<WorkspaceEntry[]>([]);
  const entriesProjectRef = useRef<string | null>(null);
  const activeProjectIdRef = useRef<string | null>(null);
  const projectLoadSequenceRef = useRef(0);

  const selectedFile = useMemo(
    () => tabs.find((tab) => tab.path === activePath) ?? tabs[0] ?? EMPTY_FILE,
    [activePath, tabs],
  );

  useEffect(() => {
    activeProjectIdRef.current = activeProject?.project_id ?? null;
  }, [activeProject?.project_id]);

  const files = useMemo<WorkspaceFile[]>(
    () =>
      entries
        .filter((entry) => entry.kind === "file")
        .map((entry) => ({
          projectId: activeProject?.project_id,
          path: entry.path,
          content: "",
          fileType: entry.file_type ?? undefined,
          language: languageFor(entry.path, entry.file_type),
        })),
    [activeProject?.project_id, entries],
  );

  const addLog = useCallback((entry: Omit<ConsoleEntry, "id" | "timestamp">) => {
    setLogs((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        timestamp: new Date().toISOString(),
        ...entry,
      },
    ]);
  }, []);

  const reportError = useCallback(
    (err: unknown, fallback: string) => {
      const message = toErrorMessage(err, fallback);
      setError(message);
      addLog({ channel: "error", message });
      return message;
    },
    [addLog],
  );

  const loadProjectFiles = useCallback(async (projectId: string) => {
    const sequence = ++projectLoadSequenceRef.current;
    try {
      const response = await promptForgeApi.projectFiles(projectId);
      if (sequence !== projectLoadSequenceRef.current) return [];
      if (activeProjectIdRef.current && activeProjectIdRef.current !== projectId) return [];
      const previousPaths = new Set(entriesRef.current.map((entry) => entry.path));
      const addedPaths = entriesProjectRef.current === projectId
        ? response.entries.filter((entry) => !previousPaths.has(entry.path)).map((entry) => entry.path)
        : [];
      entriesRef.current = response.entries;
      entriesProjectRef.current = projectId;
      setEntries(response.entries);
      if (addedPaths.length > 0) setRecentlyAddedPaths(addedPaths);
      return response.entries;
    } catch (err) {
      if (sequence !== projectLoadSequenceRef.current) return [];
      reportError(err, "Project files could not be loaded");
      return [];
    }
  }, [reportError]);

  useEffect(() => {
    if (recentlyAddedPaths.length === 0) return;
    const timeout = window.setTimeout(() => setRecentlyAddedPaths([]), 2_400);
    return () => window.clearTimeout(timeout);
  }, [recentlyAddedPaths]);

  const refreshBuildHistory = useCallback(async () => {
    try {
      const response = await promptForgeApi.buildHistory();
      setBuildHistory(response.builds);
    } catch (err) {
      reportError(err, "Build history could not be loaded");
    }
  }, [reportError]);

  const refreshWorkspaceLogs = useCallback(async (selectedExecutionId?: string) => {
    try {
      const response = await promptForgeApi.logs(selectedExecutionId);
      setWorkspaceLogs(response.logs);
      setLogs((current) => {
        const retained = current.filter((entry) => !entry.id.startsWith("workspace-log-"));
        const loaded = response.logs.map<ConsoleEntry>((item) => ({
          id: `workspace-log-${item.execution_id}-${item.log_type}-${item.path}`,
          timestamp: new Date().toISOString(),
          channel:
            item.log_type === "build"
              ? "build"
              : item.log_type === "monitor"
                ? "serial"
                : item.log_type === "flash"
                  ? "system"
                  : "workflow",
          message: `[${item.execution_id}] ${item.content.trim() || `${item.log_type} log`}`,
        }));
        return [...retained, ...loaded];
      });
    } catch (err) {
      reportError(err, "Workspace logs could not be loaded");
    }
  }, [reportError]);

  const refreshDetectedBoards = useCallback(async () => {
    try {
      const response = await promptForgeApi.detectedBoards();
      setDetectedBoards(response.boards);
      return response.boards;
    } catch (err) {
      reportError(err, "Board detection failed");
      return [];
    }
  }, [reportError]);

  const refreshMonitorStatus = useCallback(async () => {
    try {
      const response = await promptForgeApi.monitorStatus();
      setMonitorStatus(response);
      return response;
    } catch (err) {
      reportError(err, "Monitor status is unavailable");
      return null;
    }
  }, [reportError]);

  useEffect(() => {
    const latest = serialMonitor.events.at(-1);
    if (!latest || (latest.source !== "RECONNECT" && latest.source !== "RUNTIME")) return;
    void refreshMonitorStatus();
  }, [refreshMonitorStatus, serialMonitor.events]);

  const refreshProjects = useCallback(async (preferredProjectId?: string | null) => {
    let response: ProjectListResponse;
    try {
      response = await promptForgeApi.projects();
    } catch (err) {
      reportError(err, "Projects could not be loaded");
      return [];
    }
    setProjects(response.projects);
    setActiveProject((current) => {
      const rememberedProjectId =
        preferredProjectId ?? window.localStorage.getItem(LAST_PROJECT_KEY);
      if (rememberedProjectId) {
        const preferred = response.projects.find((project) => project.project_id === rememberedProjectId);
        if (preferred) {
          activeProjectIdRef.current = preferred.project_id;
          return preferred;
        }
      }
      if (current) {
        const selected = response.projects.find((project) => project.project_id === current.project_id) ?? response.projects[0] ?? null;
        activeProjectIdRef.current = selected?.project_id ?? null;
        return selected;
      }
      const selected = response.projects[0] ?? null;
      activeProjectIdRef.current = selected?.project_id ?? null;
      return selected;
    });
    return response.projects;
  }, [reportError]);

  const openFile = useCallback(
    async (entryOrPath: WorkspaceEntry | string) => {
      if (!activeProject) return;
      const path = typeof entryOrPath === "string" ? entryOrPath : entryOrPath.path;
      const kind = typeof entryOrPath === "string" ? "file" : entryOrPath.kind;
      const fileType = typeof entryOrPath === "string" ? undefined : entryOrPath.file_type;
      if (kind !== "file") return;
      const existing = tabs.find((tab) => tab.path === path);
      if (existing) {
        setActivePath(path);
        return;
      }
      const placeholder = toFile(activeProject.project_id, path, "", fileType);
      setTabs((current) => [...current, { ...placeholder, dirty: false }]);
      setActivePath(path);
      setError(null);
      try {
        const projectId = activeProject.project_id;
        const response = await promptForgeApi.fileContent(activeProject.project_id, path);
        if (activeProjectIdRef.current !== projectId) return;
        const file = toFile(response.project_id, response.path, response.content, response.file_type);
        setTabs((current) =>
          current.map((tab) => (tab.path === path ? { ...file, dirty: false } : tab)),
        );
        setActivePath(file.path);
      } catch (err) {
        const cached = activeProject.files.find((file) => file.path === path);
        if (cached) {
          const file = toFile(activeProject.project_id, cached.path, cached.content, cached.file_type);
          setTabs((current) =>
            current.map((tab) => (tab.path === path ? { ...file, dirty: false } : tab)),
          );
          setActivePath(file.path);
          return;
        }
        const message = reportError(err, `File could not be opened: ${path}`);
        setTabs((current) => current.filter((tab) => tab.path !== path));
        setActivePath((current) => (current === path ? null : current));
        return message;
      }
    },
    [activeProject, reportError, tabs],
  );

  const updateTabContent = useCallback((path: string, content: string) => {
    setTabs((current) =>
      current.map((tab) => (tab.path === path ? { ...tab, content, dirty: true } : tab)),
    );
  }, []);

  const saveFile = useCallback(
    async (path: string) => {
      if (!activeProject) return false;
      const tab = tabs.find((item) => item.path === path);
      if (!tab) return false;
      try {
        const response = await promptForgeApi.updateFile({
          project_id: activeProject.project_id,
          path,
          content: tab.content,
          file_type: tab.fileType,
        });
        if ("content" in response) {
          setTabs((current) =>
            current.map((item) =>
              item.path === path
                ? { ...toFile(response.project_id, response.path, response.content, response.file_type), dirty: false }
                : item,
            ),
          );
          setActivePath(response.path);
        }
        await loadProjectFiles(activeProject.project_id);
        await refreshProjects(activeProject.project_id);
        return true;
      } catch (err) {
        const message = reportError(err, `File could not be saved: ${path}`);
        notify.error(message);
        return false;
      }
    },
    [activeProject, loadProjectFiles, refreshProjects, reportError, tabs],
  );

  const removeTab = useCallback((path: string) => {
    setTabs((current) => {
      const next = current.filter((tab) => tab.path !== path);
      setActivePath((selected) => {
        if (selected !== path) return selected;
        return next.at(-1)?.path ?? null;
      });
      return next;
    });
  }, []);

  const closeTab = useCallback(
    (path: string) => {
      const tab = tabs.find((item) => item.path === path);
      if (!tab?.dirty) {
        removeTab(path);
        return;
      }
      void (async () => {
        const wantsSave = await dialogs.confirmAction({
          title: "Save changes before closing?",
          description: `"${path}" has unsaved changes.`,
          confirmText: "Save",
          cancelText: "Don't save",
        });
        if (wantsSave) {
          const saved = await saveFile(path);
          if (!saved) {
            notify.error(`Kept "${path}" open because saving failed.`);
            return;
          }
          removeTab(path);
          return;
        }
        const discardApproved = await dialogs.confirmAction({
          title: "Discard unsaved changes?",
          description: `Closing "${path}" now will discard its unsaved changes.`,
          confirmText: "Close without saving",
          variant: "danger",
        });
        if (discardApproved) removeTab(path);
      })();
    },
    [dialogs, removeTab, saveFile, tabs],
  );

  const createEntry = useCallback(
    async (kind: "file" | "folder", basePath?: string, explicitPath?: string) => {
      if (!activeProject) return;
      const isFile = kind === "file";
      const value = explicitPath ?? await dialogs.input({
        title: isFile ? "Create File" : "Create Folder",
        description: "Path is relative to the active project root.",
        label: isFile ? "File path" : "Folder path",
        placeholder: isFile ? "src/main.cpp" : "include",
        defaultValue: basePath ? `${basePath}/` : isFile ? "src/new_file.cpp" : "include",
        confirmText: isFile ? "Create File" : "Create Folder",
        validate: validateWorkspaceRelativePath,
      });
      if (!value) return;
      const path = normalizeWorkspaceRelativePath(value);
      try {
        const response = await promptForgeApi.createFile({
          project_id: activeProject.project_id,
          path,
          kind,
          content: "",
        });
        setEntries(response.entries);
        await refreshProjects(activeProject.project_id);
        if (kind === "file") {
          await openFile(path);
        }
      } catch (err) {
        const message = reportError(err, `${isFile ? "File" : "Folder"} could not be created`);
        await dialogs.message({
          title: isFile ? "Could not create file" : "Could not create folder",
          description: `${isFile ? "Could not create file" : "Could not create folder"}: ${message}`,
        });
      }
    },
    [activeProject, dialogs, openFile, refreshProjects, reportError],
  );

  const renameEntry = useCallback(
    async (entry: WorkspaceEntry, explicitPath?: string) => {
      if (!activeProject) return;
      const value = explicitPath ?? await dialogs.input({
        title: entry.kind === "file" ? "Rename File" : "Rename Folder",
        description: "Path is relative to the active project root.",
        label: "New path",
        defaultValue: entry.path,
        confirmText: "Rename",
        validate: validateWorkspaceRelativePath,
      });
      if (!value || value === entry.path) return;
      const path = normalizeWorkspaceRelativePath(value);
      if (path === entry.path) return;
      try {
        const response = await promptForgeApi.updateFile({
          project_id: activeProject.project_id,
          path: entry.path,
          new_path: path,
          kind: entry.kind,
        });
        await loadProjectFiles(activeProject.project_id);
        await refreshProjects(activeProject.project_id);
        setTabs((current) =>
          current.map((tab) => {
            const renamedPath =
              entry.kind === "folder" && tab.path.startsWith(`${entry.path}/`)
                ? `${path}/${tab.path.slice(entry.path.length + 1)}`
                : tab.path === entry.path
                  ? path
                  : null;
            return renamedPath
              ? {
                  ...tab,
                  path: renamedPath,
                  language: languageFor(renamedPath, tab.fileType),
                }
              : tab;
          }),
        );
        setActivePath((current) => {
          if (!current) return current;
          if (entry.kind === "folder" && current.startsWith(`${entry.path}/`)) {
            return `${path}/${current.slice(entry.path.length + 1)}`;
          }
          return current === entry.path ? path : current;
        });
        if ("entries" in response) {
          setEntries(response.entries);
        }
      } catch (err) {
        const message = reportError(err, "Workspace entry could not be renamed");
        await dialogs.message({
          title: "Could not rename entry",
          description: `Could not rename entry: ${message}`,
        });
      }
    },
    [activeProject, dialogs, loadProjectFiles, refreshProjects, reportError],
  );

  const deleteEntry = useCallback(
    async (entry: WorkspaceEntry) => {
      if (!activeProject) return;
      const approved = await dialogs.confirmAction({
        title: entry.kind === "file" ? "Delete File?" : "Delete Folder?",
        description: `Delete ${entry.path} from ${activeProject.project_name}?`,
        confirmText: "Delete",
        variant: "danger",
      });
      if (!approved) return;
      try {
        await promptForgeApi.deleteFile(activeProject.project_id, entry.path);
        await loadProjectFiles(activeProject.project_id);
        await refreshProjects(activeProject.project_id);
        setTabs((current) => current.filter((tab) => tab.path !== entry.path && !tab.path.startsWith(`${entry.path}/`)));
        setActivePath((current) => (current === entry.path || current?.startsWith(`${entry.path}/`) ? null : current));
      } catch (err) {
        const message = reportError(err, "Workspace entry could not be deleted");
        await dialogs.message({
          title: "Could not delete entry",
          description: `Could not delete entry: ${message}`,
        });
      }
    },
    [activeProject, dialogs, loadProjectFiles, refreshProjects, reportError],
  );

  const deleteProject = useCallback(async () => {
    if (!activeProject) return;
    const approved = await dialogs.confirmAction({
      title: "Delete Project?",
      description: `Delete project ${activeProject.project_name}?`,
      confirmText: "Delete",
      variant: "danger",
    });
    if (!approved) return;
    try {
      await promptForgeApi.deleteProject(activeProject.project_id);
    } catch (err) {
      const message = reportError(err, "Project could not be deleted");
      notify.error(message);
      return;
    }
    setTabs([]);
    setActivePath(null);
    setEntries([]);
    await refreshProjects();
  }, [activeProject, dialogs, refreshProjects, reportError]);

  const selectProject = useCallback((project: ProjectResponse) => {
    activeProjectIdRef.current = project.project_id;
    projectLoadSequenceRef.current += 1;
    setActiveProject(project);
    setTabs([]);
    setActivePath(null);
    setEntries([]);
    restoredProjectRef.current = null;
    window.localStorage.setItem(LAST_PROJECT_KEY, project.project_id);
  }, []);

  const rememberDesktopWorkspacePath = useCallback((path: string | null) => {
    setDesktopWorkspacePathState(path);
    if (path) {
      window.localStorage.setItem(LAST_WORKSPACE_PATH_KEY, path);
    } else {
      window.localStorage.removeItem(LAST_WORKSPACE_PATH_KEY);
    }
  }, []);

  const selectProjectByPath = useCallback(
    (path: string) => {
      const normalized = path.replaceAll("\\", "/").toLowerCase();
      const match = projects.find((project) => project.project_path.replaceAll("\\", "/").toLowerCase() === normalized);
      if (match) {
        selectProject(match);
        return true;
      }
      rememberDesktopWorkspacePath(path);
      return false;
    },
    [projects, rememberDesktopWorkspacePath, selectProject],
  );

  const importProjectPath = useCallback(
    async (path: string) => {
      setError(null);
      rememberDesktopWorkspacePath(path);
      addLog({ channel: "system", message: "Inspecting folder..." });
      try {
        const imported = await promptForgeApi.importProject({ path });
        const platformioDetected = imported.has_platformio_ini;
        addLog({
          channel: "system",
          message: platformioDetected
            ? "Detected PlatformIO project"
            : "No PlatformIO project detected. Create platformio.ini or initialize PlatformIO project.",
        });
        addLog({ channel: "system", message: "Registered external project" });
        addLog({ channel: "system", message: "Opening workspace..." });
        const refreshed = await refreshProjects(imported.project_id);
        const project = refreshed.find((item) => item.project_id === imported.project_id);
        if (project) {
          selectProject(project);
          await loadProjectFiles(project.project_id);
        }
        setActiveView("workspace");
        return imported;
      } catch (err) {
        const message = reportError(
          err,
          "This folder is not a supported ForgeX project yet. No platformio.ini was found.",
        );
        setError(
          message.includes("platformio.ini")
            ? message
            : "This folder is not a supported ForgeX project yet. No platformio.ini was found.",
        );
        return null;
      }
    },
    [addLog, loadProjectFiles, refreshProjects, rememberDesktopWorkspacePath, reportError, selectProject],
  );

  const buildActiveProject = useCallback(async () => {
    if (!activeProject || toolAction) return null;
    if (!hasPlatformIO(activeProject)) {
      const message = `Build requires platformio.ini. Generate or initialize a PlatformIO project first. Workspace root: ${activeProject.project_path}`;
      setError(message);
      addLog({ channel: "error", message });
      return null;
    }
    setToolAction("build");
    setError(null);
    addLog({ channel: "build", message: `Build started for ${activeProject.project_name}` });
    try {
      const response = await promptForgeApi.buildProject({
        project_id: activeProject.project_id,
        environment: activeEnvironmentName(activeProject),
      });
      setLastBuildResult(response);
      addLog({
        channel: response.result.success ? "build" : "error",
        message: response.result.message || `Build ${response.result.status}`,
      });
      await Promise.allSettled([refreshBuildHistory(), refreshWorkspaceLogs()]);
      return response;
    } catch (err) {
      reportError(err, "Build request failed");
      return null;
    } finally {
      setToolAction(null);
    }
  }, [activeProject, addLog, refreshBuildHistory, refreshWorkspaceLogs, reportError, toolAction]);

  const flashActiveProject = useCallback(
    async (board: DetectedBoard | null) => {
      if (!activeProject || toolAction) return null;
      if (!board) {
        await dialogs.message({
          title: "No matching device",
          description: `No matching ${projectBoardId(activeProject) ?? "target"} device detected. Connect a board and try again.`,
        });
        return null;
      }
      if (!hasPlatformIO(activeProject)) {
        const message = `Flash requires platformio.ini. Generate/build a PlatformIO project first. Workspace root: ${activeProject.project_path}`;
        setError(message);
        addLog({ channel: "error", message });
        return null;
      }
      const artifact = lastBuildResult?.result?.firmware_path ?? "Latest build artifact will be used if available.";
      const approved = await dialogs.confirmAction({
        title: "Flash firmware to device?",
        description: `Project: ${activeProject.project_name}\nBoard: ${board.board_type}\nPort: ${board.port}\nFirmware/build artifact: ${artifact}\n\nThis can modify connected hardware.`,
        confirmText: "Flash",
        variant: "hardware",
      });
      if (!approved) return null;
      setToolAction("flash");
      setError(null);
      addLog({ channel: "system", message: `Flash started for ${activeProject.project_name} on ${board.port}` });
      try {
        const response = await promptForgeApi.flashProject({
          project_id: activeProject.project_id,
          board_type: board.board_type,
          port: board.port,
          environment: activeEnvironmentName(activeProject),
          baudrate: baudFromProject(activeProject, "upload_speed", 115_200),
          verify: true,
        });
        setLastFlashResult(response);
        const flash = response.flash;
        addLog({
          channel: flash?.success ? "system" : response.build.success ? "error" : "build",
          message: flash?.message ?? response.build.message ?? "Flash did not run because build failed",
        });
        await Promise.allSettled([refreshBuildHistory(), refreshWorkspaceLogs()]);
        return response;
      } catch (err) {
        const message = reportError(err, "Flash request failed");
        await dialogs.message({
          title: "Flash failed",
          description: `Flash failed: ${message}`,
        });
        return null;
      } finally {
        setToolAction(null);
      }
    },
    [activeProject, addLog, dialogs, lastBuildResult, refreshBuildHistory, refreshWorkspaceLogs, reportError, toolAction],
  );

  const startSerialMonitor = useCallback(
    async (board: DetectedBoard | null) => {
      if (toolAction) return null;
      setToolAction("monitor");
      setError(null);
      try {
        const response = await promptForgeApi.startMonitor({
          project_id: activeProject?.project_id ?? null,
          port: board?.port ?? null,
          baudrate: baudFromProject(activeProject, "monitor_speed", 115_200),
          timeout_s: 1,
        });
        setMonitorStatus(response);
        addLog({
          channel: "serial",
          message: response.connected
            ? `Serial monitor connected on ${response.port ?? "auto"} at ${response.baudrate}`
            : `Serial monitor ${response.state}`,
        });
        return response;
      } catch (err) {
        reportError(err, "Serial monitor could not be started");
        return null;
      } finally {
        setToolAction(null);
      }
    },
    [activeProject, addLog, reportError, toolAction],
  );

  const stopSerialMonitor = useCallback(async () => {
    if (toolAction) return null;
    setToolAction("monitor");
    try {
      const response = await promptForgeApi.stopMonitor();
      setMonitorStatus(response);
      addLog({ channel: "serial", message: "Serial monitor disconnected" });
      return response;
    } catch (err) {
      reportError(err, "Serial monitor could not be stopped");
      return null;
    } finally {
      setToolAction(null);
    }
  }, [addLog, reportError, toolAction]);

  useEffect(() => {
    setDesktopWorkspacePathState(window.localStorage.getItem(LAST_WORKSPACE_PATH_KEY));
    promptForgeApi
      .health()
      .then(setHealth)
      .catch((err: unknown) => {
        setHealth(null);
        setError(toErrorMessage(err, "ForgeX API is unreachable"));
      });
    refreshProjects()
      .then(() => Promise.allSettled([refreshBuildHistory(), refreshWorkspaceLogs(), refreshDetectedBoards(), refreshMonitorStatus()]))
      .catch((err: unknown) => {
        setError(toErrorMessage(err, "Projects could not be loaded"));
      });
  }, [refreshBuildHistory, refreshDetectedBoards, refreshMonitorStatus, refreshProjects, refreshWorkspaceLogs]);

  useEffect(() => {
    if (!activeProject) {
      setEntries([]);
      return;
    }
    loadProjectFiles(activeProject.project_id).catch((err: unknown) => {
      setError(toErrorMessage(err, "Project files could not be loaded"));
    });
  }, [activeProject, loadProjectFiles]);

  useEffect(() => {
    if (!activeProject || restoredProjectRef.current === activeProject.project_id) return;
    restoredProjectRef.current = activeProject.project_id;
    const raw = window.localStorage.getItem(tabStorageKey(activeProject.project_id));
    if (!raw) return;
    let paths: string[] = [];
    try {
      paths = JSON.parse(raw) as string[];
    } catch {
      paths = [];
    }
    paths.slice(0, 8).forEach((path) => {
      void openFile(path).catch((err: unknown) => {
        console.warn(`ForgeX: could not restore open tab "${path}"`, err);
        window.localStorage.removeItem(tabStorageKey(activeProject.project_id));
      });
    });
  }, [activeProject, openFile]);

  useEffect(() => {
    if (!activeProject) return;
    const paths = tabs.map((tab) => tab.path);
    window.localStorage.setItem(tabStorageKey(activeProject.project_id), JSON.stringify(paths));
  }, [activeProject, tabs]);

  useEffect(() => {
    const dirtyCount = tabs.filter((tab) => tab.dirty).length;
    if (dirtyCount === 0) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = `${dirtyCount} unsaved ${dirtyCount === 1 ? "file" : "files"} will be lost if you leave ForgeX.`;
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [tabs]);

  useEffect(
    () => () => {
      closeExecutionSocketRef.current?.();
      closeExecutionSocketRef.current = null;
    },
    [],
  );

  const refreshAfterWorkflow = useCallback(async () => {
    if (activeProject?.external) {
      await promptForgeApi
        .importProject({ path: activeProject.project_path })
        .catch((err: unknown) => {
          console.warn("ForgeX: project re-import failed during refresh", err);
          return null;
        });
    }
    const refreshedProjects = await refreshProjects(activeProject?.project_id);
    const project =
      (activeProject
        ? refreshedProjects.find((item) => item.project_id === activeProject.project_id)
        : null) ?? latestProject(refreshedProjects);
    if (project) {
      setActiveProject(project);
      const loadedEntries = await loadProjectFiles(project.project_id);
      if (loadedEntries.some((entry) => entry.kind === "file" && entry.path === "src/main.cpp")) {
        try {
          const response = await promptForgeApi.fileContent(project.project_id, "src/main.cpp");
          const file = toFile(response.project_id, response.path, response.content, response.file_type);
          setTabs((current) => {
            const exists = current.some((tab) => tab.path === file.path);
            return exists
              ? current.map((tab) => (tab.path === file.path ? { ...file, dirty: false } : tab))
              : [...current, { ...file, dirty: false }];
          });
          setActivePath(file.path);
        } catch {
          // The explorer refresh is still useful even if the file opens later.
        }
      }
    } else {
      setEntries([]);
    }
    await Promise.allSettled([refreshBuildHistory(), refreshWorkspaceLogs()]);
  }, [activeProject, loadProjectFiles, refreshBuildHistory, refreshProjects, refreshWorkspaceLogs]);

  const refreshProjectFiles = useCallback(async () => {
    if (!activeProject) return [];
    if (activeProject.external) {
      await promptForgeApi
        .importProject({ path: activeProject.project_path })
        .catch((err: unknown) => {
          console.warn("ForgeX: project re-import failed during refresh", err);
          return null;
        });
    }
    return loadProjectFiles(activeProject.project_id);
  }, [activeProject, loadProjectFiles]);

  useEffect(() => {
    if (!activeProject) return;
    const refresh = () => {
      void refreshProjectFiles().catch((err: unknown) => {
        setError(toErrorMessage(err, "Project files could not be refreshed"));
      });
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("forgex:workspace-files-changed", refresh);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("forgex:workspace-files-changed", refresh);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [activeProject, refreshProjectFiles]);

  const execute = useCallback(
    async (prompt: string, options: ExecuteOptions = {}) => {
      const generatedTaskId = `task-${crypto.randomUUID()}`;
      if (!activeProject) {
        const message = "Open a workspace folder before running code generation.";
        setError(message);
        addLog({ channel: "error", message });
        return;
      }
      closeExecutionSocketRef.current?.();
      closeExecutionSocketRef.current = null;
      setTaskId(generatedTaskId);
      setExecutionId(null);
      setResult(null);
      setError(null);
      setGenerationProgress(null);
      setIsExecuting(true);
      setIsCancelling(false);
      setStages(initialStages.map((stage) => ({ ...stage, status: "pending" as const })));
      setLogs([]);
      addLog({ channel: "system", message: `Queued prompt execution ${generatedTaskId}` });

      const closeSocket = createExecutionSocket({
        taskId: generatedTaskId,
        onStateChange: (state) => {
          setSocketState(state === "error" ? "closed" : state);
        },
        onEvent: (event: ExecutionEvent) => {
          if (event.task_id !== generatedTaskId) return;
          setExecutionId(event.execution_id);
          addLog({ channel: consoleChannel(event), message: eventMessage(event) });
          for (const message of generationReportMessages(event)) {
            addLog({ channel: "workflow", message });
          }
          if (isLiveGenerationEvent(event)) {
            setGenerationProgress((current) => liveGenerationProgressFromEvent(current, event));
          }
          const progress = generationProgressFromEvent(event);
          if (progress) setGenerationProgress(progress);
          setStages((current) => applyStageEvent(current, event));
          if (!isTerminalEvent(event)) return;
          const status = terminalStatus(event);
          setResult({
            status,
            task_id: event.task_id,
            execution_time_ms:
              typeof event.payload.execution_time_ms === "number"
                ? event.payload.execution_time_ms
                : 0,
            steps: [],
            failures: [],
          });
          setStages((current) =>
            workflowCancelled(event)
              ? cancelActiveStages(current)
              : settleActiveStages(current, workflowFailed(event)),
          );
          setIsExecuting(false);
          setIsCancelling(false);
          const generationSucceeded = !workflowFailed(event) && !workflowCancelled(event);
          if (generationSucceeded) {
            addLog({ channel: "system", message: `Generated PlatformIO project in workspace root: ${activeProject.project_path}` });
          } else if (workflowCancelled(event)) {
            addLog({ channel: "workflow", message: "WORKFLOW CANCELLED" });
          }
          void refreshAfterWorkflow().catch((err: unknown) => {
            reportError(err, "Workspace could not be refreshed after workflow completion");
          });
          closeExecutionSocketRef.current?.();
          closeExecutionSocketRef.current = null;
        },
      });
      closeExecutionSocketRef.current = closeSocket;

      setSocketState("connecting");

      void promptForgeApi
        .execute(prompt, generatedTaskId, {
          projectId: activeProject.project_id,
          workspaceRoot: activeProject.project_path,
          selectedBoard: options.selectedBoard ?? projectBoardId(activeProject),
          selectedFramework: options.selectedFramework ?? "PlatformIO",
          generationMode: options.generationMode ?? (hasPlatformIO(activeProject) ? "modify_existing_project" : "generate_into_open_folder"),
        })
        .then((response) => {
          addLog({
            channel: "system",
            message: `Workflow start ${response.status.toLowerCase()}`,
          });
        })
        .catch((err: unknown) => {
          const message =
            toErrorMessage(err, "Workflow start request failed");
          addLog({ channel: "error", message });
          setIsExecuting(false);
          setIsCancelling(false);
        });
    },
    [
      addLog,
      activeProject,
      refreshAfterWorkflow,
      reportError,
    ],
  );

  const cancelExecution = useCallback(async () => {
    if (!taskId || !isExecuting || isCancelling) return;
    setIsCancelling(true);
    addLog({ channel: "workflow", message: "Cancellation requested" });
    try {
      await promptForgeApi.cancelExecution(taskId);
    } catch (err) {
      const message = reportError(err, "Workflow cancellation failed");
      addLog({ channel: "error", message });
      setIsCancelling(false);
    }
  }, [addLog, isCancelling, isExecuting, reportError, taskId]);

  return {
    health,
    projects,
    activeProject,
    setActiveProject: selectProject,
    activeView,
    setActiveView,
    entries,
    recentlyAddedPaths,
    files,
    tabs,
    activePath,
    selectedFile,
    openFile,
    closeTab,
    updateTabContent,
    saveFile,
    createEntry,
    renameEntry,
    deleteEntry,
    refreshProjectFiles,
    refreshProjects,
    deleteProject,
    buildHistory,
    refreshBuildHistory,
    workspaceLogs,
    refreshWorkspaceLogs,
    detectedBoards,
    refreshDetectedBoards,
    monitorStatus,
    serialMonitor,
    refreshMonitorStatus,
    toolAction,
    buildActiveProject,
    flashActiveProject,
    startSerialMonitor,
    stopSerialMonitor,
    desktopWorkspacePath,
    rememberDesktopWorkspacePath,
    selectProjectByPath,
    importProjectPath,
    stages,
    logs,
    addLog,
    generationProgress,
    clearLogs: () => setLogs([]),
    taskId,
    executionId,
    socketState,
    result,
    generatedProject: activeProject as unknown as Record<string, unknown> | null,
    buildResult: (lastBuildResult?.result as unknown as Record<string, unknown>) ?? null,
    flashResult: lastFlashResult,
    isExecuting,
    isCancelling,
    error,
    execute,
    cancelExecution,
  };
}
