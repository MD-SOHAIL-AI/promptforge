"use client";

import type { ProjectResponse, WorkspaceEntry } from "@/types";
import { FileExplorer } from "@/components/explorer/file-explorer";

interface IdeExplorerProps {
  activeProject: ProjectResponse | null;
  entries: WorkspaceEntry[];
  selectedPath?: string | null;
  revealedPaths?: string[];
  onOpenFile: (entry: WorkspaceEntry) => void;
  onCreateFile: (basePath?: string) => void;
  onCreateFolder: (basePath?: string) => void;
  onRename: (entry: WorkspaceEntry) => void;
  onDelete: (entry: WorkspaceEntry) => void;
  onRefresh: () => void;
}

export function IdeExplorer({
  activeProject,
  entries,
  selectedPath,
  revealedPaths,
  onOpenFile,
  onCreateFile,
  onCreateFolder,
  onRename,
  onDelete,
  onRefresh,
}: IdeExplorerProps) {
  return (
    <FileExplorer
      entries={entries}
      selectedPath={selectedPath}
      revealedPaths={revealedPaths}
      projectName={activeProject?.project_name ?? "No project"}
      boardName={activeProject?.target_board}
      onOpenFile={onOpenFile}
      onCreateFile={onCreateFile}
      onCreateFolder={onCreateFolder}
      onRename={onRename}
      onDelete={onDelete}
      onRefresh={onRefresh}
    />
  );
}
