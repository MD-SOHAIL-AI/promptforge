"use client";

import { type PointerEvent as ReactPointerEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Bot, ChevronLeft, ChevronRight, PlugZap, Sparkles, Workflow } from "lucide-react";

import {
  ActivityBar,
  type ActivityId,
  activityDescriptions,
  activityIcons,
  activityLabels,
} from "@/components/ide/activity-bar";
import { AiAssistantPanel } from "@/components/ide/ai-assistant-panel";
import { BottomPanel } from "@/components/ide/bottom-panel";
import { DeviceToolsPanel } from "@/components/ide/device-tools-panel";
import { EditorWorkbench } from "@/components/ide/editor-workbench";
import { ProductAgentPanel } from "@/components/ide/product-agent-panel";
import { EmptyPanel } from "@/components/ide/empty-panel";
import { IdeExplorer } from "@/components/ide/ide-explorer";
import { IdeStatusBar } from "@/components/ide/status-bar";
import { TopCommandBar } from "@/components/ide/top-command-bar";
import { useForgeXDialogs } from "@/components/ide/dialogs/forgex-dialog-provider";
import { SettingsPage } from "@/components/settings/settings-page";
import type { SettingsCategory } from "@/components/settings/types";
import { useForgeXSettings } from "@/hooks/use-forgex-settings";
import { usePromptForgeWorkspace } from "@/hooks/use-promptforge-workspace";
import { promptForgeApi } from "@/lib/api";
import type { ModelProviderResponse, ModelRouteResponse } from "@/types";

type ResizeTarget = "explorer" | "ai" | "terminal";
type RightPanelTab = "forge" | "devices";
type ForgeMode = "agent" | "workflow";

const DEFAULT_EXPLORER_WIDTH = 286;
const DEFAULT_RIGHT_PANEL_WIDTH = 360;
const DEFAULT_TERMINAL_HEIGHT = 240;

