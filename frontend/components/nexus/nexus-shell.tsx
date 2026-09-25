"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CircuitBoard,
  Code2,
  FileDiff,
  FolderGit2,
  FolderOpen,
  Hammer,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Pencil,
  Plus,
  Search,
  Settings,
  Sparkles,
  SquareTerminal,
  Trash2,
  X,
} from "lucide-react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { ChatCanvas } from "@/components/nexus/chat-canvas";
import { TaskCanvas } from "@/components/nexus/task-canvas";
import {
  BuildCanvas,
  CodeCanvas,
  HardwareCanvas,
  ReviewCanvas,
  SerialCanvas,
  TerminalCanvas,
  type NexusCanvas,
} from "@/components/nexus/work-canvases";
import { SettingsPage } from "@/components/settings/settings-page";
import type { SettingsCategory } from "@/components/settings/types";
import { useForgeXDialogs } from "@/components/ide/dialogs/forgex-dialog-provider";
import { useChatSession } from "@/hooks/use-chat-session";
import { useForgeAgentSession } from "@/hooks/use-forge-agent-session";
import { useForgeXSettings } from "@/hooks/use-forgex-settings";
import { usePromptForgeWorkspace } from "@/hooks/use-promptforge-workspace";
import { useModelRuntimeState } from "@/hooks/use-model-runtime-state";
import { promptForgeApi } from "@/lib/api";
import type {
  DetectedBoard,
  ProjectResponse,
} from "@/types";

