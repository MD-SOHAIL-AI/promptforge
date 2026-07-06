"use client";

import { MouseEvent, useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  File,
  FileCode2,
  FileCog,
  FilePlus2,
  Folder,
  FolderOpen,
  FolderPlus,
  Pencil,
  RefreshCw,
  Trash2,
} from "lucide-react";

import type { WorkspaceEntry } from "@/types";

interface TreeNode {
  name: string;
  path: string;
  kind: "folder" | "file";
  children: TreeNode[];
  entry: WorkspaceEntry;
}

interface FileExplorerProps {
  entries: WorkspaceEntry[];
  selectedPath?: string | null;
  revealedPaths?: string[];
  projectName?: string;
  boardName?: string;
  onOpenFile: (entry: WorkspaceEntry) => void;
  onCreateFile: (basePath?: string) => void;
  onCreateFolder: (basePath?: string) => void;
  onRename: (entry: WorkspaceEntry) => void;
  onDelete: (entry: WorkspaceEntry) => void;
  onRefresh: () => void;
}

function sortNodes(nodes: TreeNode[]) {
  return [...nodes].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "folder" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

function buildTree(entries: WorkspaceEntry[]) {
  const root: TreeNode[] = [];
  const folders = new Map<string, TreeNode>();

  const ensureFolder = (path: string) => {
    const existing = folders.get(path);
    if (existing) return existing;
    const parts = path.split("/");
    const node: TreeNode = {
      name: parts.at(-1) ?? path,
      path,
      kind: "folder",
      children: [],
      entry: { path, kind: "folder" },
    };
    folders.set(path, node);
    const parentPath = parts.slice(0, -1).join("/");
    if (parentPath) {
      ensureFolder(parentPath).children.push(node);
    } else {
      root.push(node);
    }
    return node;
  };

  for (const entry of entries) {
    const parts = entry.path.split("/");
    if (entry.kind === "folder") {
      ensureFolder(entry.path).entry = entry;
      continue;
    }
    const node: TreeNode = {
      name: parts.at(-1) ?? entry.path,
      path: entry.path,
      kind: "file",
      children: [],
      entry,
    };
    const parentPath = parts.slice(0, -1).join("/");
    if (parentPath) {
      ensureFolder(parentPath).children.push(node);
    } else {
      root.push(node);
    }
  }

  const order = (nodes: TreeNode[]): TreeNode[] =>
    sortNodes(nodes).map((node) => ({
      ...node,
      children: order(node.children),
    }));
  return order(root);
}

function iconFor(node: TreeNode, open: boolean) {
  if (node.kind === "folder") {
    return open ? <FolderOpen className="h-4 w-4 text-[var(--fx-warning)]" /> : <Folder className="h-4 w-4 text-[var(--fx-warning)]" />;
  }
  if (node.path.endsWith("platformio.ini") || node.path.endsWith(".ini")) {
    return <FileCog className="h-4 w-4 text-[var(--fx-success)]" />;
  }
  if (/\.(c|cpp|h|hpp|ino)$/i.test(node.path)) {
    return <FileCode2 className="h-4 w-4 text-[var(--fx-info)]" />;
  }
  return <File className="h-4 w-4 text-[var(--fx-text-muted)]" />;
}

function TreeItem({
  node,
  depth,
  selectedPath,
  revealedPaths,
  onOpenFile,
  onCreateFile,
  onCreateFolder,
  onRename,
  onDelete,
}: {
  node: TreeNode;
  depth: number;
  selectedPath?: string | null;
  revealedPaths: string[];
  onOpenFile: (entry: WorkspaceEntry) => void;
  onCreateFile: (basePath?: string) => void;
  onCreateFolder: (basePath?: string) => void;
  onRename: (entry: WorkspaceEntry) => void;
  onDelete: (entry: WorkspaceEntry) => void;
}) {
  const [open, setOpen] = useState(depth < 1);
  const selected = selectedPath === node.path;
  const revealed = revealedPaths.includes(node.path);

  useEffect(() => {
    if (node.kind === "folder" && revealedPaths.some((path) => path.startsWith(`${node.path}/`))) {
      setOpen(true);
    }
  }, [node.kind, node.path, revealedPaths]);

  const handleMain = () => {
    if (node.kind === "folder") {
      setOpen((value) => !value);
      return;
    }
    onOpenFile(node.entry);
  };

  const action = (event: MouseEvent<HTMLButtonElement>, callback: () => void) => {
    event.stopPropagation();
    callback();
  };

  return (
    <li>
      <div
        className={`group flex h-7 cursor-pointer items-center gap-1 rounded px-2 text-sm transition-colors duration-150 ${revealed ? "fx-tree-reveal" : ""} ${
          selected ? "bg-[var(--fx-accent-soft)] text-[var(--fx-text)]" : "text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
        }`}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        onClick={handleMain}
      >
        {node.kind === "folder" ? (
          open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />
        ) : (
          <span className="w-3.5" />
        )}
        {iconFor(node, open)}
        <span className="min-w-0 flex-1 truncate">{node.name}</span>
        <div className="flex opacity-0 transition group-hover:opacity-100">
          {node.kind === "folder" ? (
            <>
              <button
                className="rounded p-1 hover:bg-[var(--fx-hover)]"
                title="New file"
                aria-label="New file"
                onClick={(event) => action(event, () => onCreateFile(node.path))}
              >
                <FilePlus2 className="h-3.5 w-3.5" />
              </button>
              <button
                className="rounded p-1 hover:bg-[var(--fx-hover)]"
                title="New folder"
                aria-label="New folder"
                onClick={(event) => action(event, () => onCreateFolder(node.path))}
              >
                <FolderPlus className="h-3.5 w-3.5" />
              </button>
            </>
          ) : null}
          <button
            className="rounded p-1 hover:bg-[var(--fx-hover)]"
            title="Rename"
            aria-label="Rename"
            onClick={(event) => action(event, () => onRename(node.entry))}
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            className="rounded p-1 hover:bg-[var(--fx-hover)]"
            title="Delete"
            aria-label="Delete"
            onClick={(event) => action(event, () => onDelete(node.entry))}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      {open && node.children.length > 0 ? (
        <ul>
          {node.children.map((child) => (
            <TreeItem
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              revealedPaths={revealedPaths}
              onOpenFile={onOpenFile}
              onCreateFile={onCreateFile}
              onCreateFolder={onCreateFolder}
              onRename={onRename}
              onDelete={onDelete}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function FileExplorer({
  entries,
  selectedPath,
  revealedPaths = [],
  projectName = "Workspace",
  boardName,
  onOpenFile,
  onCreateFile,
  onCreateFolder,
  onRename,
  onDelete,
  onRefresh,
}: FileExplorerProps) {
  const tree = useMemo(() => buildTree(entries), [entries]);

  return (
    <section className="flex h-full min-h-0 flex-col border-r border-[var(--fx-border)] bg-[var(--fx-panel)]">
      <div className="flex h-9 shrink-0 items-center justify-between px-3">
        <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--fx-text-muted)]">Explorer</p>
        <div className="flex items-center gap-1 text-[var(--fx-text-muted)]">
          <button className="rounded p-1.5 hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]" title="Refresh" aria-label="Refresh" onClick={onRefresh}>
            <RefreshCw className="h-4 w-4" />
          </button>
          <button className="rounded p-1.5 hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]" title="New file" aria-label="New file" onClick={() => onCreateFile()}>
            <FilePlus2 className="h-4 w-4" />
          </button>
          <button className="rounded p-1.5 hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]" title="New folder" aria-label="New folder" onClick={() => onCreateFolder()}>
            <FolderPlus className="h-4 w-4" />
          </button>
        </div>
      </div>
      <div className="flex h-8 shrink-0 items-center gap-1 border-y border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--fx-text)]">
        <ChevronDown className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate" title={projectName}>{projectName}</span>
        {boardName ? <span className="max-w-24 truncate rounded bg-[var(--fx-accent-soft)] px-1.5 py-0.5 text-[9px] font-medium normal-case tracking-normal text-[var(--fx-text-muted)]" title={boardName}>{boardName}</span> : null}
      </div>
      <div className="flex-1 overflow-auto px-1 py-1.5">
        {tree.length > 0 ? (
          <ul className="space-y-0.5">
            {tree.map((node: TreeNode) => (
              <TreeItem
                key={node.path}
                node={node}
                depth={0}
                selectedPath={selectedPath}
                revealedPaths={revealedPaths}
                onOpenFile={onOpenFile}
                onCreateFile={onCreateFile}
                onCreateFolder={onCreateFolder}
                onRename={onRename}
                onDelete={onDelete}
              />
            ))}
          </ul>
        ) : (
          <div className="rounded border border-dashed border-[var(--fx-border-soft)] px-3 py-6 text-sm text-[var(--fx-text-muted)]">
            No project files loaded.
          </div>
        )}
      </div>
    </section>
  );
}
