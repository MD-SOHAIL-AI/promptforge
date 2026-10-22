"use client";

import { KeyboardEvent, MouseEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown, ChevronRight, ClipboardCopy, File, FileCode2, FileCog, FilePlus2,
  Folder, FolderOpen, FolderPlus, MoreHorizontal, PanelTopClose, Pencil, RefreshCw, Search, Trash2, X,
} from "lucide-react";

import type { WorkspaceEntry } from "@/types";

interface TreeNode {
  name: string;
  path: string;
  kind: "folder" | "file";
  children: TreeNode[];
  entry: WorkspaceEntry;
}

type SearchScope = "names" | "contents";

interface FileExplorerProps {
  entries: WorkspaceEntry[];
  selectedPath?: string | null;
  revealedPaths?: string[];
  projectName?: string;
  boardName?: string;
  onOpenFile: (entry: WorkspaceEntry) => void;
  onCreateFile: (basePath?: string, path?: string) => void;
  onCreateFolder: (basePath?: string, path?: string) => void;
  onRename: (entry: WorkspaceEntry, path?: string) => void;
  onDelete: (entry: WorkspaceEntry) => void;
  onRefresh: () => void | Promise<void>;
  onSearchContents?: (query: string) => Promise<string[]>;
}

interface EditingState {
  mode: "create" | "rename";
  kind: "file" | "folder";
  path: string;
  value: string;
  entry?: WorkspaceEntry;
  basePath?: string;
}

interface ContextMenuState { x: number; y: number; node: TreeNode; }

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

export function sortTreeNodes(nodes: TreeNode[]) {
  return [...nodes].sort((a, b) => a.kind !== b.kind ? (a.kind === "folder" ? -1 : 1) : collator.compare(a.name, b.name));
}

export function buildExplorerTree(entries: WorkspaceEntry[]) {
  const root: TreeNode[] = [];
  const folders = new Map<string, TreeNode>();
  const ensureFolder = (path: string) => {
    const existing = folders.get(path);
    if (existing) return existing;
    const parts = path.split("/");
    const node: TreeNode = { name: parts.at(-1) ?? path, path, kind: "folder", children: [], entry: { path, kind: "folder" } };
    folders.set(path, node);
    const parentPath = parts.slice(0, -1).join("/");
    if (parentPath) ensureFolder(parentPath).children.push(node);
    else root.push(node);
    return node;
  };
  for (const entry of entries) {
    const parts = entry.path.split("/");
    if (entry.kind === "folder") {
      ensureFolder(entry.path).entry = entry;
      continue;
    }
    const node: TreeNode = { name: parts.at(-1) ?? entry.path, path: entry.path, kind: "file", children: [], entry };
    const parentPath = parts.slice(0, -1).join("/");
    if (parentPath) ensureFolder(parentPath).children.push(node);
    else root.push(node);
  }
  const order = (nodes: TreeNode[]): TreeNode[] => sortTreeNodes(nodes).map((node) => ({ ...node, children: order(node.children) }));
  return order(root);
}

function filterTreeByName(nodes: TreeNode[], query: string): TreeNode[] {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return nodes;
  return nodes.flatMap((node) => {
    const children = filterTreeByName(node.children, normalized);
    const matches = node.path.toLocaleLowerCase().includes(normalized) || node.name.toLocaleLowerCase().includes(normalized);
    return matches || children.length > 0 ? [{ ...node, children }] : [];
  });
}

function filterTreeByPaths(nodes: TreeNode[], matchedPaths: Set<string>): TreeNode[] {
  return nodes.flatMap((node) => {
    const children = filterTreeByPaths(node.children, matchedPaths);
    return matchedPaths.has(node.path) || children.length > 0 ? [{ ...node, children }] : [];
  });
}

function visibleFileCount(nodes: TreeNode[]): number {
  return nodes.reduce((total, node) => total + (node.kind === "file" ? 1 : 0) + visibleFileCount(node.children), 0);
}