function boardIdFromProject(project: ProjectResponse | null) {
  if (!project) return null;
  const metadataBoard = project.metadata?.board;
  if (
    typeof metadataBoard === "string" &&
    metadataBoard &&
    metadataBoard !== "UNKNOWN"
  )
    return metadataBoard;
  const platformio = project.metadata?.platformio;
  if (
    platformio &&
    typeof platformio === "object" &&
    "environments" in platformio
  ) {
    const environments = (
      platformio as { environments?: Array<{ board?: string }> }
    ).environments;
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
  if (normalized === "ARDUINO_UNO" || normalized === "ARDUINO UNO")
    return "uno";
  return board.board_type;
}

type View = "chat" | "agent" | "projects" | "settings";
type Detail = Exclude<NexusCanvas, "task">;

const WORKSPACE_TABS: Array<{ id: Detail; label: string; icon: typeof Code2; shortcut?: string }> = [
  { id: "code", label: "Files", icon: FolderOpen, shortcut: "Ctrl+Shift+E" },
  { id: "review", label: "Changes", icon: FileDiff, shortcut: "Ctrl+Shift+G" },
  { id: "build", label: "Build", icon: Hammer },
  { id: "hardware", label: "Device", icon: CircuitBoard },
  { id: "terminal", label: "Terminal", icon: SquareTerminal, shortcut: "Ctrl+`" },
];
export function NexusShell() {
  const dialogs = useForgeXDialogs();
  const workspace = usePromptForgeWorkspace();
  const settingsState = useForgeXSettings();
  const agent = useForgeAgentSession(
    workspace.activeProject?.project_id ?? null,
  );
  const chat = useChatSession();
  const overlayRef = useRef<HTMLDivElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const [view, setView] = useState<View>("chat");
  const [sidebar, setSidebar] = useState(true);
  const [narrow, setNarrow] = useState(false);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState("");
  const [selectedBoardPort, setSelectedBoardPort] = useState<string | null>(
    null,
  );
  const [settingsCategory, setSettingsCategory] =
    useState<SettingsCategory>("general");
  const modelRuntime = useModelRuntimeState();
  const modelProviders = modelRuntime.providers;
  const modelRoutes = modelRuntime.routes;
  const modelCatalog = modelRuntime.catalog;
  const modelSelecting = modelRuntime.selecting;
  const selectedBoard =
    workspace.detectedBoards.find((b) => b.port === selectedBoardPort) ??
    workspace.detectedBoards[0] ??
    null;
  const selectedBoardId =
    boardIdFromProject(workspace.activeProject) ??
    boardIdFromDetected(selectedBoard);
  const activeEnvironment =
    typeof workspace.activeProject?.metadata?.active_environment === "string"
      ? workspace.activeProject.metadata.active_environment
      : null;
  const running = ["running", "queued", "cancelling"].includes(
    agent.run?.status ?? "",
  );
  const workspaceFileCount = workspace.entries.length;
  // A run exposes only the durable ChangeSet identifier. The file count is
  // loaded by the review canvas; treating the identifier as an embedded
  // ChangeSet caused the renderer contract to drift from the API.
  const workspaceChangedCount = agent.run
    ? agent.run.created_file_count + agent.run.modified_file_count + agent.run.deleted_file_count
    : 0;
  const openWorkspaceDock = useCallback((tab?: Detail) => {
    const next = tab ?? detail ?? "code";
    setDetail(next);
    if (typeof window !== "undefined") localStorage.setItem("forgex-workspace-dock-tab", next);
  }, [detail]);
  const refreshModels = modelRuntime.refresh;
  const loadModels = modelRuntime.loadModels;
  useEffect(() => {
    void refreshModels().catch(() => null);
  }, [refreshModels]);
  useEffect(() => {
    const saved = localStorage.getItem("forgex-destination");
    if (["chat", "agent", "projects", "settings"].includes(saved ?? ""))
      setView(saved as View);
    const media = matchMedia("(max-width: 1279px)");
    const update = () => {
      setNarrow(media.matches);
      if (media.matches) setSidebar(false);
    };
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!detail || typeof window === "undefined") return;
    localStorage.setItem("forgex-workspace-dock-tab", detail);
  }, [detail]);
  useEffect(() => {
    if (!narrow || (!detail && !sidebar)) return;
    const root =
      detail && view === "agent" ? overlayRef.current : sidebarRef.current;
    if (!root) return;
    const previous = document.activeElement as HTMLElement | null;
    const focusable = () =>
      Array.from(
        root.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
        ),
      ).filter((el) => el.getClientRects().length);
    focusable()[0]?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const items = focusable();
      const first = items[0],
        last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    root.addEventListener("keydown", trap);
    return () => {
      root.removeEventListener("keydown", trap);
      if (previous?.isConnected) previous.focus();
    };
  }, [narrow, detail, sidebar, view]);
  const navigate = (next: View) => {
    setView(next);
    localStorage.setItem("forgex-destination", next);
    if (narrow) setSidebar(false);
  };
  const report = useCallback(
    (error: unknown) =>
      dialogs.message({
        title: "Could not complete this action",
        description:
          error instanceof Error ? error.message : "Please try again.",
      }),
    [dialogs],
  );
  const selectModel = async (provider: string, model: string) => {
    try {
      if (agent.activeSession) {
        await promptForgeApi.configureConversation(agent.activeSession.session_id, {
          provider_id: provider,
          model_id: model,
        });
        await agent.refreshSession(agent.activeSession.session_id);
      }
      await modelRuntime.saveDefaultSelection(provider, model);
    } catch (error) {
      await report(error);
    }
  };
  const openProject = async () => {
    if (!window.forgexDesktop?.openProject) {
      await dialogs.message({
        title: "Open a project in ForgeX Desktop",
        description:
          "The desktop folder picker connects a local project. Existing projects are available below.",
      });
      return;
    }
    const result = await window.forgexDesktop.openProject();
    if (!result.canceled && result.path) {
      await workspace.importProjectPath(result.path);
      navigate("agent");
    }
  };
  const histories = view === "agent" ? agent.sessions : chat.sessions;
  const selectedSession =
    view === "agent"
      ? agent.activeSession?.session_id
      : chat.session?.session_id;
  const visibleHistory = useMemo(
    () =>
      histories.filter((s) =>
        s.title.toLowerCase().includes(search.toLowerCase()),
      ),
    [histories, search],
  );
  const newConversation = async () => {
    try {
      if (view === "agent") {
        if (!workspace.activeProject) {
          navigate("projects");
          return;
        }
        setDraft("");
        await agent.createSession();
      } else {
        await chat.create();
        navigate("chat");
      }
    } catch (e) {
      await report(e);
    }
  };
  const rename = async (id: string, title: string) => {
    const next = await dialogs.input({
      title: "Rename conversation",
      label: "Name",
      defaultValue: title,
    });
    if (!next) return;
    try {
      await (view === "agent" ? promptForgeApi.configureConversation : promptForgeApi.configureChat)(id, { title: next });
      if (view === "agent") {
        await agent.refreshSessions();
        if (selectedSession === id) await agent.refreshSession(id);
      } else {
        await chat.refresh();
        if (selectedSession === id) await chat.select(id);
      }
    } catch (e) {
      await report(e);
    }
  };
  const remove = async (id: string) => {
    if (
      !(await dialogs.confirmAction({
        title: "Delete conversation?",
        description: "This removes its saved messages. Project files are kept.",
        confirmText: "Delete",
        variant: "danger",
      }))
    )
      return;
    try {
      await (view === "agent" ? promptForgeApi.deleteConversation : promptForgeApi.deleteChat)(id);
      if (view === "agent") {
        const items = await agent.refreshSessions();
        if (selectedSession === id) {
          if (items[0]) await agent.resumeSession(items[0].session_id);
          else await agent.createSession();
        }
      } else {
        const items = await chat.refresh();
        if (selectedSession === id) {
          if (items[0]) await chat.select(items[0].session_id);
          else await chat.create();
        }
      }
    } catch (e) {
      await report(e);
    }
  };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSidebar(true);
        window.setTimeout(
          () => document.getElementById("conversation-search")?.focus(),
          0,
        );
      }
      if (view === "agent" && (e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "e") {
        e.preventDefault();
        openWorkspaceDock("code");
      }
      if (view === "agent" && (e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "g") {
        e.preventDefault();
        openWorkspaceDock("review");
      }
      if (view === "agent" && (e.ctrlKey || e.metaKey) && e.key === "`") {
        e.preventDefault();
        openWorkspaceDock("terminal");
      }
      if (e.key === "Escape") {
        setDetail(null);
        if (narrow) setSidebar(false);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [narrow, openWorkspaceDock, view]);
  const openDetail = (canvas: NexusCanvas) =>
    setDetail(canvas === "task" ? null : canvas);
  const detailView =
    detail === "code" ? (
      <CodeCanvas
        workspace={workspace}
        run={agent.run}
        applyingChanges={agent.applyingChanges}
        onBack={() => setDetail(null)}
        onOpenReview={() => setDetail("review")}
        onApply={agent.applyChanges}
        onApplied={workspace.refreshProjectFiles}
      />
    ) : detail === "review" ? (
      <ReviewCanvas
        run={agent.run}
        applyingChanges={agent.applyingChanges}
        onBack={() => setDetail(null)}
        onApply={agent.applyChanges}
        onApplied={workspace.refreshProjectFiles}
      />
    ) : detail === "build" ? (
      <BuildCanvas
        workspace={workspace}
        run={agent.run}
        onBack={() => setDetail(null)}
        onOpenTerminal={() => setDetail("terminal")}
        onFlash={() => {
          if (agent.run?.status === "awaiting_flash_confirmation") {
            navigate("agent");
            setDetail(null);
          } else {
            void workspace.flashActiveProject(selectedBoard);
          }
        }}
      />
    ) : detail === "hardware" ? (
      <HardwareCanvas
        workspace={workspace}
        selectedBoard={selectedBoard}
        onSelectBoard={setSelectedBoardPort}
        onBack={() => setDetail(null)}
      />
    ) : detail === "serial" ? (
      <SerialCanvas
        workspace={workspace}
        selectedBoard={selectedBoard}
        onBack={() => setDetail("hardware")}
      />
    ) : detail === "terminal" ? (
      <TerminalCanvas workspace={workspace} onBack={() => setDetail(null)} />
    ) : null;
  const title =
    view === "chat"
      ? (chat.session?.title ?? "New chat")
      : view === "agent"
        ? (agent.activeSession?.title ?? "New task")
        : view === "projects"
          ? "Projects"
          : "Settings";
  return (
    <main className="fx-product-shell">
      {sidebar && narrow ? (
        <button
          className="fx-sidebar-scrim"
          aria-label="Close navigation"
          onClick={() => setSidebar(false)}
        />
      ) : null}
      <aside
        ref={sidebarRef}
        inert={!sidebar}
        role={narrow ? "dialog" : undefined}
        aria-modal={narrow && sidebar ? true : undefined}
        className={`fx-sidebar ${sidebar ? "is-open" : "is-closed"}`}
        aria-label="Main navigation"
      >
        <div className="fx-sidebar-brand">
          <Sparkles size={19} />
          <strong>ForgeX</strong>
          <button
            className="fx-quiet-button ml-auto"
            aria-label="Collapse navigation"
            onClick={() => setSidebar(false)}
          >
            <PanelLeftClose size={17} />
          </button>
        </div>
        <nav>
          {(
            [
              { id: "chat", label: "Chat", icon: MessageSquare },
              { id: "agent", label: "Agent", icon: Sparkles },
              { id: "projects", label: "Projects", icon: FolderOpen },
            ] as const
          ).map((item) => (
            <button
              key={item.id}
              className={`fx-nav-item ${view === item.id ? "is-selected" : ""}`}
              aria-current={view === item.id ? "page" : undefined}
              onClick={() => navigate(item.id)}
            >
              <item.icon size={17} />
              <span>{item.label}</span>
              {item.id === "agent" && running ? (
                <span
                  className="fx-working-dot ml-auto"
                  aria-label="Agent is working"
                />
              ) : null}
            </button>
          ))}
        </nav>
        {view === "agent" ? (
          <div className="fx-sidebar-workspace">
            <div className="fx-sidebar-section-label">Workspace</div>
            {workspace.activeProject ? (
              <button className="fx-workspace-card" onClick={() => openWorkspaceDock("code")}>
                <span className="fx-workspace-card-icon"><FolderGit2 size={15} /></span>
                <span className="fx-workspace-card-copy">
                  <strong title={workspace.activeProject.project_name}>{workspace.activeProject.project_name}</strong>
                  <small title={workspace.activeProject.project_path}>{workspace.activeProject.project_path}</small>
                  <span>
                    <em>{selectedBoardId ?? workspace.activeProject.target_board ?? "Target unset"}</em>
                    <em>{workspaceFileCount} files</em>
                    {workspaceChangedCount ? <em>{workspaceChangedCount} changed</em> : null}
                  </span>
                </span>
              </button>
            ) : (
              <button className="fx-workspace-card is-empty" onClick={() => void openProject().catch(report)}>
                <span className="fx-workspace-card-icon"><FolderOpen size={15} /></span>
                <span className="fx-workspace-card-copy"><strong>Choose workspace</strong><small>Open an embedded project folder</small></span>
              </button>
            )}
          </div>
        ) : null}
        <button
          className="fx-new-conversation"
          onClick={() => void newConversation()}
        >
          <Plus size={16} />
          New {view === "agent" ? "task" : "chat"}
        </button>
        <div className="fx-history-search">
          <Search size={14} />
          <input
            id="conversation-search"
            aria-label="Search conversations"
            placeholder="Search conversations"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="fx-history-label">
          {view === "agent" ? "Project tasks" : "Recent chats"}
        </div>
        <div className="fx-history-list">
          {visibleHistory.map((s) => (
            <div
              className={`fx-history-item ${selectedSession === s.session_id ? "is-selected" : ""}`}
              key={s.session_id}
            >
              <button
                title={s.title}
                onClick={() => {
                  void (
                    view === "agent"
                      ? agent.resumeSession(s.session_id)
                      : chat.select(s.session_id)
                  ).catch(report);
                  if (narrow) setSidebar(false);
                }}
              >
                {s.title}
              </button>
              <div className="fx-history-actions">
                <button
                  aria-label={`Rename ${s.title}`}
                  onClick={() => void rename(s.session_id, s.title)}
                >
                  <Pencil size={12} />
                </button>
                <button
                  aria-label={`Delete ${s.title}`}
                  onClick={() => void remove(s.session_id)}
                >
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
          ))}
          {!visibleHistory.length ? <p>No conversations yet.</p> : null}
        </div>
        <button
          className={`fx-nav-item mt-auto ${view === "settings" ? "is-selected" : ""}`}
          onClick={() => navigate("settings")}
        >
          <Settings size={17} />
          Settings
        </button>
      </aside>
      <section className="fx-main-workspace">
        <header className="fx-product-header">
          {!sidebar ? (
            <button
              className="fx-quiet-button"
              aria-label="Open navigation"
              onClick={() => setSidebar(true)}
            >
              <PanelLeftOpen size={18} />
            </button>
          ) : null}
          <span className="min-w-0 flex-1 truncate" title={title}>
            {title}
          </span>
          {view === "agent" ? (
            <>
              <select
                aria-label="Active project"
                value={workspace.activeProject?.project_id ?? ""}
                onChange={(e) => {
                  const p = workspace.projects.find(
                    (item) => item.project_id === e.target.value,
                  );
                  if (p) workspace.setActiveProject(p);
                }}
              >
                <option value="" disabled>
                  Choose project
                </option>
                {workspace.projects.map((p) => (
                  <option key={p.project_id} value={p.project_id}>
                    {p.project_name}
                  </option>
                ))}
              </select>
              <span className="fx-header-context hidden lg:inline-flex" title="Configured target">
                <CircuitBoard size={13} />
                {selectedBoardId ?? "No target"}
              </span>
              {selectedBoard ? (
                <span className="fx-header-context hidden xl:inline-flex" title="Connected device">
                  <span className="fx-device-dot" />
                  {selectedBoard.port}
                </span>
              ) : null}
              <button
                className={`fx-workspace-toggle ${detail ? "is-open" : ""}`}
                aria-label={detail ? "Close workspace" : "Open workspace"}
                aria-expanded={Boolean(detail)}
                onClick={() => {
                  if (detail) setDetail(null);
                  else {
                    const saved = typeof window !== "undefined" ? localStorage.getItem("forgex-workspace-dock-tab") : null;
                    openWorkspaceDock(WORKSPACE_TABS.some((item) => item.id === saved) ? (saved as Detail) : "code");
                  }
                }}
              >
                {detail ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
                <span className="hidden sm:inline">Workspace</span>
                {workspaceChangedCount ? <span className="fx-workspace-count">{workspaceChangedCount}</span> : null}
              </button>
            </>
          ) : null}
        </header>
        <div className="fx-product-content">
          <PanelGroup
            direction="horizontal"
            autoSaveId="forgex-conversation-panels"
          >
            <Panel defaultSize={52} minSize={30}>
              <div className={view === "chat" ? "h-full" : "hidden"}>
                <ChatCanvas
                  chat={chat}
                  projects={workspace.projects}
                  providers={modelProviders}
                  routes={modelRoutes}
                  catalog={modelCatalog}
                  onLoadModels={loadModels}
                  onHandoff={(text, projectId) => {
                    const project = workspace.projects.find(
                      (p) => p.project_id === projectId,
                    );
                    if (project) workspace.setActiveProject(project);
                    setDraft(text);
                    navigate("agent");
                  }}
                />
              </div>
              <div className={view === "agent" ? "h-full" : "hidden"}>
                <TaskCanvas
                  agent={agent}
                  projectName={workspace.activeProject?.project_name ?? null}
                  selectedBoard={selectedBoard}
                  selectedBoardId={selectedBoardId}
                  activeEnvironment={activeEnvironment}
                  modelProviders={modelProviders}
                  modelRoutes={modelRoutes}
                  modelCatalog={modelCatalog}
                  modelSelecting={modelSelecting}
                  onProviderModelsRequest={loadModels}
                  onModelSelectionChange={selectModel}
                  onOpenCanvas={openDetail}
                  onViewFile={(path) => {
                    void workspace.openFile(path);
                    openDetail("code");
                  }}
                  onWorkspaceChanged={() =>
                    void workspace.refreshProjectFiles()
                  }
                  initialDraft={draft}
                  onDraftConsumed={() => setDraft("")}
                />
              </div>
              {view === "projects" ? (
                <div className="fx-project-page">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <h1>Your projects</h1>
                      <p>Open a workspace and bring an idea to life.</p>
                    </div>
                    <button
                      className="fx-primary-action"
                      onClick={() => void openProject().catch(report)}
                    >
                      <FolderOpen size={16} />
                      Open folder
                    </button>
                  </div>
                  <div className="fx-project-list">
                    {workspace.projects.map((p) => (
                      <button
                        key={p.project_id}
                        onClick={() => {
                          workspace.setActiveProject(p);
                          navigate("agent");
                        }}
                      >
                        <FolderOpen size={20} />
                        <span>
                          <strong>{p.project_name}</strong>
                          <small>{p.project_path}</small>
                        </span>
                        <span className="text-xs text-[var(--fx-text-muted)]">
                          {p.target_board}
                        </span>
                      </button>
                    ))}
                    {!workspace.projects.length ? (
                      <p className="py-16 text-center">
                        Open a local folder to start your first project.
                      </p>
                    ) : null}
                  </div>
                </div>
              ) : null}
              {view === "settings" ? (
                <SettingsPage
                  selectedCategory={settingsCategory}
                  providers={modelProviders}
                  routes={modelRoutes}
                  activeProject={workspace.activeProject}
                  health={workspace.health}
                  settingsState={settingsState}
                  onSelectCategory={setSettingsCategory}
                  onRefreshModelRouter={async () => {
                    await refreshModels();
                  }}
                  onLog={workspace.addLog}
                  onClose={() => navigate("chat")}
                />
              ) : null}
            </Panel>
            {view === "agent" && detail && !narrow ? (
              <>
                <PanelResizeHandle className="fx-detail-handle" />
                <Panel defaultSize={38} minSize={28} maxSize={55}>
                  <div className="fx-detail-pane fx-workspace-dock">
                    <div className="fx-workspace-tabs" role="tablist" aria-label="Workspace tools">
                      {WORKSPACE_TABS.map((item) => {
                        const Icon = item.icon;
                        const selected = detail === item.id || (item.id === "hardware" && detail === "serial");
                        return (
                          <button key={item.id} role="tab" aria-selected={selected} aria-keyshortcuts={item.shortcut} title={item.shortcut ? `${item.label} (${item.shortcut})` : item.label} className={selected ? "is-selected" : ""} onClick={() => openWorkspaceDock(item.id)}>
                            <Icon size={14} />
                            <span>{item.label}</span>
                            {item.id === "review" && workspaceChangedCount ? <em>{workspaceChangedCount}</em> : null}
                          </button>
                        );
                      })}
                      <button className="fx-workspace-tab-close" aria-label="Close workspace" onClick={() => setDetail(null)}><X size={14} /></button>
                    </div>
                    <div className="fx-workspace-dock-body">{detailView}</div>
                  </div>
                </Panel>
              </>
            ) : null}
          </PanelGroup>
        </div>
        {view === "agent" && detail && narrow ? (
          <div
            ref={overlayRef}
            className="fx-detail-overlay"
            role="dialog"
            aria-modal="true"
            aria-label="Workspace details"
          >
            <button
              className="fx-detail-close"
              aria-label="Close details"
              onClick={() => setDetail(null)}
            >
              <X size={18} />
            </button>
            <div className="fx-workspace-tabs" role="tablist" aria-label="Workspace tools">
              {WORKSPACE_TABS.map((item) => {
                const Icon = item.icon;
                const selected = detail === item.id || (item.id === "hardware" && detail === "serial");
                return <button key={item.id} role="tab" aria-selected={selected} aria-keyshortcuts={item.shortcut} title={item.shortcut ? `${item.label} (${item.shortcut})` : item.label} className={selected ? "is-selected" : ""} onClick={() => openWorkspaceDock(item.id)}><Icon size={14} /><span>{item.label}</span></button>;
              })}
            </div>
            <div className="fx-workspace-dock-body">{detailView}</div>
          </div>
        ) : null}
      </section>
    </main>
  );
}
