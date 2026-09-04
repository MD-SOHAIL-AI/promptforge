"use client";

import { Braces, Cpu, GitBranch } from "lucide-react";
import { memo } from "react";

import { CodeViewer } from "@/components/code/code-viewer";
import type { EditorTab, ProjectResponse, WorkspaceFile } from "@/types";

interface EditorWorkbenchProps {
  activeProject: ProjectResponse | null;
  selectedFile: WorkspaceFile;
  tabs: EditorTab[];
  activePath?: string | null;
  onSelectTab: (path: string) => void;
  onCloseTab: (path: string) => void;
  onChange: (path: string, content: string) => void;
  onSave: (path: string) => void;
  editorSettings?: {
    fontSize?: number;
    tabSize?: number;
    wordWrap?: boolean;
    minimapEnabled?: boolean;
    showLineNumbers?: boolean;
  };
}

function breadcrumb(path?: string | null) {
  if (!path) return "workspace";
  return path.split("/").filter(Boolean).join(" / ");
}

export const EditorWorkbench = memo(function EditorWorkbench({
  activeProject,
  selectedFile,
  tabs,
  activePath,
  onSelectTab,
  onCloseTab,
  onChange,
  onSave,
  editorSettings,
}: EditorWorkbenchProps) {
  const dirtyCount = tabs.filter((tab) => tab.dirty).length;
  const platform = activeProject?.framework || "PlatformIO";

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-transparent">
      <div className="fx-editor-context flex h-8 shrink-0 items-center justify-between border-b border-[var(--fx-border-soft)] px-3 text-[10px] text-[var(--fx-text-muted)]">
        <div className="flex min-w-0 items-center gap-2">
          <Braces className="h-4 w-4 shrink-0 text-[var(--fx-info)]" />
          <span className="truncate text-[var(--fx-code-text)]">{activeProject?.project_name ?? "ForgeX Workspace"}</span>
          <span className="text-[var(--fx-text-muted)]">/</span>
          <span className="truncate">{breadcrumb(activePath)}</span>
        </div>
        <div className="hidden items-center gap-3 whitespace-nowrap lg:flex">
          <span className="flex items-center gap-1">
            <Cpu className="h-3.5 w-3.5 text-[var(--fx-success)]" />
            {activeProject?.target_board ?? "No board"}
          </span>
          <span>{platform}</span>
          <span>{selectedFile.language}</span>
          <span>{dirtyCount > 0 ? `${dirtyCount} unsaved` : "Saved"}</span>
          <span className="flex items-center gap-1">
            <GitBranch className="h-3.5 w-3.5" />
            main
          </span>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <CodeViewer
          tabs={tabs}
          activePath={activePath}
          onSelectTab={onSelectTab}
          onCloseTab={onCloseTab}
          onChange={onChange}
          onSave={onSave}
          editorSettings={editorSettings}
        />
      </div>
    </section>
  );
});
