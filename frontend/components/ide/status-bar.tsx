"use client";

import { CheckCircle2, CircleDot, FileCode2, Radio, TriangleAlert } from "lucide-react";

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
    <footer className="flex h-7 min-w-0 shrink-0 items-center justify-between gap-3 border-t border-[var(--fx-border)] bg-[var(--fx-status-bg)] px-2.5 text-[11px] text-[var(--fx-status-text)]">
      <div className="flex min-w-0 items-center gap-4 overflow-hidden">
        <span className="flex items-center gap-1">
          {errors > 0 ? <TriangleAlert className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
          {errors > 0 ? `${errors} problem${errors === 1 ? "" : "s"}` : "No problems"}
        </span>
        {dirty > 0 ? <span>{dirty} unsaved</span> : null}
        <span className="flex items-center gap-1 truncate"><CircleDot className="h-3 w-3" />{taskStatus}</span>
        <span className="truncate font-medium text-[var(--fx-text)]">{activeProject?.project_name ?? "No workspace open"}</span>
      </div>
      <div className="flex shrink-0 items-center gap-4">
        {selectedFile.path ? <span className="flex items-center gap-1"><FileCode2 className="h-3.5 w-3.5" />{selectedFile.language}</span> : null}
        <span className="flex items-center gap-1"><Radio className="h-3.5 w-3.5" />{socketState === "open" ? "Live" : "Local"}</span>
      </div>
    </footer>
  );
}