function uniqueDraftPath(entries: WorkspaceEntry[], kind: "file" | "folder", basePath?: string) {
  const used = new Set(entries.map((entry) => entry.path.toLocaleLowerCase()));
  const prefix = basePath ? `${basePath}/` : "";
  const base = kind === "file" ? "new_file" : "new_folder";
  const suffix = kind === "file" ? ".cpp" : "";
  let index = 1;
  let candidate = `${prefix}${base}${suffix}`;
  while (used.has(candidate.toLocaleLowerCase())) candidate = `${prefix}${base}_${++index}${suffix}`;
  return candidate;
}

function iconFor(node: TreeNode, open: boolean) {
  if (node.kind === "folder") return open ? <FolderOpen className="h-4 w-4 text-[var(--fx-warning)]" /> : <Folder className="h-4 w-4 text-[var(--fx-warning)]" />;
  if (node.path.endsWith("platformio.ini") || node.path.endsWith(".ini")) return <FileCog className="h-4 w-4 text-[var(--fx-success)]" />;
  if (/\.(c|cpp|h|hpp|ino)$/i.test(node.path)) return <FileCode2 className="h-4 w-4 text-[var(--fx-info)]" />;
  return <File className="h-4 w-4 text-[var(--fx-text-muted)]" />;
}

function TreeItem({ node, depth, selectedPath, revealedPaths, onOpenFile, onCreateFile, onCreateFolder, onRename, onDelete, onContextMenu, editing, onEditValue, onCommitEdit, onCancelEdit, filterActive, collapseToken }: {
  node: TreeNode; depth: number; selectedPath?: string | null; revealedPaths: string[]; onOpenFile: (entry: WorkspaceEntry) => void;
  onCreateFile: (basePath?: string, path?: string) => void; onCreateFolder: (basePath?: string, path?: string) => void;
  onRename: (entry: WorkspaceEntry, path?: string) => void; onDelete: (entry: WorkspaceEntry) => void; onContextMenu: (event: MouseEvent, node: TreeNode) => void;
  editing: EditingState | null; onEditValue: (value: string) => void; onCommitEdit: () => void; onCancelEdit: () => void; filterActive: boolean; collapseToken: number;
}) {
  const [open, setOpen] = useState(depth === 0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const cancelledRef = useRef(false);
  const selected = selectedPath === node.path;
  const revealed = revealedPaths.includes(node.path);
  const editingThis = editing?.path === node.path;
  const effectiveOpen = open || (filterActive && node.children.length > 0);

  useEffect(() => { if (collapseToken > 0) setOpen(false); }, [collapseToken]);
  useEffect(() => { if (node.kind === "folder" && revealedPaths.some((path) => path.startsWith(`${node.path}/`))) setOpen(true); }, [node.kind, node.path, revealedPaths]);
  useEffect(() => { if (editingThis) { inputRef.current?.focus(); inputRef.current?.select(); } }, [editingThis]);

  const handleMain = () => {
    if (editingThis) return;
    if (node.kind === "folder") setOpen((value) => !value);
    else onOpenFile(node.entry);
  };
  const action = (event: MouseEvent<HTMLButtonElement>, callback: () => void) => { event.stopPropagation(); callback(); };
  const keyAction = (event: KeyboardEvent<HTMLDivElement>) => {
    if (editingThis) return;
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); handleMain(); }
    if (event.key === "F2") { event.preventDefault(); onRename(node.entry); }
    if (event.key === "Delete") { event.preventDefault(); onDelete(node.entry); }
  };

  return <li>
    <div
      className={`group flex h-7 cursor-pointer items-center gap-1 px-2 text-[13px] transition-colors duration-100 ${revealed ? "fx-tree-reveal" : ""} ${selected ? "bg-[var(--fx-accent-soft)] text-[var(--fx-text)]" : "text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"}`}
      style={{ paddingLeft: `${8 + depth * 14}px` }} onClick={handleMain} onContextMenu={(event) => onContextMenu(event, node)} onKeyDown={keyAction}
      role="treeitem" aria-selected={selected} aria-expanded={node.kind === "folder" ? effectiveOpen : undefined} tabIndex={0}
    >
      {node.kind === "folder" ? (effectiveOpen ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />) : <span className="w-3.5 shrink-0" />}
      {iconFor(node, effectiveOpen)}
      {editingThis ? <input ref={inputRef} className="min-w-0 flex-1 border border-[var(--fx-info)] bg-[var(--fx-input)] px-1 py-0.5 text-xs text-[var(--fx-text)] outline-none" value={editing.value} onChange={(event) => onEditValue(event.target.value)} onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
        if (event.key === "Enter") { event.preventDefault(); onCommitEdit(); }
        if (event.key === "Escape") { event.preventDefault(); cancelledRef.current = true; onCancelEdit(); }
      }} onBlur={() => { if (cancelledRef.current) { cancelledRef.current = false; return; } onCommitEdit(); }} /> : <span className="min-w-0 flex-1 truncate">{node.name}</span>}
      {node.kind === "folder" ? <div className="flex opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        <button className="p-1 hover:bg-[var(--fx-input)]" title="New file" aria-label="New file" onClick={(event) => action(event, () => onCreateFile(node.path))}><FilePlus2 className="h-3.5 w-3.5" /></button>
        <button className="p-1 hover:bg-[var(--fx-input)]" title="New folder" aria-label="New folder" onClick={(event) => action(event, () => onCreateFolder(node.path))}><FolderPlus className="h-3.5 w-3.5" /></button>
      </div> : null}
    </div>
    {effectiveOpen && node.children.length > 0 ? <ul role="group">{node.children.map((child) => <TreeItem key={child.path} node={child} depth={depth + 1} selectedPath={selectedPath} revealedPaths={revealedPaths} onOpenFile={onOpenFile} onCreateFile={onCreateFile} onCreateFolder={onCreateFolder} onRename={onRename} onDelete={onDelete} onContextMenu={onContextMenu} editing={editing} onEditValue={onEditValue} onCommitEdit={onCommitEdit} onCancelEdit={onCancelEdit} filterActive={filterActive} collapseToken={collapseToken} />)}</ul> : null}
  </li>;
}

