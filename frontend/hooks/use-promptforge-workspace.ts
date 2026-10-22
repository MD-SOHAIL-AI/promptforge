"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { promptForgeApi } from "@/lib/api";
import { localSession } from "@/lib/local-session";
import { apiErrorCode, toErrorMessage } from "@/lib/errors";
import { SingleFlight } from "@/lib/single-flight";
import { WriteQueue } from "@/lib/write-queue";
import { useForgeXDialogs } from "@/components/ide/dialogs/forgex-dialog-provider";
import {
  EMPTY_FILE,
  LAST_PROJECT_KEY,
  LAST_WORKSPACE_PATH_KEY,
  activeEnvironmentName,
  baudFromProject,
  hasPlatformIO,
  initialStages,
  languageFor,
  latestProject,
  normalizeWorkspaceRelativePath,
  projectBoardId,
  tabStorageKey,
  toFile,
  validateWorkspaceRelativePath,
} from "@/lib/workspace-helpers";
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
  ExecutionEvent,
  GenerationFileProgress,
  GenerationProgress,
  HealthResponse,
  MonitorStatusResponse,
  ProjectResponse,
  ProjectImportResponse,
  WorkspaceEntry,
  WorkspaceFile,
  WorkspaceLog,
  WorkflowStage,
  ForgeXSettingValue,
} from "@/types";

export type WorkspaceView = "workspace" | "projects" | "builds" | "logs";
export type GenerationMode = "new_project" | "modify_existing_project" | "generate_into_open_folder";

export interface ExecuteOptions {
  selectedBoard?: string | null;
  selectedFramework?: string | null;
  generationMode?: GenerationMode;
}

export { validateWorkspaceRelativePath };

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

