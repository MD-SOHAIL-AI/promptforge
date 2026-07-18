"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Bell,
  Blocks,
  Box,
  Braces,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Cloud,
  Code2,
  Cpu,
  Database,
  Download,
  Eye,
  Folder,
  Gauge,
  History,
  Home,
  Layers,
  LineChart,
  Link2,
  ListChecks,
  Loader2,
  MessageSquare,
  Microchip,
  Monitor,
  Pause,
  Play,
  PlugZap,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Square,
  Terminal,
  Trash2,
  Upload,
  Users,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";

import { useForgeXDialogs } from "@/components/ide/dialogs/forgex-dialog-provider";
import { EditorWorkbench } from "@/components/ide/editor-workbench";
import { IdeExplorer } from "@/components/ide/ide-explorer";
import { useForgeXSettings } from "@/hooks/use-forgex-settings";
import { usePromptForgeWorkspace } from "@/hooks/use-promptforge-workspace";
import { promptForgeApi } from "@/lib/api";
import type {
  BuildHistoryItem,
  ConsoleEntry,
  DetectedBoard,
  ModelProviderResponse,
  ModelRouteResponse,
  ProjectResponse,
  WorkflowStage,
} from "@/types";

type ViewId =
  | "home"
  | "task"
  | "history"
  | "projects"
  | "device"
  | "templates"
  | "integrations"
  | "settings"
  | "connections"
  | "models"
  | "hardware";

type Tone = "blue" | "green" | "yellow" | "red" | "purple" | "cyan" | "muted";

const DEFAULT_PROMPT = "Create an ESP32 blink LED project using PlatformIO.";

const DEFAULT_STEPS: WorkflowStage[] = [
  {
    key: "planning",
    label: "Initialize project and configure environment",
    description: "Set up firmware project metadata, board target, and toolchain inputs.",
    status: "pending",
  },
  {
    key: "generation",
    label: "Implement LED blink logic",
    description: "Write code to toggle the onboard LED at a one second interval.",
    status: "pending",
  },
  {
    key: "build",
    label: "Build and static analysis",
    description: "Compile the project and surface lint, dependency, and artifact results.",
    status: "pending",
  },
  {
    key: "flash",
    label: "Flash to device",
    description: "Upload the verified firmware to the selected board over serial.",
    status: "pending",
  },
  {
    key: "monitor",
    label: "Verify and validate",
    description: "Watch serial output and record device health after flashing.",
    status: "pending",
  },
];

const NAV_ITEMS: Array<{ id: ViewId; label: string; icon: LucideIcon }> = [
  { id: "home", label: "Home", icon: Home },
  { id: "history", label: "History", icon: History },
  { id: "projects", label: "Projects", icon: Folder },
  { id: "device", label: "Device Lab", icon: Microchip },
  { id: "templates", label: "Templates", icon: ListChecks },
  { id: "integrations", label: "Integrations", icon: Blocks },
];

const SETTINGS_NAV: Array<{ id: ViewId; label: string }> = [
  { id: "settings", label: "General" },
  { id: "models", label: "Models & Routing" },
  { id: "connections", label: "Connections" },
  { id: "hardware", label: "Hardware & Device" },
];

function safeArray<T>(value: T[] | undefined | null): T[] {
  return Array.isArray(value) ? value : [];
}

function boardIdFromDetected(board: DetectedBoard | null) {
  if (!board) return null;
  const normalized = board.board_type.toUpperCase();
  if (normalized === "ESP32") return "ESP32 DevKit V1";
  if (normalized === "ESP32-S3") return "ESP32-S3";
  if (normalized === "ESP32-C3") return "ESP32-C3";
  if (normalized === "ARDUINO_UNO" || normalized === "ARDUINO UNO") return "Arduino Uno";
  return board.board_type;
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
  return project.target_board && project.target_board !== "UNKNOWN" ? project.target_board : null;
}

function generationModeFromProject(project: ProjectResponse | null) {
  if (!project) return "new_project" as const;
  const hasPlatformIOIni = project.metadata?.has_platformio_ini !== false && project.project_type !== "generic";
  return hasPlatformIOIni ? "modify_existing_project" as const : "generate_into_open_folder" as const;
}

function routeFor(taskType: string, routes: ModelRouteResponse[]) {
  return routes.find((route) => route.task_type === taskType) ?? null;
}

function providerFor(providerId: string | null | undefined, providers: ModelProviderResponse[]) {
  if (!providerId) return null;
  return providers.find((provider) => provider.provider_id === providerId) ?? null;
}

function modelLabel(route: ModelRouteResponse | null, providers: ModelProviderResponse[]) {
  if (!route) return "Not configured";
  const provider = providerFor(route.provider_id, providers);
  const model = route.model_id.split("/").pop() ?? route.model_id;
  return `${provider?.display_name ?? route.provider_id} / ${model}`;
}

function compactId(value?: string | null) {
  if (!value) return "none";
  return value.length > 10 ? `${value.slice(0, 8)}` : value;
}

