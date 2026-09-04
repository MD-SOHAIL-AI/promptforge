"use client";

import { CircuitBoard, ChevronDown, Code2, Command, Home, Radio, Search, Settings, Sparkles, TerminalSquare } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CommandCenter } from "@/components/nexus/command-center";
import { ForgeCore, type ForgeCoreState } from "@/components/nexus/forge-core";
import { NexusHome } from "@/components/nexus/nexus-home";
import { TaskCanvas } from "@/components/nexus/task-canvas";
import { TaskCenter } from "@/components/nexus/task-center";
import { UniversalComposer, type ForgeMode } from "@/components/nexus/universal-composer";
import { BuildCanvas, CodeCanvas, HardwareCanvas, ReviewCanvas, SerialCanvas, TerminalCanvas, type NexusCanvas } from "@/components/nexus/work-canvases";
import { useForgeXDialogs } from "@/components/ide/dialogs/forgex-dialog-provider";
import { FxOverlay } from "@/components/ui/fx-overlay";
import { LiveRegion, announce } from "@/components/ui/live-region";
import { SettingsPage } from "@/components/settings/settings-page";
import type { SettingsCategory } from "@/components/settings/types";
import { useForgeAgentSession } from "@/hooks/use-forge-agent-session";
import { useForgeXSettings } from "@/hooks/use-forgex-settings";
import { usePromptForgeWorkspace } from "@/hooks/use-promptforge-workspace";
import { promptForgeApi } from "@/lib/api";
import { formatCombo, useShortcuts, type ShortcutDefinition } from "@/lib/shortcuts";
import type { DetectedBoard, HealthResponse, ModelProviderResponse, ModelRouteResponse, ProjectResponse, ProviderModelsResponse } from "@/types";

type NexusView = "home" | "task" | "settings" | Exclude<NexusCanvas, "task">;

const ALL_VIEWS = ["home", "task", "code", "build", "review", "hardware", "serial", "terminal", "settings"] as const satisfies readonly NexusView[];

const NUMBERED_VIEWS = ["home", "task", "code", "hardware", "serial", "build", "review", "terminal"] as const satisfies readonly NexusView[];