export function usePromptForgeWorkspace(preferences: Record<string, ForgeXSettingValue> = {}, preferencesLoading = false) {
  const preferencesRef = useRef(preferences);
  preferencesRef.current = preferences;
  const buildEnvironmentRef = useRef<string | null>(null);
  const sourceRevisionRef = useRef(0);
  const buildOperationRef = useRef<string | null>(null);
  const buildObserverRef = useRef<AbortController | null>(null);
  useEffect(() => () => buildObserverRef.current?.abort(), []);
  const [buildObservation, setBuildObservation] = useState<import("@/lib/build-observation").BuildObservation>({state: "idle", projectId: null, environment: null, revision: 0, operationId: null, observedAt: null});
  const [sourceEditsRevision, setSourceEditsRevision] = useState(0);
  const invalidateBuild = useCallback(() => {
    sourceRevisionRef.current += 1;
    setSourceEditsRevision(sourceRevisionRef.current);
    setBuildObservation((value) => value.state === "idle" ? value : {...value, state: "stale", message: "Source or target changed. Build again to verify this revision."});
  }, []);
  const setBuildEnvironment = useCallback((name: string | null) => {
    if (buildEnvironmentRef.current !== name) invalidateBuild();
    buildEnvironmentRef.current = name;
  }, [invalidateBuild]);
  const dialogs = useForgeXDialogs();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [backendRecovery, setBackendRecovery] = useState<{state:string; lastError?:string; restartScheduled?:boolean} | null>(null);
  useEffect(() => {
    if (!window.forgexDesktop?.getStatus) return;
    let current = true;
    let pending = false;
    const refresh = () => {
      if (pending) return;
      pending = true;
      void window.forgexDesktop!.getStatus().then(value => {
        if (current && value && typeof value === "object" && "backend" in value) {
          setBackendRecovery((value as {backend:{state:string;lastError?:string;restartScheduled?:boolean}}).backend);
        }
      }).catch(() => {}).finally(() => {pending = false;});
    };
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => {current = false; window.clearInterval(timer);};
  }, []);
  const [projects, setProjects] = useState<ProjectResponse[]>([]);
  const [activeProject, setActiveProject] = useState<ProjectResponse | null>(null);
  const [availableProjectId, setAvailableProjectId] = useState<string | null>(null);
  const [missingProjectId, setMissingProjectId] = useState<string | null>(null);
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [recentlyAddedPaths, setRecentlyAddedPaths] = useState<string[]>([]);
  const [stages, setStages] = useState<WorkflowStage[]>(initialStages);
  const [tabs, setTabs] = useState<EditorTab[]>([]);
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const fileWritesRef = useRef(new WriteQueue());
  const [activePath, setActivePath] = useState<string | null>(null);
  const [logs, setLogs] = useState<ConsoleEntry[]>([]);
  const [workspaceLogs, setWorkspaceLogs] = useState<WorkspaceLog[]>([]);
  const [buildHistory, setBuildHistory] = useState<BuildHistoryItem[]>([]);
  const [detectedBoards, setDetectedBoards] = useState<DetectedBoard[]>([]);
  const [monitorStatus, setMonitorStatus] = useState<MonitorStatusResponse | null>(null);
  const [toolAction, setToolAction] = useState<"build" | "flash" | "monitor" | null>(null);
  const [desktopWorkspacePath, setDesktopWorkspacePathState] = useState<string | null>(null);
  const [lastBuildResult, setLastBuildResult] = useState<BuildResponse | null>(null);
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
  const missingProjectsRef = useRef(new Set<string>());
  const projectReadsRef = useRef(new SingleFlight());
  const importProjectInFlightRef = useRef<Map<string, Promise<ProjectImportResponse | null>>>(new Map());

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

  const markProjectMissing = useCallback((projectId: string, cause: unknown) => {
    if (apiErrorCode(cause) !== "PROJECT_PATH_MISSING" || activeProjectIdRef.current !== projectId) return false;
    missingProjectsRef.current.add(projectId);
    setMissingProjectId(projectId);
    setAvailableProjectId(null);
    setEntries([]);
    setTabs([]);
    setActivePath(null);
    setError(null);
    return true;
  }, []);

  const loadProjectFiles = useCallback((projectId: string) => projectReadsRef.current.run(`files:${projectId}`, async () => {
    if (missingProjectsRef.current.has(projectId)) return [];
    const sequence = ++projectLoadSequenceRef.current;
    let response;
    try {
      response = await promptForgeApi.projectFiles(projectId);
    } catch (cause) {
      if (sequence !== projectLoadSequenceRef.current || activeProjectIdRef.current !== projectId) return [];
      if (markProjectMissing(projectId, cause)) return [];
      throw cause;
    }
    if (sequence !== projectLoadSequenceRef.current) return [];
    if (activeProjectIdRef.current && activeProjectIdRef.current !== projectId) return [];
    const previousPaths = new Set(entriesRef.current.map((entry) => entry.path));
    const addedPaths = entriesProjectRef.current === projectId
      ? response.entries.filter((entry) => !previousPaths.has(entry.path)).map((entry) => entry.path)
      : [];
    entriesRef.current = response.entries;
    entriesProjectRef.current = projectId;
    setEntries(response.entries);
    setAvailableProjectId(projectId);
    if (addedPaths.length > 0) setRecentlyAddedPaths(addedPaths);
    return response.entries;
  }), [markProjectMissing]);

  useEffect(() => {
    if (recentlyAddedPaths.length === 0) return;
    const timeout = window.setTimeout(() => setRecentlyAddedPaths([]), 2_400);
    return () => window.clearTimeout(timeout);
  }, [recentlyAddedPaths]);

  const refreshBuildHistory = useCallback(async () => {
    const response = await promptForgeApi.buildHistory();
    setBuildHistory(response.builds);
  }, []);

  const refreshWorkspaceLogs = useCallback(async (selectedExecutionId?: string) => {
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
  }, []);

  const refreshDetectedBoards = useCallback(async () => {
    const response = await promptForgeApi.detectedBoards();
    setDetectedBoards(response.boards);
    return response.boards;
  }, []);

  const refreshMonitorStatus = useCallback(async () => {
    const response = await promptForgeApi.monitorStatus();
    setMonitorStatus(response);
    return response;
  }, []);

  const refreshProjects = useCallback(async (preferredProjectId?: string | null) => {
    const response = await promptForgeApi.projects();
    setProjects(response.projects);
    setActiveProject((current) => {
      const rememberedProjectId =
        preferredProjectId ?? (preferencesRef.current["workspace.remember_last_workspace"] !== false && preferencesRef.current["general.startup_behavior"] !== "open_empty_workspace" ? window.localStorage.getItem(LAST_PROJECT_KEY) : null);
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
      const selected = preferencesRef.current["general.startup_behavior"] === "open_empty_workspace" ? null : response.projects[0] ?? null;
      activeProjectIdRef.current = selected?.project_id ?? null;
      return selected;
    });
    return response.projects;
  }, []);

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
        setTabs((current) => current.map((tab) =>
          tab.path === path && !tab.dirty ? { ...file, revision: response.revision, dirty: false } : tab,
        ));
        setActivePath(file.path);
      } catch (err) {
        const cached = activeProject.files.find((file) => file.path === path);
        if (cached) {
          const file = toFile(activeProject.project_id, cached.path, cached.content, cached.file_type);
          setTabs((current) =>
            current.map((tab) => (tab.path === path && !tab.dirty ? { ...file, dirty: false } : tab)),
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

  const closeTab = useCallback((path: string) => {
    const candidate = tabs.find((tab) => tab.path === path);
    if (candidate?.dirty && !window.confirm(`Discard unsaved changes to ${path}? Cancel keeps the tab open.`)) return;
    setTabs((current) => {
      const next = current.filter((tab) => tab.path !== path);
      setActivePath((selected) => {
        if (selected !== path) return selected;
        return next.at(-1)?.path ?? null;
      });
      return next;
    });
  }, [tabs]);

  const updateTabContent = useCallback((path: string, content: string) => {
    invalidateBuild();
    const next = tabsRef.current.map((tab) => tab.path === path ? {...tab, content, dirty: true} : tab);
    tabsRef.current = next;
    setTabs(next);
  }, [invalidateBuild]);

  const saveFile = useCallback(
    async (path: string, contentOverride?: string) => {
      if (!activeProject) return;
      const tab = tabsRef.current.find((item) => item.path === path);
      if (!tab) return;
      const projectId = activeProject.project_id;
      const submitted = contentOverride ?? tab.content;
      invalidateBuild();
      try {
        await fileWritesRef.current.run(JSON.stringify([projectId, path]), async () => {
          if (activeProjectIdRef.current !== projectId) return;
          const currentTab = tabsRef.current.find((item) => item.path === path);
          if (!currentTab) return;
          const response = await promptForgeApi.updateFile({
            project_id: projectId,
            path,
            content: submitted,
            expected_revision: currentTab.revision ?? undefined,
            file_type: currentTab.fileType,
          });
          if (activeProjectIdRef.current !== projectId) return;
          if ("content" in response) {
            // A successful save advances the disk baseline even if the operator
            // typed more text while it was in flight. Keep that newer buffer dirty.
            const next = tabsRef.current.map((item) =>
              item.path === path && item.revision === currentTab.revision
                ? {...item, revision: response.revision, dirty: item.content !== submitted}
                : item);
            tabsRef.current = next;
            setTabs(next);
          }
        });
        if (activeProjectIdRef.current !== projectId) return;
        await loadProjectFiles(projectId);
        await refreshProjects(projectId);
      } catch (err) {
        reportError(err, `File could not be saved: ${path}`);
      }
    },
    [activeProject, invalidateBuild, loadProjectFiles, refreshProjects, reportError],
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
      invalidateBuild();
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
    [activeProject, dialogs, invalidateBuild, openFile, refreshProjects, reportError],
  );

  const renameEntry = useCallback(
    async (entry: WorkspaceEntry, explicitPath?: string) => {
      if (!activeProject) return;
      if (tabs.some(tab => tab.dirty && (tab.path === entry.path || entry.kind === "folder" && tab.path.startsWith(`${entry.path}/`)))) {
        await dialogs.message({ title: "Unsaved changes", description: "Save or discard changes in affected tabs before renaming this entry." });
        return;
      }
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
      invalidateBuild();
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
    [activeProject, dialogs, invalidateBuild, loadProjectFiles, refreshProjects, reportError, tabs],
  );

  const deleteEntry = useCallback(
    async (entry: WorkspaceEntry) => {
      if (!activeProject) return;
      if (tabs.some(tab => tab.dirty && (tab.path === entry.path || entry.kind === "folder" && tab.path.startsWith(`${entry.path}/`)))) {
        await dialogs.message({ title: "Unsaved changes", description: "Save or discard changes in affected tabs before deleting this entry." });
        return;
      }
      const approved = preferencesRef.current["workspace.confirm_before_delete"] === false ? true : await dialogs.confirmAction({
        title: entry.kind === "file" ? "Delete File?" : "Delete Folder?",
        description: `Delete ${entry.path} from ${activeProject.project_name}?`,
        confirmText: "Delete",
        variant: "danger",
      });
      if (!approved) return;
      try {
        await promptForgeApi.deleteFile(activeProject.project_id, entry.path);
        invalidateBuild();
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
    [activeProject, dialogs, invalidateBuild, loadProjectFiles, refreshProjects, reportError, tabs],
  );

  const deleteProject = useCallback(async () => {
    if (!activeProject) return;
    if (tabs.some(tab => tab.dirty)) {
      await dialogs.message({ title: "Unsaved changes", description: "Save or discard open file changes before deleting the project." });
      return;
    }
    const approved = await dialogs.confirmAction({
      title: "Delete Project?",
      description: `Delete project ${activeProject.project_name}?`,
      confirmText: "Delete",
      variant: "danger",
    });
    if (!approved) return;
    await promptForgeApi.deleteProject(activeProject.project_id);
    setTabs([]);
    setActivePath(null);
    setEntries([]);
    await refreshProjects();
  }, [activeProject, dialogs, refreshProjects, tabs]);

  const selectProject = useCallback((project: ProjectResponse) => {
    if (activeProjectIdRef.current === project.project_id && project.metadata?.missing !== true
        && !missingProjectsRef.current.has(project.project_id)) return;
    if (tabs.some(tab => tab.dirty)) {
      window.alert("Save or discard unsaved editor changes before switching projects.");
      return;
    }
    buildObserverRef.current?.abort();
    buildObserverRef.current = null;
    setToolAction((value) => value === "build" ? null : value);
    sourceRevisionRef.current += 1;
    buildOperationRef.current = null;
    setLastBuildResult(null);
    setBuildObservation({state: "idle", projectId: project.project_id, environment: null, revision: sourceRevisionRef.current, operationId: null, observedAt: null});
    activeProjectIdRef.current = project.project_id;
    projectLoadSequenceRef.current += 1;
    setActiveProject(project);
    setAvailableProjectId(null);
    const missing = project.metadata?.missing === true || missingProjectsRef.current.has(project.project_id);
    if (missing) missingProjectsRef.current.add(project.project_id);
    setMissingProjectId(missing ? project.project_id : null);
    setTabs([]);
    setActivePath(null);
    setEntries([]);
    restoredProjectRef.current = null;
    if (preferencesRef.current["workspace.remember_last_workspace"] !== false) window.localStorage.setItem(LAST_PROJECT_KEY, project.project_id);
    else window.localStorage.removeItem(LAST_PROJECT_KEY);
  }, [tabs]);

  const rememberDesktopWorkspacePath = useCallback((path: string | null) => {
    setDesktopWorkspacePathState(path);
    if (path && preferencesRef.current["workspace.remember_last_workspace"] !== false) {
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
      const importKey = path.replaceAll("\\", "/").toLowerCase();
      const inFlight = importProjectInFlightRef.current.get(importKey);
      if (inFlight) return inFlight;
      setError(null);
      rememberDesktopWorkspacePath(path);
      addLog({ channel: "system", message: "Inspecting folder..." });
      const operation = (async () => {
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
      })();
      importProjectInFlightRef.current.set(importKey, operation);
      try {
        return await operation;
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
      } finally {
        importProjectInFlightRef.current.delete(importKey);
      }
    },
    [addLog, loadProjectFiles, refreshProjects, rememberDesktopWorkspacePath, reportError, selectProject],
  );

  const buildActiveProject = useCallback(async () => {
    if (!activeProject || toolAction || buildObserverRef.current) return null;
    if (tabs.some(tab => tab.dirty)) {
      setBuildObservation({state:"stale", projectId:activeProject.project_id, environment:buildEnvironmentRef.current, revision:sourceRevisionRef.current, operationId:null, observedAt:null, message:"Save the edited files before building this revision."});
      return null;
    }
    if (!hasPlatformIO(activeProject)) {
      const message = `Build requires platformio.ini. Generate or initialize a PlatformIO project first. Workspace root: ${activeProject.project_path}`;
      setError(message);
      addLog({ channel: "error", message });
      return null;
    }
    setToolAction("build");
    const operationId = crypto.randomUUID();
    const observer = new AbortController();
    buildObserverRef.current = observer;
    buildOperationRef.current = operationId;
    const revision = sourceRevisionRef.current;
    const environment = buildEnvironmentRef.current ?? activeEnvironmentName(activeProject);
    window.localStorage.setItem(`forgex-build-operation:${activeProject.project_id}`, JSON.stringify({operationId, environment}));
    setBuildObservation({state: "running", projectId: activeProject.project_id, environment, revision, operationId, observedAt: new Date().toISOString()});
    if (preferencesRef.current["terminal.clear_output_before_build"] === true) setLogs([]);
    setError(null);
    addLog({ channel: "build", message: `Build started for ${activeProject.project_name}` });
    try {
      const response = await promptForgeApi.buildProject({
        project_id: activeProject.project_id,
        environment,
      }, operationId, observer.signal);
      if (activeProjectIdRef.current !== activeProject.project_id || buildOperationRef.current !== operationId) return response;
      if (sourceRevisionRef.current !== revision) {
        setBuildObservation((value) => ({...value, state: "stale", message: "Source changed during the build. Build again to verify it."}));
        return response;
      }
      setLastBuildResult(response);
      setBuildObservation({state: response.operation_state === "stale" ? "stale" : response.result.success ? "passed" : "failed", projectId: activeProject.project_id, environment, revision, operationId, observedAt: new Date().toISOString(), message: response.operation_state === "stale" ? "Source changed during compilation. Build again to verify the current revision." : response.result.message});
      addLog({
        channel: response.result.success ? "build" : "error",
        message: response.result.message || `Build ${response.result.status}`,
      });
      await Promise.allSettled([refreshBuildHistory(), refreshWorkspaceLogs()]);
      return response;
    } catch (err) {
      if (activeProjectIdRef.current !== activeProject.project_id || buildOperationRef.current !== operationId) return null;
      setBuildObservation((value) => ({...value, state: "unavailable", observedAt: new Date().toISOString(), message: "Build result could not be retrieved. Refresh the saved operation before retrying."}));
      reportError(err, "Build request failed");
      return null;
    } finally {
      if (buildOperationRef.current === operationId) {
        setToolAction(null);
        buildObserverRef.current = null;
      }
    }
  }, [activeProject, addLog, refreshBuildHistory, refreshWorkspaceLogs, reportError, toolAction, tabs]);

  const refreshBuildOperation = useCallback(async () => {
    if (!activeProject) return;
    let saved: {operationId: string; environment: string | null} | null = null;
    try { saved = JSON.parse(window.localStorage.getItem(`forgex-build-operation:${activeProject.project_id}`) ?? "null"); }
    catch { return; }
    if (!saved) return;
    const projectId = activeProject.project_id;
    const revision = sourceRevisionRef.current;
    const environment = buildEnvironmentRef.current ?? activeEnvironmentName(activeProject);
    if (saved.environment !== environment) return;
    const operation = await promptForgeApi.buildOperation(saved.operationId);
    if (operation.project_id !== projectId || operation.environment !== environment) return;
    if (activeProjectIdRef.current !== projectId || revision !== sourceRevisionRef.current) return;
    buildOperationRef.current = saved.operationId;
    if (operation.result) setLastBuildResult(operation.result);
    setBuildObservation({state:tabs.some(tab => tab.dirty) ? "stale" : operation.state === "queued" ? "running" : operation.state === "interrupted" ? "unavailable" : operation.state,
      projectId, environment, revision, operationId:saved.operationId, observedAt:operation.updated_at, message:operation.message ?? operation.result?.result.message});
  }, [activeProject, tabs]);

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
      await dialogs.message({
        title: "Flash through Forge",
        description: `Open a Forge task for ${activeProject.project_name} and ask it to flash the verified firmware to ${board.port}. Forge will prepare the flash and request one-time confirmation.`,
      });
      return null;
    },
    [activeProject, addLog, dialogs, toolAction],
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
          baudrate: baudFromProject(activeProject, "monitor_speed", Number(preferencesRef.current["terminal.serial_baud_default"] ?? preferencesRef.current["hardware.default_monitor_speed"] ?? 115_200)),
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
    if (preferencesLoading) return;
    setDesktopWorkspacePathState(preferencesRef.current["workspace.remember_last_workspace"] !== false && preferencesRef.current["general.startup_behavior"] === "open_last_workspace" ? window.localStorage.getItem(LAST_WORKSPACE_PATH_KEY) : null);
    promptForgeApi
      .health()
      .then(setHealth)
      .catch((err: unknown) => {
        setHealth(null);
        setError(toErrorMessage(err, "ForgeX API is unreachable"));
      });
    refreshProjects()
      .then(() => Promise.allSettled([refreshBuildHistory(), refreshWorkspaceLogs(), ...(preferencesRef.current["hardware.auto_detect_devices"] !== false ? [refreshDetectedBoards()] : []), refreshMonitorStatus()]))
      .catch((err: unknown) => {
        setError(toErrorMessage(err, "Projects could not be loaded"));
      });
  }, [preferencesLoading, refreshBuildHistory, refreshDetectedBoards, refreshMonitorStatus, refreshProjects, refreshWorkspaceLogs]);

  useEffect(() => {
    if (preferencesLoading) return;
    let current = true;
    let pending = false;
    const check = () => {
      if (pending || localSession.getSnapshot() || document.visibilityState !== "visible") return;
      pending = true;
      void promptForgeApi.health().then(value => {if (current) setHealth(value);}).catch(() => {if (current) setHealth(null);}).finally(() => {pending = false;});
    };
    const timer = window.setInterval(check, 30_000);
    window.addEventListener("focus", check);
    return () => {current = false; window.clearInterval(timer); window.removeEventListener("focus", check);};
  }, [preferencesLoading]);

  const autoDetectDevices = preferences["hardware.auto_detect_devices"] !== false;
  const deviceRefreshSeconds = Math.max(2, Number(preferences["hardware.refresh_devices_interval"] ?? 5));
  useEffect(() => {
    if (preferencesLoading || !autoDetectDevices) return;
    let pending = false;
    const timer = window.setInterval(() => {
      if (pending || localSession.getSnapshot() || document.visibilityState !== "visible") return;
      pending = true;
      void refreshDetectedBoards().catch(() => undefined).finally(() => { pending = false; });
    }, deviceRefreshSeconds * 1000);
    return () => window.clearInterval(timer);
  }, [preferencesLoading, autoDetectDevices, deviceRefreshSeconds, refreshDetectedBoards]);

  useEffect(() => {
    if (!activeProject) {
      setEntries([]);
      return;
    }
    if (activeProject.metadata?.missing === true) {
      missingProjectsRef.current.add(activeProject.project_id);
      setMissingProjectId(activeProject.project_id);
      setAvailableProjectId(null);
      return;
    }
    loadProjectFiles(activeProject.project_id).catch((err: unknown) => {
      setError(toErrorMessage(err, "Project files could not be loaded"));
    });
  }, [activeProject, loadProjectFiles]);

  useEffect(() => {
    if (!activeProject || availableProjectId !== activeProject.project_id || restoredProjectRef.current === activeProject.project_id) return;
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
      void openFile(path).catch(() => {
        window.localStorage.removeItem(tabStorageKey(activeProject.project_id));
      });
    });
  }, [activeProject, availableProjectId, openFile]);

  useEffect(() => {
    if (!activeProject) return;
    const paths = tabs.map((tab) => tab.path);
    window.localStorage.setItem(tabStorageKey(activeProject.project_id), JSON.stringify(paths));
  }, [activeProject, tabs]);

  useEffect(
    () => () => {
      closeExecutionSocketRef.current?.();
      closeExecutionSocketRef.current = null;
    },
    [],
  );

  const refreshAfterWorkflow = useCallback(async () => {
    if (activeProject && missingProjectsRef.current.has(activeProject.project_id)) return;
    if (activeProject?.external) {
      try { await promptForgeApi.refreshProject(activeProject.project_id); }
      catch (cause) { if (markProjectMissing(activeProject.project_id, cause)) return; throw cause; }
    }
    const refreshedProjects = await refreshProjects(activeProject?.project_id);
    const project =
      (activeProject
        ? refreshedProjects.find((item) => item.project_id === activeProject.project_id)
        : null) ?? latestProject(refreshedProjects);
    if (project) {
      setActiveProject(project);
      const loadedEntries = await loadProjectFiles(project.project_id);
      if (preferencesRef.current["general.auto_open_generated_main_cpp"] !== false && loadedEntries.some((entry) => entry.kind === "file" && entry.path === "src/main.cpp")) {
        try {
          const response = await promptForgeApi.fileContent(project.project_id, "src/main.cpp");
          const file = toFile(response.project_id, response.path, response.content, response.file_type, response.revision);
          setTabs((current) => {
            const exists = current.some((tab) => tab.path === file.path);
            return exists
              ? current.map((tab) => (tab.path === file.path && !tab.dirty ? { ...file, revision: response.revision, dirty: false } : tab))
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
  }, [activeProject, loadProjectFiles, markProjectMissing, refreshBuildHistory, refreshProjects, refreshWorkspaceLogs]);

  const refreshProjectFiles = useCallback(() => {
    if (!activeProject || missingProjectsRef.current.has(activeProject.project_id)) return Promise.resolve([]);
    const project = activeProject;
    return projectReadsRef.current.run(`refresh:${project.project_id}`, async () => {
      if (project.external) {
        try { await promptForgeApi.refreshProject(project.project_id); }
        catch (cause) {
          if (markProjectMissing(project.project_id, cause)) return [];
          throw cause;
        }
      }
      if (activeProjectIdRef.current !== project.project_id) return [];
      const entries = await loadProjectFiles(project.project_id);
      // Focus/visibility is an observation, not a source edit. Let the saved
      // operation compare the actual source hash before changing build truth.
      if (buildOperationRef.current && !buildObserverRef.current) await refreshBuildOperation();
      return entries;
    });
  }, [activeProject, loadProjectFiles, markProjectMissing, refreshBuildOperation]);

  useEffect(() => {
    if (!activeProject || preferences["workspace.auto_refresh_explorer"] === false) return;
    let pending: number | null = null;
    const refresh = () => {
      if (pending !== null || missingProjectsRef.current.has(activeProject.project_id)) return;
      pending = window.setTimeout(() => {
        pending = null;
        void refreshProjectFiles().catch((err: unknown) => {
        setError(toErrorMessage(err, "Project files could not be refreshed"));
        });
      }, 100);
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("forgex:workspace-files-changed", refresh);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      if (pending !== null) window.clearTimeout(pending);
      window.removeEventListener("forgex:workspace-files-changed", refresh);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [activeProject, refreshProjectFiles, preferences]);

  const forgetMissingProject = useCallback(async () => {
    const projectId = activeProjectIdRef.current;
    if (!projectId || !missingProjectsRef.current.has(projectId)) return;
    await promptForgeApi.forgetRecentProject(projectId);
    window.localStorage.removeItem(LAST_PROJECT_KEY);
    activeProjectIdRef.current = null;
    projectLoadSequenceRef.current++;
    setActiveProject(null);
    setMissingProjectId(null);
    setAvailableProjectId(null);
    await refreshProjects();
  }, [refreshProjects]);

  const locateMissingProject = useCallback(async (path: string) => {
    const projectId = activeProjectIdRef.current;
    if (!projectId || !missingProjectsRef.current.has(projectId)) return;
    const project = await promptForgeApi.locateRecentProject(projectId, path);
    if (activeProjectIdRef.current !== projectId) return;
    missingProjectsRef.current.delete(projectId);
    setMissingProjectId(null);
    selectProject(project);
    rememberDesktopWorkspacePath(project.project_path);
    await refreshProjects(projectId);
    await loadProjectFiles(projectId);
  }, [loadProjectFiles, refreshProjects, rememberDesktopWorkspacePath, selectProject]);

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
    setBuildEnvironment,
    health,
    backendRecovery,
    projects,
    activeProject,
    activeProjectAvailable: activeProject !== null && availableProjectId === activeProject.project_id && missingProjectId !== activeProject.project_id,
    missingProject: activeProject !== null && (missingProjectId === activeProject.project_id || activeProject.metadata?.missing === true),
    markProjectMissing,
    forgetMissingProject,
    locateMissingProject,
    setActiveProject: selectProject,
    activeView,
    setActiveView,
    entries: entries.filter(entry => {
      const parts = entry.path.split("/");
      const excluded = preferences["workspace.excluded_folders"];
      return (!Array.isArray(excluded) || !parts.some(part => excluded.includes(part))) && (preferences["workspace.show_hidden_files"] === true || !parts.some(part => part.startsWith(".") && ![".gitignore", ".clang-format"].includes(part)));
    }),
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
    refreshMonitorStatus,
    toolAction,
    buildActiveProject,
    refreshBuildOperation,
    sourceEditsRevision,
    buildEnvironment: buildEnvironmentRef.current,
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
    buildObservation,
    flashResult: null,
    isExecuting,
    isCancelling,
    error,
    execute,
    cancelExecution,
  };
}