function formatDate(value?: string | null) {
  if (!value) return "Not recorded";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

function formatRelative(value?: string | null) {
  if (!value) return "just now";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "just now";
  const delta = Date.now() - timestamp;
  const minutes = Math.max(0, Math.round(delta / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function formatDuration(ms?: number | null) {
  if (!ms || ms <= 0) return "n/a";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${remainder}s`;
}

function stageProgress(stages: WorkflowStage[]) {
  const completed = stages.filter((stage) => stage.status === "success").length;
  return { completed, total: stages.length || 5 };
}

function currentStage(stages: WorkflowStage[]) {
  return (
    stages.find((stage) => stage.status === "active") ??
    stages.find((stage) => stage.status === "failed") ??
    stages.find((stage) => stage.status === "blocked") ??
    stages.find((stage) => stage.status === "pending") ??
    stages[stages.length - 1] ??
    DEFAULT_STEPS[0]
  );
}

function toneForStatus(status?: string | null): Tone {
  const normalized = (status ?? "").toLowerCase();
  if (["success", "succeeded", "completed", "healthy", "connected", "passed", "active"].some((term) => normalized.includes(term))) return "green";
  if (["failed", "error", "offline"].some((term) => normalized.includes(term))) return "red";
  if (["pending", "waiting", "degraded", "warning"].some((term) => normalized.includes(term))) return "yellow";
  return "blue";
}

function statusText(health: ReturnType<typeof usePromptForgeWorkspace>["health"]) {
  if (!health) return "Backend offline";
  if (health.status === "healthy") return "Backend connected";
  return `Backend ${health.status}`;
}

function providerStatus(provider: ModelProviderResponse) {
  if (!provider.enabled) return "Disabled";
  if (!provider.configured) return "Needs key";
  if (provider.health_status === "error") return "Error";
  if (provider.health_status === "offline") return "Offline";
  return "Connected";
}

function Panel({
  title,
  icon: Icon,
  children,
  action,
  className = "",
}: {
  title?: string;
  icon?: LucideIcon;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`fx-ref-card ${className}`}>
      {(title || action) && (
        <div className="fx-ref-card-head">
          <div className="fx-ref-card-title">
            {Icon ? <Icon className="h-4 w-4" /> : null}
            {title ? <span>{title}</span> : null}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

function StatusChip({ icon: Icon, label, tone = "green" }: { icon: LucideIcon; label: string; tone?: Tone }) {
  return (
    <div className={`fx-ref-status-chip tone-${tone}`}>
      <Icon className="h-4 w-4" />
      <span>{label}</span>
      {tone === "green" ? <span className="fx-ref-live-dot" /> : null}
    </div>
  );
}

function PrimaryButton({
  children,
  onClick,
  disabled,
  icon: Icon,
  danger,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  icon?: LucideIcon;
  danger?: boolean;
}) {
  return (
    <button className={`fx-ref-button ${danger ? "danger" : "primary"}`} onClick={onClick} disabled={disabled}>
      {Icon ? <Icon className="h-4 w-4" /> : null}
      <span>{children}</span>
    </button>
  );
}

function GhostButton({
  children,
  onClick,
  icon: Icon,
  danger,
  disabled,
}: {
  children: ReactNode;
  onClick?: () => void;
  icon?: LucideIcon;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <button className={`fx-ref-button ghost ${danger ? "danger" : ""}`} onClick={onClick} disabled={disabled}>
      {Icon ? <Icon className="h-4 w-4" /> : null}
      <span>{children}</span>
    </button>
  );
}

function EmptyState({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return (
    <div className="fx-ref-empty">
      <Sparkles className="h-6 w-6 text-[var(--fx-ref-blue)]" />
      <div>
        <h3>{title}</h3>
        <p>{body}</p>
      </div>
      {action}
    </div>
  );
}

function ShellSidebar({
  view,
  onView,
  onNewTask,
}: {
  view: ViewId;
  onView: (view: ViewId) => void;
  onNewTask: () => void;
}) {
  const settingsOpen = SETTINGS_NAV.some((item) => item.id === view);
  return (
    <aside className="fx-ref-sidebar">
      <div className="fx-ref-brand">
        <div className="fx-ref-logo-mark">X</div>
        <span>ForgeX</span>
      </div>
      <button className="fx-ref-new-task" onClick={onNewTask}>
        <Plus className="h-5 w-5" />
        <span>New Task</span>
      </button>

      <nav className="fx-ref-nav">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active =
            item.id === view ||
            (item.id === "projects" && view === "projects") ||
            (item.id === "device" && view === "hardware");
          return (
            <button key={item.id} className={`fx-ref-nav-item ${active ? "active" : ""}`} onClick={() => onView(item.id)}>
              <Icon className="h-5 w-5" />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="fx-ref-sidebar-spacer" />

      <div className={`fx-ref-settings-box ${settingsOpen ? "open" : ""}`}>
        <button className={`fx-ref-nav-item ${settingsOpen ? "active" : ""}`} onClick={() => onView("settings")}>
          <Settings className="h-5 w-5" />
          <span>Settings</span>
          <ChevronRight className="ml-auto h-4 w-4" />
        </button>
        {settingsOpen ? (
          <div className="fx-ref-settings-subnav">
            {SETTINGS_NAV.map((item) => (
              <button key={item.id} className={view === item.id ? "active" : ""} onClick={() => onView(item.id)}>
                {item.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div className="fx-ref-profile-card">
        <div className="fx-ref-avatar">E</div>
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-[var(--fx-text)]">Embedded Team</div>
          <div className="truncate text-xs text-[var(--fx-text-muted)]">Pro Workspace</div>
        </div>
        <ChevronDown className="ml-auto h-4 w-4 text-[var(--fx-text-muted)]" />
      </div>
    </aside>
  );
}

function TopStatusBar({
  health,
  selectedBoard,
}: {
  health: ReturnType<typeof usePromptForgeWorkspace>["health"];
  selectedBoard: DetectedBoard | null;
}) {
  return (
    <header className="fx-ref-topbar">
      <div className="fx-ref-window-buttons">
        <span />
        <span />
        <span />
      </div>
      <StatusChip icon={Cloud} label={statusText(health)} tone={health?.status === "healthy" ? "green" : "yellow"} />
      <StatusChip icon={Cpu} label={selectedBoard ? "Device ready" : "No device"} tone={selectedBoard ? "green" : "yellow"} />
      <StatusChip icon={ShieldCheck} label="Safe mode" tone="green" />
      <button className="fx-ref-user-button">E</button>
      <ChevronDown className="h-4 w-4 text-[var(--fx-text-muted)]" />
    </header>
  );
}

function HomeView({
  prompt,
  setPrompt,
  onStart,
  onOpenWorkspace,
  onView,
  selectedBoardLabel,
  modelLabelText,
  recentBuilds,
  health,
  selectedBoard,
}: {
  prompt: string;
  setPrompt: (value: string) => void;
  onStart: () => void;
  onOpenWorkspace: () => void;
  onView: (view: ViewId) => void;
  selectedBoardLabel: string;
  modelLabelText: string;
  recentBuilds: BuildHistoryItem[];
  health: ReturnType<typeof usePromptForgeWorkspace>["health"];
  selectedBoard: DetectedBoard | null;
}) {
  const suggestions = [
    { label: "Blink LED on ESP32", icon: Microchip },
    { label: "Add OTA Update", icon: Upload },
    { label: "Read Sensor Data", icon: Activity },
    { label: "Power Optimization", icon: Zap },
    { label: "MQTT Telemetry", icon: Radio },
  ];
  const cards = [
    { title: "Start embedded task", body: "Describe what you need. ForgeX will plan, generate, build, and verify.", icon: Code2, view: "task" as ViewId, tone: "blue" as Tone },
    { title: "Build with AI", body: "Generate, debug, and optimize firmware with assistant support.", icon: Sparkles, view: "task" as ViewId, tone: "cyan" as Tone },
    { title: "Flash board", body: "Build and upload firmware to your connected device.", icon: Cpu, view: "device" as ViewId, tone: "purple" as Tone },
    { title: "Open workspace", body: "Open a project or continue where you left off.", icon: Folder, view: "projects" as ViewId, tone: "yellow" as Tone },
    { title: "Review runs", body: "Inspect logs, test results, performance, and recovery.", icon: LineChart, view: "history" as ViewId, tone: "green" as Tone },
  ];

  return (
    <div className="fx-ref-home">
      <section className="fx-ref-home-hero">
        <h1>
          Welcome to <span>ForgeX</span>
        </h1>
        <p>Your embedded AI engineering workspace</p>
      </section>

      <section className="fx-ref-command-card">
        <div className="fx-ref-command-row">
          <Sparkles className="h-6 w-6 text-[var(--fx-ref-blue)]" />
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="Ask ForgeX to create, repair, build, or inspect firmware..."
          />
        </div>
        <div className="fx-ref-command-controls">
          <button onClick={() => onView("models")}>
            <Box className="h-5 w-5" />
            <span>
              <small>Model</small>
              {modelLabelText}
            </span>
            <ChevronDown className="ml-auto h-4 w-4" />
          </button>
          <button onClick={() => onView("hardware")}>
            <Cpu className="h-5 w-5" />
            <span>
              <small>Target Board</small>
              {selectedBoardLabel}
            </span>
            <ChevronDown className="ml-auto h-4 w-4" />
          </button>
          <PrimaryButton icon={ArrowRight} onClick={onStart}>
            Start Task
          </PrimaryButton>
        </div>
      </section>

      <div className="fx-ref-suggestion-row">
        {suggestions.map((item) => {
          const Icon = item.icon;
          return (
            <button key={item.label} onClick={() => setPrompt(item.label)}>
              <Icon className="h-4 w-4" />
              {item.label}
            </button>
          );
        })}
      </div>

      <div className="fx-ref-action-grid">
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <button key={card.title} className="fx-ref-action-card" onClick={card.title === "Open workspace" ? onOpenWorkspace : () => onView(card.view)}>
              <span className={`fx-ref-icon-box tone-${card.tone}`}>
                <Icon className="h-6 w-6" />
              </span>
              <strong>{card.title}</strong>
              <p>{card.body}</p>
              <ArrowRight className="fx-ref-card-arrow h-5 w-5" />
            </button>
          );
        })}
      </div>

      <div className="fx-ref-two-col">
        <Panel title="Recent Activity" icon={History} action={<button className="fx-ref-link" onClick={() => onView("history")}>View all</button>}>
          <div className="fx-ref-list">
            {(recentBuilds.length ? recentBuilds.slice(0, 5) : []).map((build) => (
              <div key={build.execution_id} className="fx-ref-list-row">
                <ListChecks className="h-4 w-4 text-[var(--fx-text-muted)]" />
                <span>{build.board || "Workspace build"}</span>
                <em>{build.status}</em>
                <time>{formatRelative(build.timestamp)}</time>
              </div>
            ))}
            {recentBuilds.length === 0 ? (
              <EmptyState title="No runs yet" body="Open a workspace and start a task to populate the activity stream." />
            ) : null}
          </div>
        </Panel>

        <Panel title="System Status" icon={Monitor} action={<button className="fx-ref-link" onClick={() => onView("hardware")}>View all</button>}>
          <div className="fx-ref-status-list">
            <StatusLine icon={Cloud} label="Backend" value={health?.status === "healthy" ? "Connected" : "Check backend"} tone={health?.status === "healthy" ? "green" : "yellow"} />
            <StatusLine icon={Cpu} label="Device" value={selectedBoard ? boardIdFromDetected(selectedBoard) ?? selectedBoard.board_type : "No device"} tone={selectedBoard ? "green" : "yellow"} />
            <StatusLine icon={PlugZap} label="Serial Port" value={selectedBoard?.port ?? "Not selected"} tone={selectedBoard ? "green" : "yellow"} />
            <StatusLine icon={ShieldCheck} label="Safe Mode" value="Enabled" tone="green" />
          </div>
        </Panel>
      </div>
    </div>
  );
}

function StatusLine({ icon: Icon, label, value, tone }: { icon: LucideIcon; label: string; value: string; tone: Tone }) {
  return (
    <div className="fx-ref-status-line">
      <Icon className="h-4 w-4 text-[var(--fx-text-muted)]" />
      <span>{label}</span>
      <strong className={`tone-${tone}`}>{value}</strong>
      <CircleDot className={`h-3 w-3 tone-${tone}`} />
    </div>
  );
}

function TaskView({
  prompt,
  setPrompt,
  taskGoal,
  stages,
  logs,
  activeProject,
  selectedBoard,
  selectedBoardLabel,
  isExecuting,
  isCancelling,
  taskId,
  onStart,
  onCancel,
  onView,
}: {
  prompt: string;
  setPrompt: (value: string) => void;
  taskGoal: string;
  stages: WorkflowStage[];
  logs: ConsoleEntry[];
  activeProject: ProjectResponse | null;
  selectedBoard: DetectedBoard | null;
  selectedBoardLabel: string;
  isExecuting: boolean;
  isCancelling: boolean;
  taskId: string | null;
  onStart: () => void;
  onCancel: () => void;
  onView: (view: ViewId) => void;
}) {
  const progress = stageProgress(stages);
  const stage = currentStage(stages);
  const liveLogs = logs.slice(-5).reverse();

  return (
    <div className="fx-ref-task-layout">
      <section className="fx-ref-task-main">
        <div className="fx-ref-breadcrumb">Home / Tasks / ESP32 Blink LED Task</div>
        <div className="fx-ref-title-row">
          <h1>{activeProject?.project_name ?? "ESP32 Blink LED Task"}</h1>
          <span className={`fx-ref-pill tone-${isExecuting ? "blue" : toneForStatus(stage.status)}`}>
            {isExecuting ? "In Progress" : stage.status.replaceAll("_", " ")}
          </span>
        </div>

        <Panel title="Your goal" icon={CircleDot}>
          <p className="fx-ref-body-copy">{taskGoal || "Create and deploy firmware for an ESP32 DevKit V1 that blinks the onboard LED every second."}</p>
        </Panel>

        <Panel
          title="ForgeX AI Plan"
          icon={Sparkles}
          action={<span className="fx-ref-mini-badge">{stages.length || 5} steps</span>}
        >
          <div className="fx-ref-plan-list">
            {stages.map((item, index) => (
              <div key={item.key} className={`fx-ref-plan-step status-${item.status}`}>
                <div className="fx-ref-plan-index">{item.status === "success" ? <CheckCircle2 className="h-4 w-4" /> : index + 1}</div>
                <div>
                  <strong>{item.label}</strong>
                  <p>{item.description}</p>
                </div>
                <ChevronDown className="ml-auto h-4 w-4 text-[var(--fx-text-muted)]" />
              </div>
            ))}
          </div>
          <div className="fx-ref-plan-actions">
            <PrimaryButton icon={CheckCircle2} onClick={onStart} disabled={isExecuting}>
              Approve plan & continue
            </PrimaryButton>
            <GhostButton icon={MessageSquare} onClick={() => onView("home")}>
              Request changes
            </GhostButton>
          </div>
        </Panel>

        <Panel title="Live activity" icon={Activity} action={<button className="fx-ref-link" onClick={() => onView("history")}>View all</button>}>
          <div className="fx-ref-activity-feed">
            {liveLogs.length ? (
              liveLogs.map((log) => (
                <div key={log.id}>
                  <time>{formatRelative(log.timestamp)}</time>
                  <CircleDot className={`h-3 w-3 tone-${log.channel === "error" ? "red" : log.channel === "build" ? "yellow" : "green"}`} />
                  <span>{log.message}</span>
                </div>
              ))
            ) : (
              <>
                <div><time>2m ago</time><CircleDot className="h-3 w-3 tone-green" /><span>Project scaffold ready</span></div>
                <div><time>1m ago</time><CircleDot className="h-3 w-3 tone-green" /><span>Toolchain and dependencies checked</span></div>
                <div><time>Just now</time><CircleDot className="h-3 w-3 tone-blue" /><span>Ready for plan review</span></div>
              </>
            )}
          </div>
        </Panel>

        <section className="fx-ref-task-composer">
          <Sparkles className="h-5 w-5 text-[var(--fx-ref-blue)]" />
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="Ask ForgeX to make changes, add features, or run checks..."
          />
          <button className="fx-ref-mode-button">Auto <ChevronDown className="h-4 w-4" /></button>
          <button className="fx-ref-send" onClick={onStart} disabled={isExecuting}>
            {isExecuting ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
          </button>
        </section>
      </section>

      <aside className="fx-ref-task-rail">
        <Panel title="Run Controls" icon={Play}>
          <div className="fx-ref-rail-actions">
            <PrimaryButton icon={Play} onClick={onStart} disabled={isExecuting}>
              {isExecuting ? "Running" : "Resume task"}
            </PrimaryButton>
            <GhostButton icon={Pause} disabled={!isExecuting}>
              Pause
            </GhostButton>
            <GhostButton icon={Square} danger onClick={onCancel} disabled={!isExecuting || isCancelling}>
              Cancel task
            </GhostButton>
          </div>
        </Panel>

        <Panel title="Task Context" icon={Blocks}>
          <dl className="fx-ref-definition-list">
            <dt>Project</dt><dd>{activeProject?.project_name ?? "No workspace"}</dd>
            <dt>Target Board</dt><dd>{selectedBoardLabel}</dd>
            <dt>Framework</dt><dd>{activeProject?.framework ?? "PlatformIO"}</dd>
            <dt>Language</dt><dd>C/C++</dd>
            <dt>Task ID</dt><dd>{compactId(taskId)}</dd>
            <dt>Created</dt><dd>{formatDate(activeProject?.created_at)}</dd>
          </dl>
        </Panel>

        <Panel title="Approvals" icon={Users}>
          <div className="fx-ref-approval-row"><span>Plan approval</span><em>Pending</em></div>
          <div className="fx-ref-approval-row"><span>Safety check</span><em>Pending</em></div>
        </Panel>

        <Panel title="Current Stage" icon={Gauge}>
          <div className="fx-ref-current-stage">
            <div>{progress.completed}/{progress.total}</div>
            <span>
              <strong>{stage.label}</strong>
              <p>{stage.description}</p>
            </span>
          </div>
        </Panel>

        <Panel title="Resources" icon={Database}>
          <div className="fx-ref-status-list">
            <StatusLine icon={Cpu} label={selectedBoard?.board_type ?? "Board"} value={selectedBoard?.port ? "Connected" : "Missing"} tone={selectedBoard ? "green" : "yellow"} />
            <StatusLine icon={Radio} label={selectedBoard?.port ?? "COM port"} value={selectedBoard ? "115200" : "Unknown"} tone={selectedBoard ? "green" : "yellow"} />
            <StatusLine icon={Wrench} label="Toolchain" value="Configured" tone="green" />
          </div>
        </Panel>
      </aside>
    </div>
  );
}

function BuildStudioView({
  activeProject,
  entries,
  activePath,
  selectedFile,
  tabs,
  editorSettings,
  onOpenFile,
  onCreateFile,
  onCreateFolder,
  onRename,
  onDelete,
  onRefresh,
  onSelectTab,
  onCloseTab,
  onChange,
  onSave,
  onBuild,
  onFlash,
  onMonitor,
  selectedBoard,
  logs,
  buildHistory,
  toolAction,
}: {
  activeProject: ProjectResponse | null;
  entries: ReturnType<typeof usePromptForgeWorkspace>["entries"];
  activePath: string | null | undefined;
  selectedFile: ReturnType<typeof usePromptForgeWorkspace>["selectedFile"];
  tabs: ReturnType<typeof usePromptForgeWorkspace>["tabs"];
  editorSettings: {
    fontSize?: number;
    tabSize?: number;
    wordWrap?: boolean;
    minimapEnabled?: boolean;
    showLineNumbers?: boolean;
  };
  onOpenFile: ReturnType<typeof usePromptForgeWorkspace>["openFile"];
  onCreateFile: ReturnType<typeof usePromptForgeWorkspace>["createEntry"];
  onCreateFolder: ReturnType<typeof usePromptForgeWorkspace>["createEntry"];
  onRename: ReturnType<typeof usePromptForgeWorkspace>["renameEntry"];
  onDelete: ReturnType<typeof usePromptForgeWorkspace>["deleteEntry"];
  onRefresh: () => void;
  onSelectTab: ReturnType<typeof usePromptForgeWorkspace>["openFile"];
  onCloseTab: ReturnType<typeof usePromptForgeWorkspace>["closeTab"];
  onChange: ReturnType<typeof usePromptForgeWorkspace>["updateTabContent"];
  onSave: ReturnType<typeof usePromptForgeWorkspace>["saveFile"];
  onBuild: () => void;
  onFlash: () => void;
  onMonitor: () => void;
  selectedBoard: DetectedBoard | null;
  logs: ConsoleEntry[];
  buildHistory: BuildHistoryItem[];
  toolAction: string | null;
}) {
  const latest = buildHistory[0];
  const buildStatus = toolAction === "build" ? "Building" : latest?.status ?? "Idle";
  const flashStatus = toolAction === "flash" ? "Flashing" : "Ready";
  const serialLogs = logs.filter((log) => log.channel === "serial").slice(-6);

  return (
    <div className="fx-ref-build-view">
      <div className="fx-ref-breadcrumb">Projects / {activeProject?.project_name ?? "Workspace"} / Build Studio</div>
      <section className="fx-ref-build-frame">
        <div className="fx-ref-build-explorer">
          <IdeExplorer
            activeProject={activeProject}
            entries={entries}
            selectedPath={activePath}
            onOpenFile={onOpenFile}
            onCreateFile={(basePath) => onCreateFile("file", basePath)}
            onCreateFolder={(basePath) => onCreateFolder("folder", basePath)}
            onRename={onRename}
            onDelete={onDelete}
            onRefresh={onRefresh}
          />
        </div>
        <div className="fx-ref-build-editor">
          <EditorWorkbench
            activeProject={activeProject}
            selectedFile={selectedFile}
            tabs={tabs}
            activePath={activePath}
            onSelectTab={onSelectTab}
            onCloseTab={onCloseTab}
            onChange={onChange}
            onSave={onSave}
            editorSettings={editorSettings}
          />
        </div>
        <aside className="fx-ref-build-inspector">
          <Panel title="Build Inspector" icon={SlidersHorizontal}>
            <div className="fx-ref-form-stack">
              <label><span>Build Target</span><button>{selectedBoard ? boardIdFromDetected(selectedBoard) : activeProject?.target_board ?? "No board"} <ChevronDown className="h-4 w-4" /></button></label>
              <label><span>Environment</span><button>{activeProject?.framework ?? "PlatformIO"} <em>Active</em></button></label>
            </div>
          </Panel>
          <Panel title="Compile Status" icon={CheckCircle2}>
            <BuildStatusLine label={buildStatus} detail={latest ? `Built ${formatRelative(latest.timestamp)}` : "No build recorded"} tone={toneForStatus(buildStatus)} />
          </Panel>
          <Panel title="Flash Status" icon={Zap}>
            <BuildStatusLine label={flashStatus} detail={selectedBoard?.port ?? "Select a serial device"} tone={toneForStatus(flashStatus)} />
          </Panel>
          <Panel title="Serial Monitor" icon={Terminal}>
            <pre className="fx-ref-serial-box">
              {serialLogs.length
                ? serialLogs.map((log) => `[${formatRelative(log.timestamp)}] ${log.message}`).join("\n")
                : "[ForgeX] Serial monitor ready\n[ForgeX] Open serial to inspect device output"}
            </pre>
          </Panel>
        </aside>
      </section>

      <section className="fx-ref-build-summary">
        <MetricCard icon={Folder} label="Project" value={activeProject?.project_name ?? "No project"} />
        <MetricCard icon={Cpu} label="Target Board" value={selectedBoard ? boardIdFromDetected(selectedBoard) ?? selectedBoard.board_type : activeProject?.target_board ?? "Unknown"} />
        <MetricCard icon={Gauge} label="Build Time" value={formatDuration(latest?.duration_ms)} sub={latest ? formatRelative(latest.timestamp) : "Not run"} />
        <MetricCard icon={Zap} label="Flash Time" value={toolAction === "flash" ? "Running" : "Ready"} />
        <MetricCard icon={Box} label="Binary Size" value="Pending" sub="Build artifact" />
      </section>

      <section className="fx-ref-build-actions">
        <PrimaryButton icon={Wrench} onClick={onBuild} disabled={toolAction === "build"}>Build</PrimaryButton>
        <PrimaryButton icon={Zap} onClick={onFlash} disabled={toolAction === "flash"}>Flash</PrimaryButton>
        <GhostButton icon={Terminal} onClick={onMonitor}>Open Serial</GhostButton>
        <GhostButton icon={Sparkles}>Ask ForgeX to Repair</GhostButton>
      </section>
    </div>
  );
}

function BuildStatusLine({ label, detail, tone }: { label: string; detail: string; tone: Tone }) {
  return (
    <div className="fx-ref-build-status">
      <CheckCircle2 className={`h-5 w-5 tone-${tone}`} />
      <div><strong className={`tone-${tone}`}>{label}</strong><p>{detail}</p></div>
    </div>
  );
}

function MetricCard({ icon: Icon, label, value, sub }: { icon: LucideIcon; label: string; value: string; sub?: string }) {
  return (
    <div className="fx-ref-metric-card">
      <Icon className="h-5 w-5" />
      <span>{label}</span>
      <strong>{value}</strong>
      {sub ? <p>{sub}</p> : null}
    </div>
  );
}

function SettingsOverviewView({ onView, health, selectedBoard, modelLabelText }: {
  onView: (view: ViewId) => void;
  health: ReturnType<typeof usePromptForgeWorkspace>["health"];
  selectedBoard: DetectedBoard | null;
  modelLabelText: string;
}) {
  const overview = [
    { label: "App Version", value: health?.version ?? "v1.3.2", body: "Latest version installed", icon: Layers, tone: "blue" as Tone },
    { label: "License Status", value: "Pro License", body: "Workspace enabled", icon: ShieldCheck, tone: "green" as Tone },
    { label: "Backend Status", value: health?.status === "healthy" ? "Connected" : "Check backend", body: "Core services", icon: Cloud, tone: health?.status === "healthy" ? "green" as Tone : "yellow" as Tone },
    { label: "Active Device", value: selectedBoard ? boardIdFromDetected(selectedBoard) ?? selectedBoard.board_type : "No device", body: selectedBoard?.port ?? "Connect hardware", icon: Cpu, tone: selectedBoard ? "purple" as Tone : "yellow" as Tone },
    { label: "Default Model", value: modelLabelText.split("/")[0]?.trim() || "ForgeX Pro", body: "Optimized for embedded", icon: Box, tone: "yellow" as Tone },
  ];
  const categories = [
    { view: "connections" as ViewId, title: "Connections", body: "Configure backend, MQTT, and external service connections.", icon: Link2, tone: "blue" as Tone },
    { view: "models" as ViewId, title: "Models", body: "Manage AI models, firmware targets, and default preferences.", icon: Box, tone: "cyan" as Tone },
    { view: "models" as ViewId, title: "Routing", body: "Configure routing rules, failover behavior, and endpoint priorities.", icon: SlidersHorizontal, tone: "purple" as Tone },
    { view: "settings" as ViewId, title: "Policies", body: "Set workspace policies, safety rules, and execution guardrails.", icon: ShieldCheck, tone: "yellow" as Tone },
    { view: "hardware" as ViewId, title: "Hardware", body: "Manage connected devices, interfaces, and hardware preferences.", icon: Microchip, tone: "green" as Tone },
    { view: "settings" as ViewId, title: "Workspace", body: "Manage team members, roles, and workspace settings.", icon: Users, tone: "blue" as Tone },
    { view: "settings" as ViewId, title: "Appearance", body: "Customize theme, accent color, and display preferences.", icon: Eye, tone: "purple" as Tone },
    { view: "settings" as ViewId, title: "Notifications", body: "Configure in-app alerts, email notifications, and quiet hours.", icon: Bell, tone: "yellow" as Tone },
    { view: "settings" as ViewId, title: "About", body: "View product information, third-party licenses, and acknowledgments.", icon: CircleDot, tone: "cyan" as Tone },
  ];

  return (
    <div className="fx-ref-page">
      <h1>Settings Overview</h1>
      <p>Manage your workspace, connections, models, and system preferences.</p>
      <div className="fx-ref-overview-grid">
        {overview.map((item) => {
          const Icon = item.icon;
          return (
            <Panel key={item.label}>
              <div className="fx-ref-overview-card">
                <Icon className={`h-7 w-7 tone-${item.tone}`} />
                <span>{item.label}</span>
                <strong>{item.value}</strong>
                <p>{item.body}</p>
                <button className="fx-ref-link">Manage <ArrowRight className="h-4 w-4" /></button>
              </div>
            </Panel>
          );
        })}
      </div>
      <div className="fx-ref-settings-grid">
        {categories.map((item) => {
          const Icon = item.icon;
          return (
            <button key={item.title} className="fx-ref-settings-card" onClick={() => onView(item.view)}>
              <Icon className={`h-7 w-7 tone-${item.tone}`} />
              <span><strong>{item.title}</strong><p>{item.body}</p></span>
              <ArrowRight className="ml-auto h-5 w-5" />
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ModelsRoutingView({ providers, routes }: { providers: ModelProviderResponse[]; routes: ModelRouteResponse[] }) {
  const defaultRoute = routeFor("code_generation", routes);
  const planningRoute = routeFor("planning", routes) ?? routeFor("requirements", routes);
  const visionRoute = routeFor("vision", routes);
  const routeCards = [
    { title: "Default Coding Model", value: modelLabel(defaultRoute, providers), icon: Code2 },
    { title: "Planning Model", value: modelLabel(planningRoute, providers), icon: SlidersHorizontal },
    { title: "Vision Model", value: modelLabel(visionRoute, providers), icon: Eye },
    { title: "Fallback Chain", value: `${routes.filter((route) => route.fallback_enabled).length} routes`, icon: ShieldCheck },
  ];
  const providerRows = providers.length ? providers : [
    { provider_id: "openai", display_name: "OpenAI", enabled: true, configured: false, auth_type: "api_key", local: false, default_model: "gpt-4o" },
    { provider_id: "anthropic", display_name: "Anthropic", enabled: true, configured: false, auth_type: "api_key", local: false, default_model: "claude" },
    { provider_id: "groq", display_name: "Groq", enabled: true, configured: false, auth_type: "api_key", local: false, default_model: "llama" },
  ] as ModelProviderResponse[];
  const routingRows = routes.length ? routes : [
    { task_type: "code_generation", provider_id: "openai", model_id: "gpt-4o", fallback_enabled: true, local_only: false },
    { task_type: "planning", provider_id: "anthropic", model_id: "claude-3.5-sonnet", fallback_enabled: true, local_only: false },
    { task_type: "recovery", provider_id: "mistral", model_id: "mixtral-8x22b", fallback_enabled: false, local_only: false },
  ] as ModelRouteResponse[];

  return (
    <div className="fx-ref-page fx-ref-models-layout">
      <section>
        <h1>Settings / Models & Routing</h1>
        <p>Configure models, providers, and intelligent routing for optimal performance and reliability.</p>
        <div className="fx-ref-route-card-grid">
          {routeCards.map((card) => {
            const Icon = card.icon;
            return (
              <Panel key={card.title}>
                <div className="fx-ref-route-card">
                  <Icon className="h-6 w-6 text-[var(--fx-text-muted)]" />
                  <span>{card.title}</span>
                  <button>{card.value}<ChevronDown className="h-4 w-4" /></button>
                </div>
              </Panel>
            );
          })}
        </div>
        <div className="fx-ref-provider-strip">
          {providerRows.slice(0, 5).map((provider) => (
            <button key={provider.provider_id} className={`fx-ref-provider-card ${provider.configured ? "connected" : ""}`}>
              <Box className="h-7 w-7" />
              <span><strong>{provider.display_name}</strong><em>{providerStatus(provider)}</em></span>
            </button>
          ))}
          <button className="fx-ref-provider-card add"><Plus className="h-6 w-6" /><span>Add Provider</span></button>
        </div>
        <Panel title="Routing Strategy">
          <div className="fx-ref-routing-list">
            {routingRows.slice(0, 4).map((route, index) => {
              const provider = providerFor(route.provider_id, providers);
              return (
                <div key={`${route.task_type}-${route.provider_id}`} className="fx-ref-routing-row">
                  <div className="fx-ref-routing-index">{index + 1}</div>
                  <span>
                    <strong>{index === 0 ? "Primary Route" : route.fallback_enabled ? "Fallback Route" : "Final Recovery Route"}</strong>
                    <p>{route.task_type.replaceAll("_", " ")}</p>
                  </span>
                  <span><small>Provider / Model</small>{provider?.display_name ?? route.provider_id}<br />{route.model_id}</span>
                  <span><small>Success (7d)</small>{route.local_only ? "Local" : "97.1%"}</span>
                  <span><small>Avg. Latency</small>{provider?.local ? "local" : "1.56s"}</span>
                  <button><ChevronRight className="h-4 w-4" /></button>
                </div>
              );
            })}
          </div>
        </Panel>
      </section>
      <aside className="fx-ref-model-rail">
        <Panel title="Execution Controls" icon={Settings}>
          <div className="fx-ref-settings-form">
            <label>Request Timeout<input value="45 seconds" readOnly /></label>
            <label>Max Retries<input value="2 retries" readOnly /></label>
            <ToggleRow label="Cache Responses" enabled />
            <ToggleRow label="Log Prompts & Responses" enabled />
            <ToggleRow label="Stream Responses" enabled />
            <PrimaryButton icon={Download}>Save Changes</PrimaryButton>
            <GhostButton icon={RefreshCw}>Reset to Defaults</GhostButton>
          </div>
        </Panel>
        <Panel title="Performance Overview">
          <div className="fx-ref-performance-grid">
            <MetricCard icon={Gauge} label="Success Rate" value="97.1%" sub="+2.4%" />
            <MetricCard icon={Activity} label="Avg. Response" value="1.56s" sub="-8.7%" />
            <MetricCard icon={Database} label="Total Requests" value="24,892" sub="+18.3%" />
            <MetricCard icon={AlertTriangle} label="Error Rate" value="2.9%" sub="-1.2%" />
          </div>
        </Panel>
      </aside>
    </div>
  );
}

function ToggleRow({ label, enabled }: { label: string; enabled: boolean }) {
  return (
    <div className="fx-ref-toggle-row">
      <span>{label}</span>
      <button className={enabled ? "on" : ""}><span /></button>
    </div>
  );
}

function ConnectionsView({ providers }: { providers: ModelProviderResponse[] }) {
  const providerRows = providers.length ? providers : [
    { provider_id: "openai", display_name: "OpenAI API", enabled: true, configured: false, auth_type: "api_key", local: false, default_model: "gpt-4o" },
    { provider_id: "openrouter", display_name: "OpenRouter API", enabled: true, configured: false, auth_type: "api_key", local: false, default_model: "auto" },
    { provider_id: "anthropic", display_name: "Anthropic API", enabled: true, configured: false, auth_type: "api_key", local: false, default_model: "claude" },
    { provider_id: "groq", display_name: "Groq API", enabled: true, configured: false, auth_type: "api_key", local: false, default_model: "llama" },
  ] as ModelProviderResponse[];
  const selected = providerRows[0];

  return (
    <div className="fx-ref-page">
      <h1>Settings / Connections</h1>
      <p>Manage API providers and CLI connections for your workspace.</p>
      <div className="fx-ref-connections-layout">
        <Panel>
          <div className="fx-ref-preferences-sidebar">
            {["General", "Appearance", "Notifications", "Keys & Secrets", "Profile", "Members", "Billing", "Usage", "Connections", "Webhooks", "Marketplace", "Update", "Diagnostics", "About"].map((item) => (
              <button key={item} className={item === "Connections" ? "active" : ""}>{item}</button>
            ))}
          </div>
        </Panel>
        <Panel title="API Providers" action={<button className="fx-ref-link"><Plus className="h-4 w-4" /> Add Provider</button>}>
          <div className="fx-ref-provider-list">
            {providerRows.map((provider) => (
              <button key={provider.provider_id} className={provider.provider_id === selected.provider_id ? "active" : ""}>
                <Box className="h-6 w-6" />
                <span><strong>{provider.display_name}</strong><em>{providerStatus(provider)}</em></span>
                <ChevronRight className="h-4 w-4" />
              </button>
            ))}
          </div>
          <div className="fx-ref-section-label">CLI Connections</div>
          <div className="fx-ref-provider-list">
            {["Codex CLI", "Google Antigravity CLI"].map((item) => (
              <button key={item}>
                <Terminal className="h-6 w-6" />
                <span><strong>{item}</strong><em>Connected</em></span>
                <ChevronRight className="h-4 w-4" />
              </button>
            ))}
          </div>
        </Panel>
        <Panel>
          <div className="fx-ref-provider-detail">
            <div className="fx-ref-provider-header">
              <Box className="h-10 w-10" />
              <span><strong>{selected.display_name}</strong><em>{providerStatus(selected)}</em></span>
            </div>
            <div className="fx-ref-tab-row"><button className="active">Overview</button><button>Models</button><button>Usage</button><button>Advanced</button></div>
            <div className="fx-ref-health-grid">
              <MetricCard icon={ShieldCheck} label="Authentication" value={selected.configured ? "Success" : "Missing"} sub={selected.configured ? "API key valid" : "Add credentials"} />
              <MetricCard icon={Activity} label="Health" value={selected.health_status ?? "Healthy"} sub="Latest check" />
              <MetricCard icon={History} label="Last checked" value={formatRelative(selected.last_checked_at)} sub={selected.last_checked_at ? formatDate(selected.last_checked_at) : "Pending"} />
              <MetricCard icon={Gauge} label="Rate limit" value="9,832 / 10,000" sub="Requests remaining" />
            </div>
            <div className="fx-ref-api-config">
              <h3>API Configuration</h3>
              <label>API Key<div><input readOnly value={selected.api_key_masked ?? selected.masked_api_key ?? "********************************"} /><button>Reveal</button></div></label>
              <label>Base URL<input readOnly value={selected.base_url ?? "https://api.openai.com/v1"} /></label>
              <button><ChevronRight className="h-4 w-4" /> Additional Headers (Optional)</button>
            </div>
            <div className="fx-ref-provider-actions">
              <PrimaryButton icon={Download}>Save Key</PrimaryButton>
              <GhostButton icon={Activity}>Test Connection</GhostButton>
              <GhostButton icon={Trash2} danger>Disconnect</GhostButton>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function HardwareView({
  activeProject,
  selectedBoard,
  boards,
  monitorStatus,
  logs,
  onBuild,
  onFlash,
  onMonitor,
  onStopMonitor,
  onRefreshBoards,
}: {
  activeProject: ProjectResponse | null;
  selectedBoard: DetectedBoard | null;
  boards: DetectedBoard[];
  monitorStatus: ReturnType<typeof usePromptForgeWorkspace>["monitorStatus"];
  logs: ConsoleEntry[];
  onBuild: () => void;
  onFlash: () => void;
  onMonitor: () => void;
  onStopMonitor: () => void;
  onRefreshBoards: () => void;
}) {
  const serialLogs = logs.filter((log) => log.channel === "serial").slice(-10);
  const boardName = selectedBoard ? boardIdFromDetected(selectedBoard) ?? selectedBoard.board_type : activeProject?.target_board ?? "No board";

  return (
    <div className="fx-ref-page">
      <h1>Settings / Hardware & Device</h1>
      <p>Configure and manage your connected hardware and device settings.</p>
      <div className="fx-ref-tabs">
        {["General", "Hardware", "Build & Flash", "AI Assistant", "Integrations", "Advanced"].map((item) => (
          <button key={item} className={item === "Hardware" ? "active" : ""}>{item}</button>
        ))}
      </div>
      <div className="fx-ref-hardware-grid">
        <Panel title="Connected Board" icon={Box} action={<button className="fx-ref-icon-only"><ChevronDown className="h-4 w-4" /></button>}>
          <div className="fx-ref-board-card">
            <div className="fx-ref-board-visual"><Microchip className="h-16 w-16" /></div>
            <div>
              <h3>{boardName}</h3>
              <p>{selectedBoard?.description ?? "ESP32 development board"}</p>
              <p>{selectedBoard?.manufacturer ?? "Dual-core MCU, Wi-Fi, BLE"}</p>
              <dl><dt>Port</dt><dd>{selectedBoard?.port ?? "Unknown"}</dd><dt>Flash Size</dt><dd>4MB</dd><dt>Chip Revision</dt><dd>v1.1</dd></dl>
              <GhostButton icon={Eye}>View Pinout</GhostButton>
            </div>
          </div>
        </Panel>
        <Panel title="Connection" icon={Link2}>
          <dl className="fx-ref-definition-list">
            <dt>Port</dt><dd>{selectedBoard?.port ?? "No port"}</dd>
            <dt>Baud Rate</dt><dd>{monitorStatus?.baudrate ?? 115200}</dd>
            <dt>Data Bits</dt><dd>8</dd>
            <dt>Stop Bits</dt><dd>1</dd>
            <dt>Parity</dt><dd>None</dd>
            <dt>Flow Control</dt><dd>None</dd>
          </dl>
          <GhostButton icon={Activity} onClick={onRefreshBoards}>Test Connection</GhostButton>
        </Panel>
        <Panel title="Quick Actions" icon={Zap}>
          <div className="fx-ref-quick-actions">
            <PrimaryButton icon={Cpu} onClick={onFlash}>Flash Firmware</PrimaryButton>
            <GhostButton icon={Trash2}>Erase Flash</GhostButton>
            <GhostButton icon={Download}>Read Flash</GhostButton>
            <GhostButton icon={Upload}>Upload Filesystem</GhostButton>
            <GhostButton icon={RefreshCw}>Restart</GhostButton>
            <GhostButton icon={Terminal} onClick={onMonitor}>Open Serial Monitor</GhostButton>
          </div>
        </Panel>
        <Panel title="Device Overview" icon={CircleDot}>
          <dl className="fx-ref-definition-list">
            <dt>Chip Model</dt><dd>{selectedBoard?.board_type ?? "ESP32"}</dd>
            <dt>CPU Cores</dt><dd>2</dd>
            <dt>Max Clock</dt><dd>240 MHz</dd>
            <dt>Flash Size</dt><dd>4 MB</dd>
            <dt>SDK Version</dt><dd>v5.1.2</dd>
            <dt>Boot Mode</dt><dd>SPI</dd>
          </dl>
        </Panel>
        <Panel title="Serial Monitor" icon={Terminal}>
          <div className="fx-ref-form-stack">
            <label><span>Baud Rate</span><button>{monitorStatus?.baudrate ?? 115200}<ChevronDown className="h-4 w-4" /></button></label>
            <label><span>Line Ending</span><button>Both<ChevronDown className="h-4 w-4" /></button></label>
          </div>
          <div className="fx-ref-inline-actions"><GhostButton icon={Trash2}>Clear</GhostButton><GhostButton icon={Monitor} onClick={monitorStatus?.connected ? onStopMonitor : onMonitor}>{monitorStatus?.connected ? "Close" : "Open"}</GhostButton></div>
        </Panel>
        <Panel title="Bootloader Tools" icon={Settings}>
          <dl className="fx-ref-definition-list">
            <dt>Bootloader Version</dt><dd>v3.0.0</dd>
            <dt>Partition Table</dt><dd>factory_default</dd>
            <dt>Flash Mode</dt><dd>QIO</dd>
            <dt>Flash Frequency</dt><dd>80MHz</dd>
          </dl>
          <GhostButton icon={Eye}>Open Bootloader</GhostButton>
        </Panel>
        <Panel title="Power & Reset" icon={Gauge}>
          <div className="fx-ref-rail-actions">
            <GhostButton icon={RefreshCw}>Reset (EN)</GhostButton>
            <GhostButton icon={Download}>Download Boot</GhostButton>
          </div>
          <dl className="fx-ref-definition-list">
            <dt>Power Source</dt><dd>USB</dd>
            <dt>Voltage</dt><dd>5.05 V</dd>
            <dt>Current</dt><dd>182 mA</dd>
          </dl>
        </Panel>
        <Panel title="Safety Checks" icon={ShieldCheck}>
          <div className="fx-ref-safety-list">
            {["Power Stability", "Board Connection", "Flash Integrity", "Bootloader Protection", "Temperature"].map((item) => (
              <div key={item}><CheckCircle2 className="h-4 w-4 tone-green" /><span>{item}<small>Passed</small></span></div>
            ))}
          </div>
          <GhostButton icon={RefreshCw} onClick={onBuild}>Run All Checks</GhostButton>
        </Panel>
        <Panel title="Live Device Log" icon={Terminal}>
          <pre className="fx-ref-live-log">
            {serialLogs.length
              ? serialLogs.map((log) => `${formatRelative(log.timestamp)} ${log.message}`).join("\n")
              : "10:24:11.123 I (boot): ESP-IDF bootloader\n10:24:11.140 I (app): Starting application\n10:24:11.522 I (wifi): Connected to ForgeX_Network\n10:24:12.001 I (app): Device is ready"}
          </pre>
          <div className="fx-ref-log-footer">
            <label><input type="checkbox" defaultChecked /> Autoscroll</label>
            <label><input type="checkbox" defaultChecked /> Show Timestamps</label>
            <span>{boards.length} device(s)</span>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function HistoryView({ builds }: { builds: BuildHistoryItem[] }) {
  const rows = builds.length ? builds : [
    { execution_id: "7f3c9d2e", timestamp: new Date().toISOString(), board: "ESP32 DevKit V1", status: "succeeded", duration_ms: 161000 },
    { execution_id: "4a91b7c1", timestamp: new Date(Date.now() - 18 * 60_000).toISOString(), board: "ESP32-S3", status: "succeeded", duration_ms: 82000 },
    { execution_id: "b1d44f90", timestamp: new Date(Date.now() - 3 * 60 * 60_000).toISOString(), board: "STM32F407G-DISCO", status: "failed", duration_ms: 185000 },
  ] as BuildHistoryItem[];

  return (
    <div className="fx-ref-page">
      <h1>Run History</h1>
      <p>Review and audit past task executions.</p>
      <Panel>
        <div className="fx-ref-filter-bar">
          <label><Search className="h-5 w-5" /><input placeholder="Search tasks or runs..." /></label>
          <button>Board <span>All Boards</span><ChevronDown className="h-4 w-4" /></button>
          <button>Model <span>All Models</span><ChevronDown className="h-4 w-4" /></button>
          <button>Result <span>All Results</span><ChevronDown className="h-4 w-4" /></button>
          <GhostButton icon={Download}>Export</GhostButton>
        </div>
      </Panel>
      <Panel>
        <div className="fx-ref-history-table">
          <div className="head"><span>Task / Run</span><span>Board</span><span>Model</span><span>Duration</span><span>Build Result</span><span>Flash Result</span><span>Timestamp</span></div>
          {rows.map((row, index) => (
            <div key={row.execution_id} className={`fx-ref-history-row ${index === 0 ? "expanded" : ""}`}>
              <span><strong>{index === 0 ? "OTA update with version check" : row.board}</strong><small>Run ID: {compactId(row.execution_id)}</small></span>
              <span>{row.board}</span>
              <span>ForgeX Pro</span>
              <span>{formatDuration(row.duration_ms)}</span>
              <span className={`tone-${toneForStatus(row.status)}`}>{row.status}</span>
              <span className={index === 2 ? "tone-red" : "tone-green"}>{index === 2 ? "Failed" : "Succeeded"}</span>
              <span>{formatDate(row.timestamp)}</span>
              {index === 0 ? (
                <div className="fx-ref-history-expanded">
                  <div className="fx-ref-tab-row"><button className="active">Overview</button><button>Conversation</button><button>Patch</button><button>Build Logs</button><button>Verification</button><button>Recovery</button></div>
                  <div className="fx-ref-history-metrics">
                    <MetricCard icon={MessageSquare} label="Conversation Summary" value="14 messages" sub="User: 5  AI: 9" />
                    <MetricCard icon={Braces} label="Patch Summary" value="3 files changed" sub="+128  -37" />
                    <MetricCard icon={Terminal} label="Build Logs Summary" value="0 errors" sub="12 warnings" />
                    <MetricCard icon={ShieldCheck} label="Verification Result" value="Passed" sub="All checks passed" />
                    <MetricCard icon={Wrench} label="Recovery Actions" value="0" sub="No recovery needed" />
                  </div>
                  <div className="fx-ref-warning-banner"><AlertTriangle className="h-5 w-5" /> Verification failure detected. Review logs for more details. <button>View Logs</button></div>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

function PlaceholderView({ title, body, icon: Icon }: { title: string; body: string; icon: LucideIcon }) {
  return (
    <div className="fx-ref-page">
      <h1>{title}</h1>
      <Panel>
        <EmptyState title={title} body={body} action={<GhostButton icon={Icon}>Open</GhostButton>} />
      </Panel>
    </div>
  );
}

export function ForgeXShell() {
  const dialogs = useForgeXDialogs();
  const workspace = usePromptForgeWorkspace();
  const settingsState = useForgeXSettings();
  const [view, setView] = useState<ViewId>("home");
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [taskGoal, setTaskGoal] = useState(DEFAULT_PROMPT);
  const [isOpeningWorkspace, setIsOpeningWorkspace] = useState(false);
  const [selectedBoardPort, setSelectedBoardPort] = useState<string | null>(null);
  const [modelProviders, setModelProviders] = useState<ModelProviderResponse[]>([]);
  const [modelRoutes, setModelRoutes] = useState<ModelRouteResponse[]>([]);

  const projects = safeArray(workspace.projects);
  const entries = safeArray(workspace.entries);
  const tabs = safeArray(workspace.tabs);
  const logs = safeArray(workspace.logs);
  const buildHistory = safeArray(workspace.buildHistory);
  const detectedBoards = safeArray(workspace.detectedBoards);
  const stages = safeArray(workspace.stages).length ? safeArray(workspace.stages) : DEFAULT_STEPS;
  const providers = safeArray(modelProviders);
  const routes = safeArray(modelRoutes);
  const selectedBoard = useMemo(
    () => detectedBoards.find((board) => board.port === selectedBoardPort) ?? detectedBoards[0] ?? null,
    [detectedBoards, selectedBoardPort],
  );
  const selectedBoardLabel = boardIdFromProject(workspace.activeProject) ?? boardIdFromDetected(selectedBoard) ?? "No board";
  const codeRoute = routeFor("code_generation", routes);
  const modelLabelText = modelLabel(codeRoute, providers);

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

  const refreshModelRouter = useCallback(async () => {
    try {
      const [providerResponse, routeResponse] = await Promise.all([
        promptForgeApi.modelProviders(),
        promptForgeApi.modelRoutes(),
      ]);
      setModelProviders(safeArray(providerResponse.providers));
      setModelRoutes(safeArray(routeResponse.routes));
    } catch {
      setModelProviders([]);
      setModelRoutes([]);
    }
  }, []);

  useEffect(() => {
    void refreshModelRouter();
    const timer = window.setInterval(() => void refreshModelRouter(), 15_000);
    return () => window.clearInterval(timer);
  }, [refreshModelRouter]);

  useEffect(() => {
    if (!selectedBoardPort && detectedBoards[0]) {
      setSelectedBoardPort(detectedBoards[0].port);
    }
  }, [detectedBoards, selectedBoardPort]);

  const openWorkspace = useCallback(async () => {
    if (isOpeningWorkspace) return;
    setIsOpeningWorkspace(true);
    try {
      let selectedPath: string | null = null;
      if (window.forgexDesktop?.openFolder) {
        const result = await window.forgexDesktop.openFolder();
        if (result.canceled) return;
        selectedPath = result.path;
      } else {
        selectedPath = await dialogs.input({
          title: "Open workspace",
          description: "Enter an absolute folder path available to the local ForgeX backend.",
          label: "Workspace path",
          defaultValue: workspace.desktopWorkspacePath ?? "",
          placeholder: "C:\\projects\\firmware",
          confirmText: "Open workspace",
          validate: (value) => (value.trim() ? null : "Enter a folder path."),
        });
      }
      if (!selectedPath?.trim()) return;
      await workspace.importProjectPath(selectedPath.trim());
      setView("projects");
    } catch (error) {
      const message = error instanceof Error ? error.message : "The workspace picker failed unexpectedly.";
      workspace.addLog({ channel: "error", message: `Open workspace failed: ${message}` });
      await dialogs.message({ title: "Workspace could not be opened", description: message });
    } finally {
      setIsOpeningWorkspace(false);
    }
  }, [dialogs, isOpeningWorkspace, workspace]);

  const startTask = useCallback(() => {
    const value = prompt.trim() || DEFAULT_PROMPT;
    setTaskGoal(value);
    setView("task");
    void workspace.execute(value, {
      selectedBoard: boardIdFromProject(workspace.activeProject) ?? boardIdFromDetected(selectedBoard),
      selectedFramework: "PlatformIO",
      generationMode: generationModeFromProject(workspace.activeProject),
    });
  }, [prompt, selectedBoard, workspace]);

  const renderView = () => {
    if (view === "home") {
      return (
        <HomeView
          prompt={prompt}
          setPrompt={setPrompt}
          onStart={startTask}
          onOpenWorkspace={() => void openWorkspace()}
          onView={setView}
          selectedBoardLabel={selectedBoardLabel}
          modelLabelText={modelLabelText}
          recentBuilds={buildHistory}
          health={workspace.health}
          selectedBoard={selectedBoard}
        />
      );
    }
    if (view === "task") {
      return (
        <TaskView
          prompt={prompt}
          setPrompt={setPrompt}
          taskGoal={taskGoal}
          stages={stages}
          logs={logs}
          activeProject={workspace.activeProject}
          selectedBoard={selectedBoard}
          selectedBoardLabel={selectedBoardLabel}
          isExecuting={workspace.isExecuting}
          isCancelling={workspace.isCancelling}
          taskId={workspace.taskId}
          onStart={startTask}
          onCancel={() => void workspace.cancelExecution()}
          onView={setView}
        />
      );
    }
    if (view === "projects") {
      return (
        <BuildStudioView
          activeProject={workspace.activeProject}
          entries={entries}
          activePath={workspace.activePath}
          selectedFile={workspace.selectedFile}
          tabs={tabs}
          editorSettings={editorSettings}
          onOpenFile={workspace.openFile}
          onCreateFile={workspace.createEntry}
          onCreateFolder={workspace.createEntry}
          onRename={workspace.renameEntry}
          onDelete={workspace.deleteEntry}
          onRefresh={() => void workspace.refreshProjectFiles()}
          onSelectTab={workspace.openFile}
          onCloseTab={workspace.closeTab}
          onChange={workspace.updateTabContent}
          onSave={workspace.saveFile}
          onBuild={() => void workspace.buildActiveProject()}
          onFlash={() => void workspace.flashActiveProject(selectedBoard)}
          onMonitor={() => void workspace.startSerialMonitor(selectedBoard)}
          selectedBoard={selectedBoard}
          logs={logs}
          buildHistory={buildHistory}
          toolAction={workspace.toolAction}
        />
      );
    }
    if (view === "history") return <HistoryView builds={buildHistory} />;
    if (view === "settings") return <SettingsOverviewView onView={setView} health={workspace.health} selectedBoard={selectedBoard} modelLabelText={modelLabelText} />;
    if (view === "models") return <ModelsRoutingView providers={providers} routes={routes} />;
    if (view === "connections") return <ConnectionsView providers={providers} />;
    if (view === "hardware" || view === "device") {
      return (
        <HardwareView
          activeProject={workspace.activeProject}
          selectedBoard={selectedBoard}
          boards={detectedBoards}
          monitorStatus={workspace.monitorStatus}
          logs={logs}
          onBuild={() => void workspace.buildActiveProject()}
          onFlash={() => void workspace.flashActiveProject(selectedBoard)}
          onMonitor={() => void workspace.startSerialMonitor(selectedBoard)}
          onStopMonitor={() => void workspace.stopSerialMonitor()}
          onRefreshBoards={() => void workspace.refreshDetectedBoards()}
        />
      );
    }
    if (view === "templates") return <PlaceholderView title="Templates" body="Reusable firmware prompts, board recipes, and validation policies will live here." icon={ListChecks} />;
    if (view === "integrations") return <PlaceholderView title="Integrations" body="Connect issue trackers, artifact stores, telemetry, and team tools." icon={Blocks} />;
    return null;
  };

  return (
    <main className="fx-reference-shell">
      <ShellSidebar
        view={view === "hardware" ? "device" : view}
        onView={(nextView) => setView(nextView === "device" ? "hardware" : nextView)}
        onNewTask={() => {
          setPrompt(DEFAULT_PROMPT);
          setView("home");
        }}
      />
      <section className="fx-ref-main">
        <TopStatusBar health={workspace.health} selectedBoard={selectedBoard} />
        <div className="fx-ref-scroll">{renderView()}</div>
      </section>
      {projects.length === 0 && !workspace.activeProject ? (
        <button className="fx-ref-floating-open" onClick={() => void openWorkspace()} disabled={isOpeningWorkspace}>
          <Folder className="h-4 w-4" />
          {isOpeningWorkspace ? "Opening..." : "Open workspace"}
        </button>
      ) : null}
    </main>
  );
}
