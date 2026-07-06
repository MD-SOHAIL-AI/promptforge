"use client";

import { BrainCircuit, Cpu, FolderOpen, Hammer, Play, Radio, Search, Settings, Upload, Wifi, WifiOff, Zap } from "lucide-react";

import type { HealthResponse, ProjectResponse } from "@/types";

interface TopCommandBarProps {
  health: HealthResponse | null;
  projects: ProjectResponse[];
  activeProject: ProjectResponse | null;
  selectedBoardLabel: string;
  command: string;
  isExecuting: boolean;
  toolAction: "build" | "flash" | "monitor" | null;
  socketState: "idle" | "connecting" | "reconnecting" | "open" | "closed";
  modelRouteLabel: string;
  onCommandChange: (value: string) => void;
  onSubmitCommand: () => void;
  onRunBuild: () => void;
  onFlash: () => void;
  onMonitor: () => void;
  onOpenFolder: () => void;
  onOpenProject: () => void;
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
  toolAction,
  socketState,
  modelRouteLabel,
  onCommandChange,
  onSubmitCommand,
  onRunBuild,
  onFlash,
  onMonitor,
  onOpenFolder,
  onOpenProject,
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
    <header className="flex h-16 min-w-0 shrink-0 items-center gap-3 overflow-hidden border-b px-3 pl-4" style={{ backgroundColor: "var(--fx-panel)", borderColor: "var(--fx-border)", color: "var(--fx-text)" }}>
      <div className="flex min-w-[190px] shrink-0 items-center gap-3 xl:min-w-[238px]">
        <div className="relative flex h-9 w-9 items-center justify-center overflow-hidden rounded-xl bg-[var(--fx-accent)] text-white shadow-[0_8px_24px_var(--fx-glow)]">
          <span className="absolute inset-0 bg-gradient-to-br from-white/25 to-transparent" /><Zap className="relative h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="text-[15px] font-semibold leading-4 tracking-tight">ForgeX</div>
          <div className="truncate text-[9px] font-medium uppercase leading-4 tracking-[.12em] text-[var(--fx-text-muted)]">Embedded intelligence</div>
        </div>
      </div>

      <form
        className="fx-control flex h-10 min-w-[180px] flex-1 items-center gap-2 px-3 text-sm shadow-inner"
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
          placeholder="Ask ForgeX to create, build, or inspect firmware"
          disabled={isExecuting}
        />
      </form>

      <div className="flex min-w-0 items-center gap-2 overflow-x-auto">
        <button
          className="fx-control flex h-9 max-w-[230px] shrink-0 items-center gap-2 px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
          onClick={onOpenModels}
          title="Open model settings"
        >
          <BrainCircuit className="h-4 w-4 shrink-0 text-[var(--fx-info)]" />
          <span className="truncate">Model: {safeModelRouteLabel}</span>
        </button>

        <label className="flex h-9 min-w-[190px] shrink-0 items-center gap-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)]">
          <Cpu className="h-4 w-4 text-[var(--fx-info)]" />
          <select
            className="min-w-0 flex-1 bg-transparent text-[var(--fx-text)] outline-none"
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
        </label>

        <button
          className="flex h-9 shrink-0 items-center gap-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-sm text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] 2xl:px-3"
          onClick={onOpenFolder}
          title="Open folder"
        >
          <FolderOpen className="h-4 w-4" />
          <span className="hidden 2xl:inline">Open Folder</span>
        </button>
        <button
          className="hidden h-9 shrink-0 items-center gap-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-3 text-sm text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] 2xl:flex"
          onClick={onOpenProject}
          title="Open existing managed project folder"
        >
          <FolderOpen className="h-4 w-4" />
          Open Project
        </button>

        <div
          className={`flex h-9 shrink-0 items-center gap-2 rounded border px-3 text-xs ${
            connected ? "border-[var(--fx-success)] bg-[var(--fx-success-soft)] text-[var(--fx-success)]" : "border-[var(--fx-error)] bg-[var(--fx-error-soft)] text-[var(--fx-error)]"
          }`}
          title={socketState === "open" ? "Workflow socket connected" : statusLabel}
        >
          {connected ? <Wifi className="h-4 w-4" /> : <WifiOff className="h-4 w-4" />}
          <span className="hidden whitespace-nowrap 2xl:inline">{statusLabel}</span>
        </div>

        <button
          className="flex h-9 shrink-0 items-center gap-2 rounded bg-[var(--fx-accent)] px-3 text-sm font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          onClick={onRunBuild}
          disabled={!activeProject || Boolean(toolAction)}
          title="Build the active project"
        >
          {toolAction === "build" ? <Play className="h-4 w-4 animate-pulse" /> : <Hammer className="h-4 w-4" />}
          Run
        </button>
        <button
          className="flex h-9 shrink-0 items-center gap-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-3 text-sm text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:cursor-not-allowed disabled:opacity-60"
          disabled={!activeProject || selectedBoardLabel === "No board" || Boolean(toolAction)}
          onClick={onFlash}
          title={`Flash to ${selectedBoardLabel}`}
        >
          <Upload className="h-4 w-4" />
          Flash
        </button>
        <button
          className="flex h-9 shrink-0 items-center justify-center rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-2 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:cursor-not-allowed disabled:opacity-60"
          disabled={Boolean(toolAction)}
          onClick={onMonitor}
          title={`Start serial monitor${selectedBoardLabel === "No board" ? "" : ` on ${selectedBoardLabel}`}`}
        >
          <Radio className="h-4 w-4" />
        </button>
        <button
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
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
