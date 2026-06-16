"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PromptForgeApiError, promptForgeApi } from "@/lib/api";
import {
  applyStageEvent,
  isTerminalEvent,
  settleActiveStages,
  terminalStatus,
  workflowFailed,
} from "@/lib/workflow-stage-state";
import { createExecutionSocket } from "@/lib/websocket";
import type {
  BuildHistoryItem,
  ConsoleEntry,
  EditorTab,
  ExecuteResponse,
  ExecutionEvent,
  HealthResponse,
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
  if (event.event.startsWith("BUILD")) return "build";
  if (event.event.startsWith("MONITOR")) return "serial";
  return "workflow";
}

function eventMessage(event: ExecutionEvent) {
  const status = typeof event.payload.status === "string" ? ` ${event.payload.status}` : "";
  return `${event.event.replaceAll("_", " ")}${status}`;
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

export function usePromptForgeWorkspace() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [projects, setProjects] = useState<ProjectResponse[]>([]);
  const [activeProject, setActiveProject] = useState<ProjectResponse | null>(null);
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [stages, setStages] = useState<WorkflowStage[]>(initialStages);
  const [tabs, setTabs] = useState<EditorTab[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [logs, setLogs] = useState<ConsoleEntry[]>([]);
  const [workspaceLogs, setWorkspaceLogs] = useState<WorkspaceLog[]>([]);
  const [buildHistory, setBuildHistory] = useState<BuildHistoryItem[]>([]);
  const [activeView, setActiveView] = useState<WorkspaceView>("workspace");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [socketState, setSocketState] = useState<"idle" | "connecting" | "open" | "closed">("idle");
  const [result, setResult] = useState<ExecuteResponse | null>(null);
  const [isExecuting, setIsExecuting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const restoredProjectRef = useRef<string | null>(null);
  const closeExecutionSocketRef = useRef<(() => void) | null>(null);

  const selectedFile = useMemo(
    () => tabs.find((tab) => tab.path === activePath) ?? tabs[0] ?? EMPTY_FILE,
    [activePath, tabs],
  );

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
      const message =
        err instanceof PromptForgeApiError || err instanceof Error ? err.message : fallback;
      setError(message);
      addLog({ channel: "error", message });
      return message;
    },
    [addLog],
  );

  const loadProjectFiles = useCallback(async (projectId: string) => {
    const response = await promptForgeApi.projectFiles(projectId);
    setEntries(response.entries);
    return response.entries;
  }, []);

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

  const refreshProjects = useCallback(async (preferredProjectId?: string | null) => {
    const response = await promptForgeApi.projects();
    setProjects(response.projects);
    setActiveProject((current) => {
      if (preferredProjectId) {
        const preferred = response.projects.find((project) => project.project_id === preferredProjectId);
        if (preferred) return preferred;
      }
      if (current) {
        return response.projects.find((project) => project.project_id === current.project_id) ?? response.projects[0] ?? null;
      }
      return response.projects[0] ?? null;
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
        const response = await promptForgeApi.fileContent(activeProject.project_id, path);
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

  const closeTab = useCallback((path: string) => {
    setTabs((current) => {
      const next = current.filter((tab) => tab.path !== path);
      setActivePath((selected) => {
        if (selected !== path) return selected;
        return next.at(-1)?.path ?? null;
      });
      return next;
    });
  }, []);

  const updateTabContent = useCallback((path: string, content: string) => {
    setTabs((current) =>
      current.map((tab) => (tab.path === path ? { ...tab, content, dirty: true } : tab)),
    );
  }, []);

  const saveFile = useCallback(
    async (path: string) => {
      if (!activeProject) return;
      const tab = tabs.find((item) => item.path === path);
      if (!tab) return;
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
      } catch (err) {
        reportError(err, `File could not be saved: ${path}`);
      }
    },
    [activeProject, loadProjectFiles, refreshProjects, reportError, tabs],
  );

  const createEntry = useCallback(
    async (kind: "file" | "folder", basePath?: string) => {
      if (!activeProject) return;
      const label = kind === "file" ? "File path" : "Folder path";
      const value = window.prompt(label, basePath ? `${basePath}/` : kind === "file" ? "src/new_file.cpp" : "src/new_folder");
      if (!value) return;
      try {
        const response = await promptForgeApi.createFile({
          project_id: activeProject.project_id,
          path: value,
          kind,
          content: "",
        });
        setEntries(response.entries);
        await refreshProjects(activeProject.project_id);
        if (kind === "file") {
          await openFile(value);
        }
      } catch (err) {
        reportError(err, `${kind === "file" ? "File" : "Folder"} could not be created`);
      }
    },
    [activeProject, openFile, refreshProjects, reportError],
  );

  const renameEntry = useCallback(
    async (entry: WorkspaceEntry) => {
      if (!activeProject) return;
      const value = window.prompt("New path", entry.path);
      if (!value || value === entry.path) return;
      try {
        const response = await promptForgeApi.updateFile({
          project_id: activeProject.project_id,
          path: entry.path,
          new_path: value,
          kind: entry.kind,
        });
        await loadProjectFiles(activeProject.project_id);
        await refreshProjects(activeProject.project_id);
        setTabs((current) =>
          current.map((tab) => {
            const renamedPath =
              entry.kind === "folder" && tab.path.startsWith(`${entry.path}/`)
                ? `${value}/${tab.path.slice(entry.path.length + 1)}`
                : tab.path === entry.path
                  ? value
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
            return `${value}/${current.slice(entry.path.length + 1)}`;
          }
          return current === entry.path ? value : current;
        });
        if ("entries" in response) {
          setEntries(response.entries);
        }
      } catch (err) {
        reportError(err, "Workspace entry could not be renamed");
      }
    },
    [activeProject, loadProjectFiles, refreshProjects, reportError],
  );

  const deleteEntry = useCallback(
    async (entry: WorkspaceEntry) => {
      if (!activeProject) return;
      if (!window.confirm(`Delete ${entry.path}?`)) return;
      try {
        await promptForgeApi.deleteFile(activeProject.project_id, entry.path);
        await loadProjectFiles(activeProject.project_id);
        await refreshProjects(activeProject.project_id);
        setTabs((current) => current.filter((tab) => tab.path !== entry.path && !tab.path.startsWith(`${entry.path}/`)));
        setActivePath((current) => (current === entry.path || current?.startsWith(`${entry.path}/`) ? null : current));
      } catch (err) {
        reportError(err, "Workspace entry could not be deleted");
      }
    },
    [activeProject, loadProjectFiles, refreshProjects, reportError],
  );

  const deleteProject = useCallback(async () => {
    if (!activeProject) return;
    if (!window.confirm(`Delete project ${activeProject.project_name}?`)) return;
    await promptForgeApi.deleteProject(activeProject.project_id);
    setTabs([]);
    setActivePath(null);
    setEntries([]);
    await refreshProjects();
  }, [activeProject, refreshProjects]);

  const selectProject = useCallback((project: ProjectResponse) => {
    setActiveProject(project);
    setTabs([]);
    setActivePath(null);
    setEntries([]);
    restoredProjectRef.current = null;
  }, []);

  useEffect(() => {
    promptForgeApi
      .health()
      .then(setHealth)
      .catch((err: unknown) => {
        setHealth(null);
        setError(err instanceof Error ? err.message : "PromptForge API is unreachable");
      });
    refreshProjects()
      .then(() => Promise.allSettled([refreshBuildHistory(), refreshWorkspaceLogs()]))
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Projects could not be loaded");
      });
  }, [refreshBuildHistory, refreshProjects, refreshWorkspaceLogs]);

  useEffect(() => {
    if (!activeProject) {
      setEntries([]);
      return;
    }
    loadProjectFiles(activeProject.project_id).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Project files could not be loaded");
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
      void openFile(path).catch(() => {
        window.localStorage.removeItem(tabStorageKey(activeProject.project_id));
      });
    });
  }, [activeProject, openFile]);

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
    const refreshedProjects = await refreshProjects();
    const project = latestProject(refreshedProjects);
    if (project) {
      setActiveProject(project);
      await loadProjectFiles(project.project_id);
    } else {
      setEntries([]);
    }
    await Promise.allSettled([refreshBuildHistory(), refreshWorkspaceLogs()]);
  }, [loadProjectFiles, refreshBuildHistory, refreshProjects, refreshWorkspaceLogs]);

  const execute = useCallback(
    async (prompt: string) => {
      const generatedTaskId = `task-${crypto.randomUUID()}`;
      closeExecutionSocketRef.current?.();
      closeExecutionSocketRef.current = null;
      setTaskId(generatedTaskId);
      setExecutionId(null);
      setResult(null);
      setError(null);
      setIsExecuting(true);
      setStages(initialStages.map((stage) => ({ ...stage, status: "pending" as const })));
      addLog({ channel: "system", message: `Queued prompt execution ${generatedTaskId}` });

      const closeSocket = createExecutionSocket({
        taskId: generatedTaskId,
        onStateChange: (state) => {
          setSocketState(state === "error" ? "closed" : state);
        },
        onEvent: (event: ExecutionEvent) => {
          setExecutionId(event.execution_id);
          addLog({ channel: consoleChannel(event), message: eventMessage(event) });
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
          setStages((current) => settleActiveStages(current, workflowFailed(event)));
          setIsExecuting(false);
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
        .execute(prompt, generatedTaskId)
        .then((response) => {
          addLog({
            channel: "system",
            message: `Workflow start ${response.status.toLowerCase()}`,
          });
        })
        .catch((err: unknown) => {
          const message =
            err instanceof PromptForgeApiError || err instanceof Error
              ? err.message
              : "Workflow start request failed";
          addLog({ channel: "error", message });
        });
    },
    [
      addLog,
      refreshAfterWorkflow,
      reportError,
    ],
  );

  return {
    health,
    projects,
    activeProject,
    setActiveProject: selectProject,
    activeView,
    setActiveView,
    entries,
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
    refreshProjectFiles: () => (activeProject ? loadProjectFiles(activeProject.project_id) : Promise.resolve([])),
    refreshProjects,
    deleteProject,
    buildHistory,
    refreshBuildHistory,
    workspaceLogs,
    refreshWorkspaceLogs,
    stages,
    logs,
    clearLogs: () => setLogs([]),
    taskId,
    executionId,
    socketState,
    result,
    generatedProject: activeProject as unknown as Record<string, unknown> | null,
    buildResult: null as Record<string, unknown> | null,
    isExecuting,
    error,
    execute,
  };
}