export function FileExplorer({ entries, selectedPath, revealedPaths = [], projectName = "Workspace", boardName, onOpenFile, onCreateFile, onCreateFolder, onRename, onDelete, onRefresh, onSearchContents }: FileExplorerProps) {
  const [filter, setFilter] = useState("");
  const [searchScope, setSearchScope] = useState<SearchScope>("names");
  const [contentMatches, setContentMatches] = useState<string[] | null>(null);
  const [contentSearchError, setContentSearchError] = useState<string | null>(null);
  const [searchingContents, setSearchingContents] = useState(false);
  const [editing, setEditing] = useState<EditingState | null>(null);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [collapseToken, setCollapseToken] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const searchRequest = useRef(0);

  const draftEntries = useMemo(() => editing?.mode === "create" ? [...entries, { path: editing.path, kind: editing.kind }] : entries, [editing, entries]);
  const unfilteredTree = useMemo(() => buildExplorerTree(draftEntries), [draftEntries]);
  const filterActive = filter.trim().length > 0;
  const tree = useMemo(() => {
    if (searchScope === "contents") return !filterActive || !contentMatches ? (filterActive ? [] : unfilteredTree) : filterTreeByPaths(unfilteredTree, new Set(contentMatches));
    return filterTreeByName(unfilteredTree, filter);
  }, [contentMatches, filter, filterActive, searchScope, unfilteredTree]);
  const visibleFiles = useMemo(() => visibleFileCount(tree), [tree]);

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(null), 2_000);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  useEffect(() => {
    if (searchScope !== "contents" || !filterActive || !onSearchContents) {
      setContentMatches(null); setContentSearchError(null); setSearchingContents(false); return;
    }
    if (filter.trim().length < 2) { setContentMatches([]); setContentSearchError("Enter at least 2 characters to search contents."); return; }
    const requestId = ++searchRequest.current;
    const timeout = window.setTimeout(() => {
      setSearchingContents(true); setContentSearchError(null);
      void onSearchContents(filter.trim()).then((paths) => {
        if (requestId === searchRequest.current) setContentMatches(paths);
      }).catch(() => {
        if (requestId === searchRequest.current) { setContentMatches([]); setContentSearchError("Content search is unavailable right now."); }
      }).finally(() => { if (requestId === searchRequest.current) setSearchingContents(false); });
    }, 220);
    return () => window.clearTimeout(timeout);
  }, [filter, filterActive, onSearchContents, searchScope]);

  useEffect(() => {
    if (!contextMenu && !moreOpen) return;
    const close = () => { setContextMenu(null); setMoreOpen(false); };
    window.addEventListener("click", close); window.addEventListener("keydown", close); window.addEventListener("resize", close);
    return () => { window.removeEventListener("click", close); window.removeEventListener("keydown", close); window.removeEventListener("resize", close); };
  }, [contextMenu, moreOpen]);

  const beginCreate = (kind: "file" | "folder", basePath?: string) => {
    const path = uniqueDraftPath(entries, kind, basePath);
    setEditing({ mode: "create", kind, path, value: path, basePath }); setContextMenu(null); setMoreOpen(false);
  };
  const beginRename = (entry: WorkspaceEntry) => { setEditing({ mode: "rename", kind: entry.kind, path: entry.path, value: entry.path, entry }); setContextMenu(null); };
  const commitEdit = () => {
    if (!editing) return;
    const value = editing.value.trim().replaceAll("\\", "/"); setEditing(null);
    if (!value || value === editing.entry?.path) return;
    if (editing.mode === "create") { if (editing.kind === "file") onCreateFile(editing.basePath, value); else onCreateFolder(editing.basePath, value); return; }
    if (editing.entry) onRename(editing.entry, value);
  };
  const refresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try { await onRefresh(); setNotice("Explorer refreshed"); } finally { setRefreshing(false); }
  };
  const copyRelativePath = async (path: string) => {
    setContextMenu(null);
    try { await navigator.clipboard.writeText(path); setNotice(`Copied ${path}`); } catch { setNotice("Could not copy path"); }
  };
  const emptyMessage = !filterActive ? (projectName === "No project" ? "Open a project to browse its files." : "This project has no visible files yet.") : searchScope === "contents" && searchingContents ? "Searching file contents..." : contentSearchError ?? "No matching files found.";

  return <section className="fx-side-panel relative flex h-full min-h-0 flex-col border-r border-[var(--fx-border)]">
    <div className="flex h-10 shrink-0 items-center justify-between border-b border-[var(--fx-border)] px-3">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-[var(--fx-text)]">Explorer</p>
      <div className="relative flex items-center gap-0.5 text-[var(--fx-text-muted)]">
        <button className="p-1.5 hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]" title="Collapse all folders" aria-label="Collapse all folders" onClick={() => setCollapseToken((value) => value + 1)}><PanelTopClose className="h-4 w-4" /></button>
        <button className="p-1.5 hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:opacity-45" title="Refresh explorer" aria-label="Refresh explorer" disabled={refreshing} onClick={() => void refresh()}><RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} /></button>
        <button className="p-1.5 hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]" title="Explorer actions" aria-label="Explorer actions" onClick={(event) => { event.stopPropagation(); setMoreOpen((value) => !value); }}><MoreHorizontal className="h-4 w-4" /></button>
        {moreOpen ? <div className="absolute right-0 top-8 z-50 min-w-40 border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] py-1 text-xs text-[var(--fx-text)] shadow-lg" onClick={(event) => event.stopPropagation()}>
          <button className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-[var(--fx-hover)]" onClick={() => beginCreate("file")}><FilePlus2 className="h-3.5 w-3.5" />New file</button>
          <button className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-[var(--fx-hover)]" onClick={() => beginCreate("folder")}><FolderPlus className="h-3.5 w-3.5" />New folder</button>
        </div> : null}
      </div>
    </div>
    <div className="flex h-8 shrink-0 items-center gap-1 border-b border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--fx-text)]">
      <ChevronDown className="h-3.5 w-3.5 shrink-0" /><span className="min-w-0 flex-1 truncate" title={projectName}>{projectName}</span>
      {boardName ? <span className="max-w-24 truncate bg-[var(--fx-accent-soft)] px-1.5 py-0.5 text-[9px] font-medium normal-case tracking-normal text-[var(--fx-text-muted)]" title={boardName}>{boardName}</span> : null}
    </div>
    <div className="px-2 pb-2 pt-2">
      <label className="flex h-9 items-center gap-1.5 border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-xs text-[var(--fx-text-muted)] focus-within:border-[var(--fx-info)]">
        <Search className="h-3.5 w-3.5 shrink-0" /><input className="min-w-0 flex-1 bg-transparent text-[var(--fx-text)] outline-none placeholder:text-[var(--fx-text-muted)]" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={searchScope === "names" ? "Find files" : "Find in files"} />
        {filter ? <button type="button" className="p-0.5 hover:bg-[var(--fx-hover)]" onClick={() => setFilter("")} title="Clear search" aria-label="Clear search"><X className="h-3.5 w-3.5" /></button> : null}
      </label>
      <div className="mt-1 flex h-7 overflow-hidden border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] text-[11px]">
        {(["names", "contents"] as const).map((scope) => <button key={scope} className={`min-w-0 flex-1 px-2 capitalize ${searchScope === scope ? "bg-[var(--fx-input)] font-semibold text-[var(--fx-text)] shadow-[inset_0_0_0_1px_var(--fx-border)]" : "text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"}`} onClick={() => setSearchScope(scope)}>{scope}</button>)}
      </div>
    </div>
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {filterActive ? <div className="shrink-0 px-3 pb-1 text-[10px] text-[var(--fx-text-muted)]">{searchingContents ? "Searching..." : `${visibleFiles} ${visibleFiles === 1 ? "file" : "files"}`}</div> : null}
      <div className="min-h-0 flex-1 overflow-auto px-1 pb-1">
        {tree.length > 0 ? <ul role="tree" aria-label={`${projectName} files`}>{tree.map((node) => <TreeItem key={node.path} node={node} depth={0} selectedPath={selectedPath} revealedPaths={revealedPaths} onOpenFile={onOpenFile} onCreateFile={(basePath) => beginCreate("file", basePath)} onCreateFolder={(basePath) => beginCreate("folder", basePath)} onRename={beginRename} onDelete={onDelete} onContextMenu={(event, selectedNode) => { event.preventDefault(); event.stopPropagation(); setContextMenu({ x: event.clientX, y: event.clientY, node: selectedNode }); }} editing={editing} onEditValue={(value) => setEditing((current) => current ? { ...current, value } : current)} onCommitEdit={commitEdit} onCancelEdit={() => setEditing(null)} filterActive={filterActive} collapseToken={collapseToken} />)}</ul> : <div className="px-4 py-8 text-center text-sm leading-5 text-[var(--fx-text-muted)]">{emptyMessage}</div>}
      </div>
    </div>
    <div className="flex h-7 shrink-0 items-center justify-between border-t border-[var(--fx-border)] px-3 text-[10px] text-[var(--fx-text-muted)]"><span>{entries.filter((entry) => entry.kind === "file").length} files</span><span className="max-w-44 truncate text-[var(--fx-success)]" aria-live="polite">{notice ?? ""}</span></div>
    {contextMenu ? <div className="fixed z-50 min-w-44 overflow-hidden border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] py-1 text-xs text-[var(--fx-text)] shadow-lg" style={{ left: contextMenu.x, top: contextMenu.y }} onClick={(event) => event.stopPropagation()}>
      {contextMenu.node.kind === "folder" ? <><button className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-[var(--fx-hover)]" onClick={() => beginCreate("file", contextMenu.node.path)}><FilePlus2 className="h-3.5 w-3.5" />New file</button><button className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-[var(--fx-hover)]" onClick={() => beginCreate("folder", contextMenu.node.path)}><FolderPlus className="h-3.5 w-3.5" />New folder</button></> : null}
      {contextMenu.node.kind === "file" ? <button className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-[var(--fx-hover)]" onClick={() => { setContextMenu(null); onOpenFile(contextMenu.node.entry); }}><FileCode2 className="h-3.5 w-3.5" />Open</button> : null}
      <button className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-[var(--fx-hover)]" onClick={() => beginRename(contextMenu.node.entry)}><Pencil className="h-3.5 w-3.5" />Rename</button>
      <button className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-[var(--fx-hover)]" onClick={() => void copyRelativePath(contextMenu.node.path)}><ClipboardCopy className="h-3.5 w-3.5" />Copy relative path</button>
      <div className="my-1 border-t border-[var(--fx-border)]" /><button className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[var(--fx-error)] hover:bg-[var(--fx-hover)]" onClick={() => { setContextMenu(null); onDelete(contextMenu.node.entry); }}><Trash2 className="h-3.5 w-3.5" />Delete</button>
    </div> : null}
  </section>;
}
