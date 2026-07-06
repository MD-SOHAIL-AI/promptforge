"use client";

import { Hammer, Library, PlugZap, Radio, RefreshCw, Upload, Wrench, Zap } from "lucide-react";

import type { DetectedBoard, HealthResponse, MonitorStatusResponse, ProjectResponse } from "@/types";

interface DeviceToolsPanelProps {
  activeProject: ProjectResponse | null;
  health: HealthResponse | null;
  boards: DetectedBoard[];
  selectedBoard: DetectedBoard | null;
  selectedBoardPort: string | null;
  monitorStatus: MonitorStatusResponse | null;
  toolAction: "build" | "flash" | "monitor" | null;
  onSelectBoard: (port: string) => void;
  onRefreshBoards: () => void;
  onBuild: () => void;
  onFlash: () => void;
  onMonitor: () => void;
  onStopMonitor: () => void;
  onImportProject: () => void;
  embedded?: boolean;
}

export function DeviceToolsPanel({
  activeProject,
  health,
  boards,
  selectedBoard,
  selectedBoardPort,
  monitorStatus,
  toolAction,
  onSelectBoard,
  onRefreshBoards,
  onBuild,
  onFlash,
  onMonitor,
  onStopMonitor,
  onImportProject,
  embedded = false,
}: DeviceToolsPanelProps) {
  const connected = Boolean(health);
  const boardList = Array.isArray(boards) ? boards : [];
  const boardName = selectedBoard?.board_type || activeProject?.target_board || "No board detected";
  const framework = activeProject?.framework || "platformio";
  const monitorConnected = Boolean(monitorStatus?.connected);
  const activeEnvironment =
    typeof activeProject?.metadata?.active_environment === "string"
      ? activeProject.metadata.active_environment
      : null;

  const content = (
    <>
      {!embedded ? (
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-[var(--fx-border)] px-3">
        <div className="flex items-center gap-2 text-sm font-medium text-[var(--fx-text)]">
          <PlugZap className="h-4 w-4 text-[var(--fx-success)]" />
          Device Tools
        </div>
        <span className={`rounded px-2 py-0.5 text-[11px] ${connected ? "bg-[var(--fx-success-soft)] text-[var(--fx-success)]" : "bg-[var(--fx-error-soft)] text-[var(--fx-error)]"}`}>
          {connected ? "Connected" : "Offline"}
        </span>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-auto p-3">
        <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-[var(--fx-text)]">{boardName}</p>
              <p className="mt-1 truncate text-xs text-[var(--fx-text-muted)]">
                {selectedBoard?.description ?? `${framework} target`}
              </p>
            </div>
            <button
              className="rounded border border-[var(--fx-border)] px-2 py-1 text-xs text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:cursor-not-allowed disabled:opacity-60"
              disabled={!monitorConnected || Boolean(toolAction)}
              onClick={onStopMonitor}
              title="Stop serial monitor"
            >
              {monitorConnected ? "Disconnect" : "Disconnected"}
            </button>
          </div>
          <select
            className="mt-3 h-8 w-full rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-xs text-[var(--fx-text)] outline-none"
            value={selectedBoardPort ?? ""}
            onChange={(event) => onSelectBoard(event.target.value)}
          >
            {boardList.length === 0 ? <option value="">No serial boards detected</option> : null}
            {boardList.map((board) => (
              <option key={board.port} value={board.port}>
                {board.board_type} - {board.port}
              </option>
            ))}
          </select>
          <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
            <dt className="text-[var(--fx-text-muted)]">Port</dt>
            <dd className="truncate text-right text-[var(--fx-code-text)]">{selectedBoard?.port ?? monitorStatus?.port ?? "None"}</dd>
            <dt className="text-[var(--fx-text-muted)]">Flash</dt>
            <dd className="text-right text-[var(--fx-code-text)]">{selectedBoard?.vid ? `VID ${selectedBoard.vid.toString(16).toUpperCase()}` : "Unknown"}</dd>
            <dt className="text-[var(--fx-text-muted)]">USB PID</dt>
            <dd className="text-right text-[var(--fx-code-text)]">{selectedBoard?.pid ? selectedBoard.pid.toString(16).toUpperCase() : "Unknown"}</dd>
            <dt className="text-[var(--fx-text-muted)]">Baud</dt>
            <dd className="text-right text-[var(--fx-code-text)]">{monitorStatus?.baudrate ?? 115200}</dd>
            <dt className="text-[var(--fx-text-muted)]">Env</dt>
            <dd className="truncate text-right text-[var(--fx-code-text)]">{activeEnvironment ?? "Default"}</dd>
          </dl>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2">
          {[
            { label: "Build", icon: Hammer, onClick: onBuild, disabled: !activeProject || Boolean(toolAction) },
            { label: "Upload", icon: Upload, onClick: onFlash, disabled: !activeProject || !selectedBoard || Boolean(toolAction) },
            { label: monitorConnected ? "Monitor On" : "Monitor", icon: Radio, onClick: onMonitor, disabled: Boolean(toolAction) },
            { label: "Refresh", icon: RefreshCw, onClick: onRefreshBoards, disabled: Boolean(toolAction) },
          ].map((action) => {
            const Icon = action.icon;
            return (
              <button
                key={action.label}
                className="flex items-center justify-center gap-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-2 py-2 text-sm text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:cursor-not-allowed disabled:opacity-60"
                disabled={action.disabled}
                onClick={action.onClick}
                title={action.label}
              >
                <Icon className="h-4 w-4" />
                {action.label}
              </button>
            );
          })}
        </div>

        <div className="mt-3 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
          <div className="mb-2 text-xs font-semibold uppercase text-[var(--fx-text-muted)]">Quick Access</div>
          <div className="space-y-1">
            {[
              { label: "New Project", icon: Zap, disabled: true },
              { label: "Import Project", icon: Wrench, disabled: false, onClick: onImportProject },
              { label: "Project Examples", icon: Library, disabled: true },
              { label: "Library Manager", icon: Library, disabled: true },
              { label: "Board Manager", icon: PlugZap, disabled: true },
              { label: "PlatformIO Home", icon: Zap, disabled: true },
            ].map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.label}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={item.disabled}
                  onClick={item.onClick}
                  title={item.disabled ? "Quick access placeholder for a later UI wiring pass" : item.label}
                >
                  <Icon className="h-3.5 w-3.5 text-[var(--fx-info)]" />
                  {item.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );

  if (embedded) {
    return <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-[var(--fx-panel)]">{content}</div>;
  }

  return (
    <aside className="flex min-h-0 flex-col border-l border-t border-[var(--fx-border)] bg-[var(--fx-panel)]">
      {content}
    </aside>
  );
}
