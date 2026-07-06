"use client";

import { Bell, CheckCircle2, GitBranch, Radio, TriangleAlert } from "lucide-react";

import type { ConsoleEntry, EditorTab, ProjectResponse, WorkspaceFile } from "@/types";

interface IdeStatusBarProps {
  activeProject: ProjectResponse | null;
  selectedFile: WorkspaceFile;
  tabs: EditorTab[];
  logs: ConsoleEntry[];
  socketState: "idle" | "connecting" | "reconnecting" | "open" | "closed";
  isExecuting: boolean;
}

export function IdeStatusBar({ activeProject, selectedFile, tabs, logs, socketState, isExecuting }: IdeStatusBarProps) {
  const errors = logs.filter((log) => log.channel === "error").length;
  const dirty = tabs.filter((tab) => tab.dirty).length;
  const taskStatus = socketState === "reconnecting" ? "Restoring workflow events" : isExecuting ? "Task running" : socketState === "open" ? "Socket connected" : "Ready";

  return (
    <footer className="flex h-6 min-w-0 shrink-0 items-center justify-between gap-3 border-t border-[var(--fx-border)] bg-[var(--fx-status-bg)] px-2 text-[11px] text-[var(--fx-status-text)]">
      <div className="flex min-w-0 items-center gap-4 overflow-hidden">
        <span className="flex items-center gap-1">
          <GitBranch className="h-3.5 w-3.5" />
          main
        </span>
        <span className="flex items-center gap-1">
          {errors > 0 ? <TriangleAlert className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
          {errors} errors
        </span>
        <span>{dirty} unsaved</span>
        <span className="truncate">{taskStatus}</span>
        <span className="truncate">{activeProject?.project_name ?? "No project"}</span>
      </div>
      <div className="flex shrink-0 items-center gap-4">
        <span>Ln 1, Col 1</span>
        <span>Spaces: 2</span>
        <span>UTF-8</span>
        <span>{selectedFile.language}</span>
        <span className="flex items-center gap-1">
          <Radio className="h-3.5 w-3.5" />
          PlatformIO
        </span>
        <Bell className="h-3.5 w-3.5" />
      </div>
    </footer>
  );
}
