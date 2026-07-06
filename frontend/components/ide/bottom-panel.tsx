"use client";

import { AlertTriangle, Bug, ChevronDown, ChevronUp, Maximize2, Minimize2, Radio, SquareTerminal, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { ForgeXTerminal } from "@/components/terminal/forgex-terminal";
import type { ConsoleEntry, ProjectResponse, WorkspaceLog, WorkflowStage } from "@/types";

type BottomTab = "problems" | "terminal" | "debug" | "serial";

interface BottomPanelProps {
  logs: ConsoleEntry[];
  workspaceLogs: WorkspaceLog[];
  stages: WorkflowStage[];
  activeProject: ProjectResponse | null;
  height: number;
  collapsed: boolean;
  maximized: boolean;
  onToggleCollapsed: () => void;
  onToggleMaximized: () => void;
  onClearLogs: () => void;
}

const tabs = [
  { id: "problems" as const, label: "Problems", icon: AlertTriangle },
  { id: "terminal" as const, label: "Terminal", icon: SquareTerminal },
  { id: "debug" as const, label: "Debug Console", icon: Bug },
  { id: "serial" as const, label: "Serial Monitor", icon: Radio },
];

function time(value: string) {
  try {
    return new Date(value).toLocaleTimeString();
  } catch {
    return "--:--:--";
  }
}

export function BottomPanel({
  logs,
  workspaceLogs,
  stages,
  activeProject,
  height,
  collapsed,
  maximized,
  onToggleCollapsed,
  onToggleMaximized,
  onClearLogs,
}: BottomPanelProps) {
  const [active, setActive] = useState<BottomTab>("terminal");
  const problems = logs.filter((log) => log.channel === "error");
  const serialLogs = logs.filter((log) => log.channel === "serial");
  const workspaceSerial = workspaceLogs.filter((log) => log.log_type === "monitor");

  const lines = useMemo(() => {
    if (active === "problems") {
      return problems.length > 0 ? problems.map((log) => `[${time(log.timestamp)}] ${log.message}`) : ["No problems detected."];
    }
    if (active === "debug") {
      return stages.map((stage) => `${stage.label.padEnd(12)} ${stage.status} - ${stage.description}`);
    }
    if (active === "serial") {
      const stream = [
        ...serialLogs.map((log) => `[${time(log.timestamp)}] ${log.message}`),
        ...workspaceSerial.map((log) => `[${log.execution_id}] ${log.content.trim()}`),
      ].filter(Boolean);
      return stream.length > 0 ? stream : ["Serial monitor idle."];
    }
    return logs.length > 0
      ? logs.map((log) => `[${time(log.timestamp)}] [${log.channel}] ${log.message}`)
      : ["ForgeX output ready."];
  }, [active, logs, problems, serialLogs, stages, workspaceSerial]);

  return (
    <section
      className="fx-bottom-panel min-h-0 min-w-0 shrink-0 overflow-hidden border-t border-[var(--fx-border)] bg-[var(--fx-bg)] transition-[height] duration-200 ease-out"
      style={{ height: collapsed ? 35 : height }}
      aria-label="Panel"
    >
      <div className="flex h-9 min-w-0 items-center justify-between border-b border-[var(--fx-border)] bg-[var(--fx-panel)] px-2">
        <div className="flex h-full min-w-0 overflow-x-auto" role="tablist" aria-label="Bottom panel">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const selected = active === tab.id;
            return (
              <button
                key={tab.id}
                className={`relative flex h-full shrink-0 items-center gap-1.5 px-2.5 text-[11px] font-medium uppercase tracking-wide ${
                  selected ? "text-[var(--fx-text)]" : "text-[var(--fx-text-muted)] hover:text-[var(--fx-text)]"
                }`}
                onClick={() => setActive(tab.id)}
                role="tab"
                aria-selected={selected}
              >
                <Icon className="h-3 w-3" />
                {tab.label}
                {selected ? <span className="absolute inset-x-2 bottom-0 h-px bg-[var(--fx-accent)]" /> : null}
              </button>
            );
          })}
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
            onClick={onClearLogs}
            title="Clear live output"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Clear
          </button>
          <button
            className="flex h-7 w-7 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
            onClick={onToggleMaximized}
            title={maximized ? "Restore bottom panel" : "Maximize bottom panel"}
            aria-label={maximized ? "Restore bottom panel" : "Maximize bottom panel"}
          >
            {maximized ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </button>
          <button
            className="flex h-7 w-7 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
            onClick={onToggleCollapsed}
            title={collapsed ? "Show terminal panel" : "Collapse terminal panel"}
            aria-label={collapsed ? "Show bottom panel" : "Collapse bottom panel"}
          >
            {collapsed ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
        </div>
      </div>
      {collapsed ? null : active === "terminal" ? (
        <div className="min-h-0" style={{ height: height - 36 }} role="tabpanel">
          <ForgeXTerminal activeProject={activeProject} workflowLogs={logs} />
        </div>
      ) : (
        <div className="overflow-auto px-3 py-2 font-mono leading-5 text-[var(--fx-terminal-text)]" style={{ height: height - 36, fontSize: "var(--fx-terminal-font-size)" }} role="tabpanel">
          {lines.map((line, index) => (
            <div key={`${active}-${index}`} className={active === "problems" && problems.length > 0 ? "text-[var(--fx-error)]" : ""}>
              {line}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