function storedNumber(key: string, fallback: number) {
  if (typeof window === "undefined") return fallback;
  const value = Number(window.localStorage.getItem(key));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function safeArray<T>(value: T[] | undefined | null): T[] {
  return Array.isArray(value) ? value : [];
}

function boardIdFromProject(project: ReturnType<typeof usePromptForgeWorkspace>["activeProject"]) {
  if (!project) return null;
  const metadataBoard = project.metadata?.board;
  if (typeof metadataBoard === "string" && metadataBoard && metadataBoard !== "UNKNOWN") return metadataBoard;
  const platformio = project.metadata?.platformio;
  if (platformio && typeof platformio === "object" && "environments" in platformio) {
    const environments = (platformio as { environments?: Array<{ board?: string }> }).environments;
    const board = environments?.find((environment) => environment.board)?.board;
    if (board) return board;
  }
  return project.target_board !== "UNKNOWN" ? project.target_board : null;
}

function generationModeFromProject(project: ReturnType<typeof usePromptForgeWorkspace>["activeProject"]) {
  if (!project) return "new_project" as const;
  const hasPlatformIOIni = project.metadata?.has_platformio_ini !== false && project.project_type !== "generic";
  return hasPlatformIOIni ? "modify_existing_project" as const : "generate_into_open_folder" as const;
}

function boardIdFromDetected(board: { board_type: string } | null) {
  if (!board) return null;
  const normalized = board.board_type.toUpperCase();
  if (normalized === "ESP32") return "esp32dev";
  if (normalized === "ESP32-S3") return "esp32-s3-devkitc-1";
  if (normalized === "ESP32-C3") return "esp32-c3-devkitm-1";
  if (normalized === "ARDUINO_UNO" || normalized === "ARDUINO UNO") return "uno";
  return board.board_type;
}

export function ForgeXShell() {
  const dialogs = useForgeXDialogs();
  const workspace = usePromptForgeWorkspace();
  const settingsState = useForgeXSettings();
  const [activity, setActivity] = useState<ActivityId>("explorer");
  const [command, setCommand] = useState("");
  const [lastPrompt, setLastPrompt] = useState("");
  const [layoutReady, setLayoutReady] = useState(false);
  const [explorerWidth, setExplorerWidth] = useState(DEFAULT_EXPLORER_WIDTH);
  const [aiWidth, setAiWidth] = useState(DEFAULT_RIGHT_PANEL_WIDTH);
  const [terminalHeight, setTerminalHeight] = useState(DEFAULT_TERMINAL_HEIGHT);
  const [aiCollapsed, setAiCollapsed] = useState(false);
  const [terminalCollapsed, setTerminalCollapsed] = useState(false);
  const [terminalMaximized, setTerminalMaximized] = useState(false);
  const [rightPanelTab, setRightPanelTab] = useState<RightPanelTab>("forge");
  const [forgeMode, setForgeMode] = useState<ForgeMode>("agent");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsCategory, setSettingsCategory] = useState<SettingsCategory>("general");
  const [selectedBoardPort, setSelectedBoardPort] = useState<string | null>(null);
  const [modelProviders, setModelProviders] = useState<ModelProviderResponse[]>([]);
  const [modelRoutes, setModelRoutes] = useState<ModelRouteResponse[]>([]);
  const [viewportWidth, setViewportWidth] = useState(1440);
  const [viewportHeight, setViewportHeight] = useState(920);
  const [resizingTarget, setResizingTarget] = useState<ResizeTarget | null>(null);

  const safeProjects = safeArray(workspace.projects);
  const safeDetectedBoards = safeArray(workspace.detectedBoards);
  const safeEntries = safeArray(workspace.entries);
  const safeTabs = safeArray(workspace.tabs);
  const safeLogs = safeArray(workspace.logs);
  const safeWorkspaceLogs = safeArray(workspace.workspaceLogs);
  const safeStages = safeArray(workspace.stages);
  const safeModelProviders = safeArray(modelProviders);
  const safeModelRoutes = safeArray(modelRoutes);
  const compactSettingsLayout = settingsOpen && viewportWidth < 1180;
  const showExplorerPanel = !compactSettingsLayout;
  const showRightPanel = !(settingsOpen && viewportWidth < 1280);
  const terminalMaxHeight = Math.max(180, Math.floor(viewportHeight * 0.5));

  const activityIcon = useMemo(() => activityIcons[activity], [activity]);
  const selectedBoard = useMemo(
    () => safeDetectedBoards.find((board) => board.port === selectedBoardPort) ?? safeDetectedBoards[0] ?? null,
    [selectedBoardPort, safeDetectedBoards],
  );
  const selectedBoardId = boardIdFromProject(workspace.activeProject) ?? boardIdFromDetected(selectedBoard);
  const selectedBoardLabel = selectedBoard
    ? `${selectedBoardId ?? selectedBoard.board_type} ${selectedBoard.port}`
    : selectedBoardId ?? "No board";
  const codeRoute = safeModelRoutes.find((route) => route?.task_type === "code_generation");
  const routeProvider = safeModelProviders.find((provider) => provider?.provider_id === codeRoute?.provider_id);
  const routeModelId = typeof codeRoute?.model_id === "string" && codeRoute.model_id.trim() ? codeRoute.model_id : null;
  const routeProviderId = typeof codeRoute?.provider_id === "string" && codeRoute.provider_id.trim() ? codeRoute.provider_id : null;
  const modelRouteLabel = !routeModelId
    ? "Not configured"
    : routeProvider?.health_status === "offline" && routeProvider.local
      ? "Local offline"
      : routeProvider?.health_status === "error"
        ? "Provider error"
        : routeProvider?.health_status === "not_configured" || routeProvider?.configured === false
          ? "Not configured"
          : `${routeProvider?.display_name ?? routeProviderId ?? "Model"} / ${routeModelId.split("/").pop() ?? routeModelId}`;
  const editorSettings = useMemo(
    () => ({
      fontSize: Number(settingsState.settings["editor.font_size"] ?? 14),
      tabSize: Number(settingsState.settings["editor.tab_size"] ?? 2),
      wordWrap: Boolean(settingsState.settings["editor.word_wrap"]),
      minimapEnabled: Boolean(settingsState.settings["editor.minimap_enabled"]),
      showLineNumbers: settingsState.settings["editor.show_line_numbers"] !== false,
    }),
    [settingsState.settings],
  );

  const openSettings = useCallback((category: SettingsCategory = settingsCategory) => {
    setSettingsCategory(category);
    setSettingsOpen(true);
  }, [settingsCategory]);

  const refreshModelRouter = useCallback(async () => {
    try {
      const [providers, routes] = await Promise.all([
        promptForgeApi.modelProviders(),
        promptForgeApi.modelRoutes(),
      ]);
      setModelProviders(safeArray(providers?.providers));
      setModelRoutes(safeArray(routes?.routes));
    } catch {
      setModelProviders([]);
      setModelRoutes([]);
    }
  }, []);

  useEffect(() => {
    void refreshModelRouter();
    const timer = window.setInterval(() => void refreshModelRouter(), 15_000);
    const refreshOnFocus = () => void refreshModelRouter();
    window.addEventListener("focus", refreshOnFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshOnFocus);
    };
  }, [refreshModelRouter]);

  useEffect(() => {
    if (!selectedBoardPort && safeDetectedBoards[0]) {
      setSelectedBoardPort(safeDetectedBoards[0].port);
    }
  }, [selectedBoardPort, safeDetectedBoards]);

  useEffect(() => {
    setExplorerWidth(Math.round(storedNumber("forgex.layout.explorerWidth", DEFAULT_EXPLORER_WIDTH)));
    setAiWidth(Math.round(storedNumber("forgex.layout.aiWidth", DEFAULT_RIGHT_PANEL_WIDTH)));
    setTerminalHeight(Math.round(storedNumber("forgex.layout.terminalHeight", DEFAULT_TERMINAL_HEIGHT)));
    setAiCollapsed(window.localStorage.getItem("forgex.layout.aiCollapsed") === "true");
    setTerminalCollapsed(window.localStorage.getItem("forgex.layout.terminalCollapsed") === "true");
    setTerminalMaximized(window.localStorage.getItem("forgex.layout.terminalMaximized") === "true");
    setViewportWidth(window.innerWidth);
    setViewportHeight(window.innerHeight);
    setLayoutReady(true);
  }, []);

  useEffect(() => {
    const handleResize = () => {
      setViewportWidth(window.innerWidth);
      setViewportHeight(window.innerHeight);
    };
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    setTerminalHeight((current) => clamp(current, 120, terminalMaxHeight));
  }, [terminalMaxHeight]);

  useEffect(() => {
    if (!layoutReady) return;
    window.localStorage.setItem("forgex.layout.explorerWidth", String(explorerWidth));
  }, [explorerWidth, layoutReady]);

  useEffect(() => {
    if (!layoutReady) return;
    window.localStorage.setItem("forgex.layout.aiWidth", String(aiWidth));
  }, [aiWidth, layoutReady]);

  useEffect(() => {
    if (!layoutReady) return;
    window.localStorage.setItem("forgex.layout.terminalHeight", String(terminalHeight));
  }, [terminalHeight, layoutReady]);

  useEffect(() => {
    if (!layoutReady) return;
    window.localStorage.setItem("forgex.layout.aiCollapsed", String(aiCollapsed));
  }, [aiCollapsed, layoutReady]);

  useEffect(() => {
    if (!layoutReady) return;
    window.localStorage.setItem("forgex.layout.terminalCollapsed", String(terminalCollapsed));
  }, [terminalCollapsed, layoutReady]);

  useEffect(() => {
    if (!layoutReady) return;
    window.localStorage.setItem("forgex.layout.terminalMaximized", String(terminalMaximized));
  }, [terminalMaximized, layoutReady]);

  const executePrompt = (prompt: string) => {
    const value = prompt.trim();
    if (!value || workspace.isExecuting) return;
    setLastPrompt(value);
    setCommand("");
    void workspace.execute(value, {
      selectedBoard: selectedBoardId,
      selectedFramework: "PlatformIO",
      generationMode: generationModeFromProject(workspace.activeProject),
    });
  };

  const startResize = useCallback(
    (target: ResizeTarget, event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      setResizingTarget(target);
      document.body.style.cursor = target === "terminal" ? "row-resize" : "col-resize";
      document.body.style.userSelect = "none";
      const startX = event.clientX;
      const startY = event.clientY;
      const initialExplorer = explorerWidth;
      const initialAi = aiWidth;
      const initialTerminal = terminalHeight;

      const move = (moveEvent: PointerEvent) => {
        if (target === "explorer") {
          setExplorerWidth(clamp(initialExplorer + moveEvent.clientX - startX, 220, 440));
        }
        if (target === "ai") {
          setAiWidth(clamp(initialAi - (moveEvent.clientX - startX), 320, 520));
        }
        if (target === "terminal") {
          setTerminalMaximized(false);
          setTerminalHeight(clamp(initialTerminal - (moveEvent.clientY - startY), 120, terminalMaxHeight));
        }
      };
      const up = () => {
        setResizingTarget(null);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    },
    [aiWidth, explorerWidth, terminalHeight, terminalMaxHeight],
  );

  const toggleTerminalMaximized = useCallback(() => {
    setTerminalCollapsed(false);
    setTerminalMaximized((current) => {
      const next = !current;
      setTerminalHeight(next ? terminalMaxHeight : DEFAULT_TERMINAL_HEIGHT);
      return next;
    });
  }, [terminalMaxHeight]);

  const openFolder = async () => {
    if (!window.forgexDesktop?.openFolder) {
      await dialogs.message({
        title: "Desktop app required",
        description: "Open Folder is available in the Electron desktop app.",
      });
      return;
    }
    const result = await window.forgexDesktop.openFolder();
    if (!result.canceled && result.path) {
      await workspace.importProjectPath(result.path);
    }
  };

  const openProject = async () => {
    if (!window.forgexDesktop?.openProject) {
      await dialogs.message({
        title: "Desktop app required",
        description: "Open Project is available in the Electron desktop app.",
      });
      return;
    }
    const result = await window.forgexDesktop.openProject();
    if (!result.canceled && result.path) {
      await workspace.importProjectPath(result.path);
    }
  };

  return (
    <main className="flex h-dvh min-w-0 flex-col overflow-hidden" style={{ backgroundColor: "var(--fx-bg)", color: "var(--fx-text)" }}>
      <TopCommandBar
        health={workspace.health}
        projects={safeProjects}
        activeProject={workspace.activeProject}
        selectedBoardLabel={selectedBoardLabel}
        command={command}
        isExecuting={workspace.isExecuting}
        toolAction={workspace.toolAction}
        socketState={workspace.socketState}
        modelRouteLabel={modelRouteLabel}
        onCommandChange={setCommand}
        onSubmitCommand={() => executePrompt(command)}
        onRunBuild={() => void workspace.buildActiveProject()}
        onFlash={() => void workspace.flashActiveProject(selectedBoard)}
        onMonitor={() => void workspace.startSerialMonitor(selectedBoard)}
        onOpenFolder={() => void openFolder()}
        onOpenProject={() => void openProject()}
        onOpenModels={() => {
          openSettings("models");
        }}
        onOpenSettings={() => openSettings()}
        onSelectProject={workspace.setActiveProject}
      />

      <div
        className={`fx-ide-grid grid min-h-0 min-w-0 flex-1 ${resizingTarget ? "fx-resizing" : ""}`}
        style={{
          gridTemplateColumns: `52px ${showExplorerPanel ? Math.round(explorerWidth) : 0}px ${showExplorerPanel ? 4 : 0}px minmax(0, 1fr) ${showRightPanel && !aiCollapsed ? 4 : 0}px ${showRightPanel ? (aiCollapsed ? 44 : Math.round(aiWidth)) : 0}px`,
        }}
      >
        <ActivityBar active={activity} onChange={setActivity} onOpenSettings={() => openSettings()} />
        {showExplorerPanel && activity === "explorer" ? (
          <IdeExplorer
            activeProject={workspace.activeProject}
            entries={safeEntries}
            selectedPath={workspace.activePath}
            revealedPaths={workspace.recentlyAddedPaths}
            onOpenFile={workspace.openFile}
            onCreateFile={(basePath) => void workspace.createEntry("file", basePath)}
            onCreateFolder={(basePath) => void workspace.createEntry("folder", basePath)}
            onRename={workspace.renameEntry}
            onDelete={workspace.deleteEntry}
            onRefresh={() => void workspace.refreshProjectFiles()}
          />
        ) : showExplorerPanel ? (
          <EmptyPanel
            icon={activityIcon}
            title={activityLabels[activity]}
            description={activityDescriptions[activity]}
          />
        ) : (
          <div className="min-w-0 overflow-hidden" />
        )}

        {showExplorerPanel ? (
          <div
            className="cursor-col-resize bg-[var(--fx-border-soft)] transition hover:bg-[var(--fx-accent)]/50"
            onPointerDown={(event) => startResize("explorer", event)}
            title="Resize explorer"
          />
        ) : (
          <div />
        )}

        <section className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          {settingsOpen ? (
            <SettingsPage
              selectedCategory={settingsCategory}
              providers={safeModelProviders}
              routes={safeModelRoutes}
              activeProject={workspace.activeProject}
              health={workspace.health}
              settingsState={settingsState}
              onSelectCategory={setSettingsCategory}
              onRefreshModelRouter={refreshModelRouter}
              onLog={workspace.addLog}
              onClose={() => setSettingsOpen(false)}
            />
          ) : (
            <EditorWorkbench
              activeProject={workspace.activeProject}
              selectedFile={workspace.selectedFile}
              tabs={safeTabs}
              activePath={workspace.activePath}
              onSelectTab={workspace.openFile}
              onCloseTab={workspace.closeTab}
              onChange={workspace.updateTabContent}
              onSave={(path) => void workspace.saveFile(path)}
              editorSettings={editorSettings}
            />
          )}
          <div
            className="h-1 cursor-row-resize bg-[var(--fx-border-soft)] transition hover:bg-[var(--fx-accent)]/50"
            onPointerDown={(event) => startResize("terminal", event)}
            title="Resize terminal"
          />
          <BottomPanel
            logs={safeLogs}
            workspaceLogs={safeWorkspaceLogs}
            stages={safeStages}
            activeProject={workspace.activeProject}
            height={terminalHeight}
            collapsed={terminalCollapsed}
            maximized={terminalMaximized}
            onToggleCollapsed={() => setTerminalCollapsed((value) => !value)}
            onToggleMaximized={toggleTerminalMaximized}
            onClearLogs={workspace.clearLogs}
          />
        </section>

        {showRightPanel && !aiCollapsed ? (
          <div
            className="cursor-col-resize bg-[var(--fx-border-soft)] transition hover:bg-[var(--fx-accent)]/50"
            onPointerDown={(event) => startResize("ai", event)}
            title="Resize Forge"
          />
        ) : (
          <div />
        )}

        {showRightPanel ? (
          <section className="flex min-h-0 min-w-0 flex-col border-l border-[var(--fx-border)] bg-[var(--fx-panel)]">
          {aiCollapsed ? (
            <div className="flex min-h-0 flex-1 flex-col items-center py-2">
              <button
                className="flex h-9 w-9 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
                title="Expand right panel"
                onClick={() => setAiCollapsed(false)}
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <Bot className="mt-3 h-5 w-5 text-[var(--fx-accent)]" />
              <PlugZap className="mt-4 h-5 w-5 text-[var(--fx-success)]" />
            </div>
          ) : (
            <>
              <div className="flex h-10 shrink-0 items-center justify-between border-b border-[var(--fx-border)] px-2">
                <div className="flex min-w-0 items-center gap-1">
                  {[
                    { id: "forge" as const, label: "Forge", icon: Bot },
                    { id: "devices" as const, label: "Device Tools", icon: PlugZap },
                  ].map((tab) => {
                    const Icon = tab.icon;
                    const active = rightPanelTab === tab.id;
                    return (
                      <button
                        key={tab.id}
                        className={`flex h-7 min-w-0 items-center gap-1.5 rounded px-2 text-xs ${
                          active ? "bg-[var(--fx-input)] text-[var(--fx-text)]" : "text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
                        }`}
                        onClick={() => setRightPanelTab(tab.id)}
                        title={tab.label}
                      >
                        <Icon className="h-3.5 w-3.5 shrink-0" />
                        <span className="truncate">{tab.label}</span>
                      </button>
                    );
                  })}
                </div>
                <button
                  className="ml-2 flex h-7 w-7 shrink-0 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
                  onClick={() => setAiCollapsed(true)}
                  title="Collapse right panel"
                  aria-label="Collapse right panel"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
              </div>

              <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
                {rightPanelTab === "forge" ? (
                  <div className="flex h-full min-h-0 flex-col">
                    <div className="shrink-0 border-b border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-2">
                      <div className="grid grid-cols-2 rounded-lg bg-[var(--fx-input)] p-1" aria-label="Forge mode">
                        {[
                          { id: "agent" as const, label: "Agent", icon: Sparkles, hint: "Change project files" },
                          { id: "workflow" as const, label: "Build", icon: Workflow, hint: "Generate, build and flash" },
                        ].map((mode) => {
                          const Icon = mode.icon;
                          const active = forgeMode === mode.id;
                          return (
                            <button
                              key={mode.id}
                              type="button"
                              className={`flex min-w-0 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] font-medium transition ${
                                active
                                  ? "bg-[var(--fx-panel-elevated)] text-[var(--fx-text)] shadow-sm"
                                  : "text-[var(--fx-text-muted)] hover:text-[var(--fx-text)]"
                              }`}
                              onClick={() => setForgeMode(mode.id)}
                              title={mode.hint}
                            >
                              <Icon className={`h-3.5 w-3.5 ${active ? "text-[var(--fx-accent)]" : ""}`} />
                              {mode.label}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                    <div className="min-h-0 flex-1 overflow-hidden">
                      {forgeMode === "agent" ? (
                        <ProductAgentPanel
                          projectId={workspace.activeProject?.project_id ?? null}
                          projectName={workspace.activeProject?.project_name ?? null}
                          onOpenReview={(reviewId) => {
                            window.sessionStorage.setItem("forgex.pendingBridgeReviewId", reviewId);
                            openSettings("models");
                          }}
                          onWorkspaceChanged={() => void workspace.refreshProjectFiles()}
                          embedded
                        />
                      ) : (
                        <AiAssistantPanel
                          stages={safeStages}
                          isExecuting={workspace.isExecuting}
                          isCancelling={workspace.isCancelling}
                          socketState={workspace.socketState}
                          generationProgress={workspace.generationProgress}
                          lastPrompt={lastPrompt}
                          trace={safeLogs}
                          workspaceNeedsInitialization={workspace.activeProject?.project_type === "generic" || workspace.activeProject?.metadata?.has_platformio_ini === false}
                          collapsed={false}
                          onToggleCollapsed={() => setAiCollapsed((value) => !value)}
                          onExecute={executePrompt}
                          onCancel={() => void workspace.cancelExecution()}
                          embedded
                        />
                      )}
                    </div>
                  </div>
                ) : (
                  <DeviceToolsPanel
                    activeProject={workspace.activeProject}
                    health={workspace.health}
                    boards={safeDetectedBoards}
                    selectedBoard={selectedBoard}
                    selectedBoardPort={selectedBoard?.port ?? null}
                    monitorStatus={workspace.monitorStatus}
                    toolAction={workspace.toolAction}
                    onSelectBoard={setSelectedBoardPort}
                    onRefreshBoards={() => void workspace.refreshDetectedBoards()}
                    onBuild={() => void workspace.buildActiveProject()}
                    onFlash={() => void workspace.flashActiveProject(selectedBoard)}
                    onMonitor={() => void workspace.startSerialMonitor(selectedBoard)}
                    onStopMonitor={() => void workspace.stopSerialMonitor()}
                    onImportProject={() => void openProject()}
                    embedded
                  />
                )}
              </div>
            </>
          )}
          </section>
        ) : (
          <div className="min-w-0 overflow-hidden" />
        )}
      </div>

      <IdeStatusBar
        activeProject={workspace.activeProject}
        selectedFile={workspace.selectedFile}
        tabs={safeTabs}
        logs={safeLogs}
        socketState={workspace.socketState}
        isExecuting={workspace.isExecuting}
      />
    </main>
  );
}
