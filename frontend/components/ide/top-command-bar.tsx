"use client";

import { BrainCircuit, ChevronDown, Cpu, FolderOpen, Hammer, LoaderCircle, Play, Radio, Search, Settings, Upload, Wifi, WifiOff } from "lucide-react";

import type { HealthResponse, ProjectResponse } from "@/types";

interface TopCommandBarProps {
  health: HealthResponse | null;
  projects: ProjectResponse[];
  activeProject: ProjectResponse | null;
  selectedBoardLabel: string;
  command: string;
  isExecuting: boolean;
  isOpeningWorkspace: boolean;
  toolAction: "build" | "flash" | "monitor" | null;
  socketState: "idle" | "connecting" | "reconnecting" | "open" | "closed";
  modelRouteLabel: string;
  onCommandChange: (value: string) => void;
  onSubmitCommand: () => void;
  onRunBuild: () => void;
  onFlash: () => void;
  onMonitor: () => void;
  onOpenWorkspace: () => void;
  onOpenModels: () => void;
  onOpenSettings: () => void;
  onSelectProject: (project: ProjectResponse) => void;
}

export function TopCommandBar({
  health,
  projects,
  activeProject,
  selectedBoardLabel,
  command,
  isExecuting,
  isOpeningWorkspace,
  toolAction,
  socketState,
  modelRouteLabel,
  onCommandChange,
  onSubmitCommand,
  onRunBuild,
  onFlash,
  onMonitor,
  onOpenWorkspace,
  onOpenModels,
  onOpenSettings,
  onSelectProject,
}: TopCommandBarProps) {
  const safeProjects = Array.isArray(projects) ? projects : [];
  const safeModelRouteLabel = modelRouteLabel?.trim() || "Not configured";
  const connected = Boolean(health);
  const statusLabel = connected
    ? health?.service === "forgex-backend"
      ? "Backend connected"
      : "Connected"
    : "Backend offline";

  return (
    <header className="flex h-[58px] min-w-0 shrink-0 items-center gap-2.5 overflow-hidden border-b px-3" style={{ backgroundColor: "var(--fx-header)", borderColor: "var(--fx-border)", color: "var(--fx-text)" }}>
      <div className="flex min-w-[150px] shrink-0 items-center gap-2.5 xl:min-w-[178px]">
        <div className="min-w-0">
          <div className="text-[14px] font-semibold leading-4 tracking-tight">ForgeX Studio</div>
          <div className="truncate text-[9px] font-medium uppercase leading-4 tracking-[.14em] text-[var(--fx-text-muted)]">Firmware workspace</div>
        </div>
      </div>

      <form
        className="fx-command flex h-9 min-w-[180px] max-w-[620px] flex-1 items-center gap-2 px-3 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmitCommand();
        }}
      >
        <Search className="h-4 w-4 shrink-0 text-[var(--fx-text-muted)]" />
        <input
          className="min-w-0 flex-1 bg-transparent text-[var(--fx-text)] outline-none placeholder:text-[var(--fx-text-muted)]"
          value={command}
          onChange={(event) => onCommandChange(event.target.value)}
          placeholder="Ask ForgeX to create, repair, or inspect firmware…"
          disabled={isExecuting}
        />
      </form>

      <div className="ml-auto flex min-w-0 items-center gap-1.5 overflow-x-auto">
        <button
          type="button"
          className="fx-top-chip hidden h-8 max-w-[200px] shrink-0 items-center gap-2 px-2 text-xs xl:flex"
          onClick={onOpenModels}
          title="Open model settings"
        >
          <BrainCircuit className="h-4 w-4 shrink-0 text-[var(--fx-info)]" />
          <span className="truncate">Model: {safeModelRouteLabel}</span>
        </button>

        <label className="fx-top-chip flex h-8 min-w-[170px] max-w-[220px] shrink-0 items-center gap-2 px-2 text-xs text-[var(--fx-code-text)]">
          <Cpu className="h-4 w-4 text-[var(--fx-info)]" />
          <select
            className="min-w-0 flex-1 appearance-none bg-transparent text-[var(--fx-text)] outline-none"
            value={activeProject?.project_id ?? ""}
            onChange={(event) => {
              const selected = safeProjects.find((project) => project.project_id === event.target.value);
              if (selected) onSelectProject(selected);
            }}
          >
            {activeProject ? null : <option value="">No project</option>}
            {safeProjects.map((project) => (
              <option key={project.project_id} value={project.project_id}>
                {project.target_board || project.project_name}
              </option>
            ))}
          </select>
          <ChevronDown className="h-3 w-3 shrink-0 text-[var(--fx-text-muted)]" />
        </label>

        <button
          type="button"
          className="fx-top-chip flex h-8 shrink-0 items-center gap-2 px-2.5 text-xs disabled:cursor-wait disabled:opacity-60"
          onClick={onOpenWorkspace}
          disabled={isOpeningWorkspace}
          title="Open a workspace folder"
        >
          {isOpeningWorkspace ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <FolderOpen className="h-4 w-4" />}
          <span className="hidden 2xl:inline">Open workspace</span>
        </button>

        <div
          className={`flex h-8 shrink-0 items-center gap-2 rounded-lg border px-2 text-xs ${
            connected ? "border-[var(--fx-success)] bg-[var(--fx-success-soft)] text-[var(--fx-success)]" : "border-[var(--fx-error)] bg-[var(--fx-error-soft)] text-[var(--fx-error)]"
          }`}
          title={socketState === "open" ? "Workflow socket connected" : statusLabel}
        >
          {connected ? <Wifi className="h-4 w-4" /> : <WifiOff className="h-4 w-4" />}
          <span className="hidden whitespace-nowrap 2xl:inline">{statusLabel}</span>
        </div>

        <button
          type="button"
          className="flex h-8 shrink-0 items-center gap-2 rounded-lg bg-[var(--fx-accent)] px-3 text-xs font-semibold text-white shadow-[0_8px_20px_var(--fx-glow)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          onClick={onRunBuild}
          disabled={!activeProject || Boolean(toolAction)}
          title="Build the active project"
        >
          {toolAction === "build" ? <Play className="h-4 w-4 animate-pulse" /> : <Hammer className="h-4 w-4" />}
          Run
        </button>
        <button
          type="button"
          className="fx-top-chip flex h-8 shrink-0 items-center gap-2 px-2.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!activeProject || selectedBoardLabel === "No board" || Boolean(toolAction)}
          onClick={onFlash}
          title={`Flash to ${selectedBoardLabel}`}
        >
          <Upload className="h-4 w-4" />
          Flash
        </button>
        <button
          type="button"
          className="fx-top-chip flex h-8 shrink-0 items-center justify-center px-2 text-[var(--fx-code-text)] disabled:cursor-not-allowed disabled:opacity-50"
          disabled={Boolean(toolAction)}
          onClick={onMonitor}
          title={`Start serial monitor${selectedBoardLabel === "No board" ? "" : ` on ${selectedBoardLabel}`}`}
        >
          <Radio className="h-4 w-4" />
        </button>
        <button
          type="button"
          className="fx-top-chip flex h-8 w-8 shrink-0 items-center justify-center text-[var(--fx-text-muted)]"
          title="Settings"
          aria-label="Settings"
          onClick={onOpenSettings}
        >
          <Settings className="h-4 w-4" />
        </button>
      </div>
    </header>
  );
}