function viewFromHash(hash: string): NexusView | null {
  const id = hash.replace(/^#/, "");
  return (ALL_VIEWS as readonly string[]).includes(id) ? (id as NexusView) : null;
}

function boardIdFromProject(project: ProjectResponse | null) {
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

function boardIdFromDetected(board: DetectedBoard | null) {
  if (!board) return null;
  const normalized = board.board_type.toUpperCase();
  if (normalized === "ESP32") return "esp32dev";
  if (normalized === "ESP32-S3") return "esp32-s3-devkitc-1";
  if (normalized === "ESP32-C3") return "esp32-c3-devkitm-1";
  if (normalized === "ARDUINO_UNO" || normalized === "ARDUINO UNO") return "uno";
  return board.board_type;
}

function coreState(enabled: boolean, runStatus?: string, active?: string | null): ForgeCoreState {
  if (!enabled) return "offline";
  if (["failed", "blocked", "timed_out"].includes(runStatus ?? "")) return "error";
  if (runStatus === "awaiting_flash_confirmation") return "waiting";
  if (runStatus === "completed") return "complete";
  if (active?.toLowerCase().includes("build")) return "building";
  if (["running", "queued", "cancelling"].includes(runStatus ?? "")) return "working";
  return "idle";
}

const navItems = [
  { id: "home" as const, label: "Home", icon: Home },
  { id: "task" as const, label: "Forge", icon: Sparkles },
  { id: "code" as const, label: "Workspace", icon: Code2 },
  { id: "hardware" as const, label: "Hardware", icon: CircuitBoard },
  { id: "serial" as const, label: "Live device", icon: Radio },
];

export function NexusShell() {
  const dialogs = useForgeXDialogs();
  const workspace = usePromptForgeWorkspace();
  const settingsState = useForgeXSettings();
  const agent = useForgeAgentSession(workspace.activeProject?.project_id ?? null);
  const [view, setView] = useState<NexusView>("home");
  const [selectedBoardPort, setSelectedBoardPort] = useState<string | null>(null);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [quickOpen, setQuickOpen] = useState(false);
  const [quickMode, setQuickMode] = useState<ForgeMode>("auto");
  const [focusMode, setFocusMode] = useState(false);
  const [settingsCategory, setSettingsCategory] = useState<SettingsCategory>("general");
  const [modelProviders, setModelProviders] = useState<ModelProviderResponse[]>([]);
  const [modelRoutes, setModelRoutes] = useState<ModelRouteResponse[]>([]);
  const [connection, setConnection] = useState<HealthResponse | null>(null);
  const [checkingHealth, setCheckingHealth] = useState(false);

  const selectedBoard = useMemo(() => workspace.detectedBoards.find((board) => board.port === selectedBoardPort) ?? workspace.detectedBoards[0] ?? null, [selectedBoardPort, workspace.detectedBoards]);
  const selectedBoardId = boardIdFromProject(workspace.activeProject) ?? boardIdFromDetected(selectedBoard);
  const selectedBoardLabel = selectedBoard ? `${selectedBoard.board_type} · ${selectedBoard.port}` : selectedBoardId ?? "No device";
  const activeEnvironment = typeof workspace.activeProject?.metadata?.active_environment === "string" ? workspace.activeProject.metadata.active_environment : null;
  const running = ["running", "queued", "cancelling"].includes(agent.run?.status ?? "");
  const agentCore = coreState(agent.enabled, agent.run?.status, agent.currentActivity?.label);
  const activeModelRoute = useMemo(() => modelRoutes.find((route) => route.task_type === "code_generation") ?? null, [modelRoutes]);

  const refreshModelRouter = useCallback(async () => {
    try {
      const [providers, routes] = await Promise.all([promptForgeApi.modelProviders(), promptForgeApi.modelRoutes()]);
      setModelProviders(Array.isArray(providers.providers) ? providers.providers : []);
      setModelRoutes(Array.isArray(routes.routes) ? routes.routes : []);
    } catch {
      setModelProviders([]);
      setModelRoutes([]);
    }
  }, []);

  useEffect(() => { void refreshModelRouter(); }, [refreshModelRouter]);
  useEffect(() => { if (!selectedBoardPort && workspace.detectedBoards[0]) setSelectedBoardPort(workspace.detectedBoards[0].port); }, [selectedBoardPort, workspace.detectedBoards]);

  const loadProviderModels = useCallback((providerId: string): Promise<ProviderModelsResponse> => promptForgeApi.providerModels(providerId), []);

  const changeChatModel = useCallback(async (providerId: string, modelId: string) => {
    const current = modelRoutes.find((route) => route.task_type === "code_generation");
    const result = await promptForgeApi.saveModelSelection({
      provider_id: providerId,
      model_id: modelId,
      fallback_enabled: current?.fallback_enabled ?? true,
    });
    const nextRoute: ModelRouteResponse = {
      task_type: "code_generation",
      provider_id: result.selection.provider_id,
      model_id: result.selection.model_id,
      fallback_enabled: result.selection.fallback_enabled,
      fallback_provider_id: result.selection.fallback_provider_id,
      local_only: result.selection.local_only,
    };
    setModelRoutes((currentRoutes) => [nextRoute, ...currentRoutes.filter((route) => route.task_type !== "code_generation")]);
    await refreshModelRouter();
  }, [modelRoutes, refreshModelRouter]);

  const viewRef = useRef<NexusView>("home");
  useEffect(() => { viewRef.current = view; }, [view]);

  useEffect(() => {
    const applyHash = () => {
      const next = viewFromHash(window.location.hash);
      if (next && next !== viewRef.current) setView(next);
    };
    applyHash();
    window.addEventListener("hashchange", applyHash);
    return () => window.removeEventListener("hashchange", applyHash);
  }, []);

  useEffect(() => {
    if (window.location.hash !== `#${view}`) window.history.replaceState(null, "", `#${view}`);
  }, [view]);

  const toggleQuickForge = useCallback(() => setQuickOpen((value) => !value), []);
  const toggleFocusMode = useCallback(() => {
    setFocusMode((value) => {
      announce(value ? "Focus mode off" : "Focus mode on");
      return !value;
    });
  }, []);

  const shortcuts = useMemo<ShortcutDefinition[]>(() => [
    { id: "command-palette", combo: "mod+k", allowInInput: true, description: "Open command palette", handler: () => setCommandOpen(true) },
    { id: "quick-forge", combo: "alt+space", allowInInput: true, description: "Toggle Quick Forge HUD", handler: toggleQuickForge },
    { id: "focus-mode", combo: "mod+shift+f", allowInInput: true, description: "Toggle focus mode (hide chrome)", handler: toggleFocusMode },
    ...NUMBERED_VIEWS.map((target, index) => ({
      id: `view-${target}`,
      combo: `mod+${index + 1}`,
      description: `Go to ${target} view`,
      handler: () => setView(target),
    })),
    { id: "open-settings", combo: "mod+,", description: "Open settings", handler: () => setView("settings") },
    {
      id: "close-layer",
      combo: "escape",
      allowInInput: true,
      description: "Close topmost layer or exit focus mode",
      handler: () => {
        if (commandOpen) setCommandOpen(false);
        else if (quickOpen) setQuickOpen(false);
        else if (tasksOpen) setTasksOpen(false);
        else if (focusMode) setFocusMode(false);
      },
    },
  ], [commandOpen, focusMode, quickOpen, tasksOpen, toggleFocusMode, toggleQuickForge]);
  useShortcuts(shortcuts);

  useEffect(() => { if (view !== "task") setFocusMode(false); }, [view]);

  const recheckConnection = useCallback(async () => {
    setCheckingHealth(true);
    announce("Checking backend connection…");
    try {
      const health = await promptForgeApi.health();
      setConnection(health);
      announce(`Backend online · ${health.status} · v${health.version}`);
    } catch {
      setConnection(null);
      announce("Backend unreachable");
    } finally {
      setCheckingHealth(false);
    }
  }, []);

  const openProject = useCallback(async () => {
    if (!window.forgexDesktop?.openProject) {
      await dialogs.message({ title: "Desktop app required", description: "Open Project is available in the ForgeX desktop app." });
      return;
    }
    const result = await window.forgexDesktop.openProject();
    if (!result.canceled && result.path) {
      await workspace.importProjectPath(result.path);
      setView("home");
    }
  }, [dialogs, workspace]);

  const setCanvas = (canvas: NexusCanvas) => {
    if (canvas === "task") setView("task");
    else setView(canvas);
  };

  const backToTask = () => setView("task");
  const openCanvas = (canvas: NexusCanvas) => setCanvas(canvas);

  const sendForge = useCallback(async (prompt: string, mode: ForgeMode = "auto") => {
    if (!workspace.activeProject) return;
    setView("task");
    await agent.sendMessage(prompt, undefined, {
      autonomy: mode === "plan" ? "plan_only" : mode === "ask" ? "staged_changes" : "auto",
      boardPort: selectedBoard?.port ?? null,
      boardType: selectedBoardId,
      environment: activeEnvironment,
    });
  }, [activeEnvironment, agent, selectedBoard, selectedBoardId, workspace.activeProject]);

  const beginTask = (prompt?: string) => {
    setView("task");
    if (prompt) void sendForge(prompt);
  };

  const navigate = (target: "home" | "task" | "settings") => setView(target);

  const healthSnapshot = connection ?? workspace.health;
  const healthy = Boolean(healthSnapshot);
  const healthDetail = healthSnapshot
    ? `${healthSnapshot.status} · v${healthSnapshot.version} · workflow ${healthSnapshot.services.workflow ? "ok" : "down"} · planner ${healthSnapshot.services.planner ? "ok" : "down"}`
    : "Backend unreachable — click to retry";
  const footerChip = "flex items-center gap-1.5 rounded-md px-1.5 py-0.5 transition-colors hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]";

  return (
    <main className="fx-nexus-shell flex h-dvh min-w-0 flex-col overflow-hidden text-[var(--fx-text)]">
      <header className={`fx-nexus-global-header ${focusMode ? "hidden" : "flex"} h-12 shrink-0 items-center gap-3 border-b border-[var(--fx-border-soft)] px-3 md:px-4`}>
        <button onClick={() => setView("home")} aria-label="ForgeX home" className="flex shrink-0 items-center gap-2 rounded-xl pr-2 text-[var(--fx-text)]"><span className="grid h-7 w-7 place-items-center rounded-[10px] bg-[var(--fx-accent)] text-white shadow-[0_0_20px_var(--fx-glow)]"><Sparkles className="h-3.5 w-3.5" aria-hidden="true" /></span><span className="hidden text-[12px] font-bold tracking-[-.02em] sm:inline">ForgeX</span></button>

        <div className="hidden h-5 w-px bg-[var(--fx-border-soft)] sm:block" />
        <span className="relative inline-flex min-w-0 max-w-[190px] items-center">
          <select aria-label="Active project" value={workspace.activeProject?.project_id ?? ""} onChange={(event) => { const project = workspace.projects.find((item) => item.project_id === event.target.value); if (project) workspace.setActiveProject(project); }} className="w-full min-w-0 cursor-pointer appearance-none truncate rounded-lg border border-[var(--fx-border-soft)] bg-[var(--fx-input)] py-1 pl-2 pr-6 text-[10px] font-medium text-[var(--fx-text)] outline-none transition-colors hover:border-[var(--fx-border)] hover:text-[var(--fx-text)] focus-visible:border-[var(--fx-accent)]">
            {!workspace.activeProject ? <option value="">No project</option> : null}
            {workspace.projects.map((project) => <option key={project.project_id} value={project.project_id}>{project.project_name}</option>)}
          </select>
          <ChevronDown className="pointer-events-none absolute right-1.5 h-3 w-3 text-[var(--fx-text-muted)]" aria-hidden="true" />
        </span>

        <button onClick={() => setCommandOpen(true)} className="mx-auto hidden h-8 w-full max-w-[460px] items-center gap-2 rounded-xl border border-[var(--fx-border-soft)] bg-[color-mix(in_srgb,var(--fx-panel)_64%,transparent)] px-3 text-[10px] text-[var(--fx-text-muted)] hover:border-[var(--fx-border)] hover:bg-[var(--fx-hover)] md:flex"><Search className="h-3.5 w-3.5" aria-hidden="true" /><span className="min-w-0 flex-1 text-left">Search, command or ask Forge…</span><span className="rounded border border-[var(--fx-border-soft)] px-1.5 py-0.5 font-mono text-[8px]">{formatCombo("mod+k")}</span></button>

        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          <button onClick={() => setView("hardware")} aria-label={`Device ${selectedBoardLabel} — open hardware view`} className={`hidden items-center gap-2 rounded-xl border px-2.5 py-1.5 text-[9px] sm:flex ${selectedBoard ? "border-[color-mix(in_srgb,var(--fx-info)_28%,var(--fx-border))] bg-[color-mix(in_srgb,var(--fx-info)_6%,transparent)] text-[var(--fx-text)]" : "border-[var(--fx-border-soft)] text-[var(--fx-text-muted)]"}`}><span className={`h-1.5 w-1.5 rounded-full ${selectedBoard ? "bg-[var(--fx-success)] shadow-[0_0_8px_var(--fx-success)]" : "bg-[var(--fx-text-muted)]"}`} /><span className="max-w-[135px] truncate">{selectedBoardLabel}</span></button>
          <button onClick={() => setView("task")} aria-label="Forge task view" className="grid h-8 w-8 place-items-center rounded-xl hover:bg-[var(--fx-hover)]" title="Forge"><ForgeCore state={agentCore} size="sm" label={false} /></button>
        </div>
      </header>

      <div className="flex min-h-0 min-w-0 flex-1">
        <nav aria-label="Primary" className={`fx-nexus-nav ${focusMode ? "hidden" : "flex"} w-[54px] shrink-0 flex-col items-center border-r border-[var(--fx-border-soft)] py-2`}>
          <div className="space-y-1">{navItems.map((item, index) => { const Icon = item.icon; const active = view === item.id; return <button key={item.id} onClick={() => setView(item.id)} aria-label={item.label} aria-current={active ? "page" : undefined} className={`fx-nexus-nav-button ${active ? "is-active" : ""}`} title={`${item.label} · ${formatCombo(`mod+${index + 1}`)}`}><Icon className="h-4 w-4" aria-hidden="true" /></button>; })}</div>
          <div className="mt-auto space-y-1"><button onClick={() => setCommandOpen(true)} className="fx-nexus-nav-button md:hidden" title="Command center" aria-label="Command center"><Command className="h-4 w-4" aria-hidden="true" /></button><button onClick={() => setView("terminal")} aria-label="Console" aria-current={view === "terminal" ? "page" : undefined} className={`fx-nexus-nav-button ${view === "terminal" ? "is-active" : ""}`} title={`Console · ${formatCombo("mod+8")}`}><TerminalSquare className="h-4 w-4" aria-hidden="true" /></button><button onClick={() => setView("settings")} aria-label="Settings" aria-current={view === "settings" ? "page" : undefined} className={`fx-nexus-nav-button ${view === "settings" ? "is-active" : ""}`} title={`Settings · ${formatCombo("mod+,")}`}><Settings className="h-4 w-4" aria-hidden="true" /></button></div>
        </nav>

        <section className="fx-nexus-canvas relative min-h-0 min-w-0 flex-1 overflow-hidden">
          {view === "home" ? <NexusHome agent={agent} workspace={workspace} selectedBoardLabel={selectedBoardLabel} modelProviders={modelProviders} modelRoute={activeModelRoute} loadProviderModels={loadProviderModels} onModelChange={changeChatModel} onBeginTask={beginTask} onOpenProject={() => void openProject()} onOpenCode={() => setView("code")} onOpenHardware={() => setView("hardware")} /> : null}
          {view === "task" ? <TaskCanvas agent={agent} projectName={workspace.activeProject?.project_name ?? null} selectedBoard={selectedBoard} selectedBoardId={selectedBoardId} activeEnvironment={activeEnvironment} onOpenCanvas={openCanvas} onOpenSessions={() => setTasksOpen(true)} onWorkspaceChanged={() => void workspace.refreshProjectFiles()} modelProviders={modelProviders} modelRoute={activeModelRoute} loadProviderModels={loadProviderModels} onModelChange={changeChatModel} focused={focusMode} onFocusToggle={() => setFocusMode((value) => !value)} /> : null}
          {view === "code" ? <CodeCanvas workspace={workspace} onBack={backToTask} /> : null}
          {view === "build" ? <BuildCanvas workspace={workspace} run={agent.run} onBack={backToTask} onOpenTerminal={() => setView("terminal")} onFlash={() => agent.run?.status === "awaiting_flash_confirmation" ? void agent.confirmFlash({ port: selectedBoard?.port, boardType: selectedBoardId, environment: activeEnvironment }) : selectedBoard ? void workspace.flashActiveProject(selectedBoard) : undefined} /> : null}
          {view === "review" ? <ReviewCanvas run={agent.run} onBack={backToTask} onApply={() => agent.applyChanges()} onApplied={() => { void workspace.refreshProjectFiles(); setView("task"); }} /> : null}
          {view === "hardware" ? <HardwareCanvas workspace={workspace} selectedBoard={selectedBoard} onSelectBoard={setSelectedBoardPort} onBack={() => setView("home")} /> : null}
          {view === "serial" ? <SerialCanvas workspace={workspace} selectedBoard={selectedBoard} onBack={() => setView("hardware")} /> : null}
          {view === "terminal" ? <TerminalCanvas workspace={workspace} onBack={backToTask} /> : null}
          {view === "settings" ? <SettingsPage selectedCategory={settingsCategory} providers={modelProviders} routes={modelRoutes} activeProject={workspace.activeProject} health={workspace.health} settingsState={settingsState} onSelectCategory={setSettingsCategory} onRefreshModelRouter={refreshModelRouter} onLog={workspace.addLog} onClose={() => setView("home")} /> : null}
        </section>
      </div>

      <footer className={`fx-nexus-status ${focusMode ? "hidden" : "flex"} h-6 shrink-0 items-center gap-1 border-t border-[var(--fx-border-soft)] px-3 font-mono text-[11px] text-[var(--fx-text-muted)]`}>
        <button type="button" onClick={() => void recheckConnection()} disabled={checkingHealth} title={healthDetail} aria-label={`Backend connection: ${healthy ? "ready" : "offline"} — re-check now`} className={footerChip}>
          <span className={`h-1.5 w-1.5 rounded-full ${healthy ? "bg-[var(--fx-success)]" : "bg-[var(--fx-error)]"} ${checkingHealth ? "animate-pulse" : ""}`} aria-hidden="true" />
          {checkingHealth ? "checking…" : healthy ? "ready" : "backend offline"}
        </button>
        <button type="button" onClick={() => setView("hardware")} title={`Target board — open hardware · ${formatCombo("mod+4")}`} aria-label={`Target board ${workspace.activeProject?.target_board ?? "none"} — open hardware view`} className={footerChip}>{workspace.activeProject?.target_board ?? "no target"}</button>
        {workspace.activeProject ? <button type="button" onClick={() => setView("build")} title="Framework — open build canvas" aria-label={`Framework ${workspace.activeProject.framework} — open build view`} className={`${footerChip} hidden sm:flex`}>{workspace.activeProject.framework}</button> : null}
        <button type="button" onClick={() => setView("task")} title="Forge activity — open task view" aria-label={`Forge ${running ? "working" : "ready"} — open task view`} className={`${footerChip} ml-auto hidden sm:flex`}>{running ? agent.currentActivity?.label ?? "Forge working" : "Forge ready"}</button>
        <button type="button" onClick={() => setView("serial")} title="Serial port — open live device" aria-label={`Serial port ${selectedBoard?.port ?? "none"} — open live device view`} className={footerChip}>{selectedBoard?.port ?? "no device"}</button>
      </footer>

      <TaskCenter agent={agent} open={tasksOpen} onClose={() => setTasksOpen(false)} onOpenTask={() => setView("task")} />
      <CommandCenter open={commandOpen} onClose={() => setCommandOpen(false)} onNavigate={navigate} onCanvas={openCanvas} onOpenProject={() => void openProject()} onAskForge={(prompt) => void sendForge(prompt)} />

      <FxOverlay open={quickOpen} onOpenChange={setQuickOpen} size="sm" title="Quick Forge">
        <div className="space-y-3 p-3">
          <div className="flex items-center gap-2 px-1 text-[10px] font-semibold uppercase tracking-[.14em] text-[var(--fx-text-muted)]"><ForgeCore state={agentCore} size="sm" label={false} /> Ask without leaving your flow</div>
          <UniversalComposer compact mode={quickMode} onModeChange={setQuickMode} onSubmit={(value) => { setQuickOpen(false); void sendForge(value, quickMode); }} running={running} onStop={() => void agent.cancel()} submitDisabled={!workspace.activeProject} placeholder="Ask Forge about the current project…" modelProviders={modelProviders} modelRoute={activeModelRoute} loadProviderModels={loadProviderModels} onModelChange={changeChatModel} />
        </div>
      </FxOverlay>

      <LiveRegion />
    </main>
  );
}
