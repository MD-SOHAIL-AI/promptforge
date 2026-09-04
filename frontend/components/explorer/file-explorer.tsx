"use client";

import Fuse, { type FuseResultMatch } from "fuse.js";
import {
  KeyboardEvent as ReactKeyboardEvent,
  DragEvent as ReactDragEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Binary,
  Braces,
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  Copy,
  File,
  FileCode,
  FileCode2,
  FileCog,
  FilePlus2,
  FileSpreadsheet,
  FileText,
  FileType2,
  Folder,
  FolderOpen,
  FolderPlus,
  Globe,
  Image as ImageIcon,
  ListTree,
  Lock,
  Palette,
  Pencil,
  RefreshCw,
  Search,
  Terminal,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { ErrorNote } from "@/components/ui/error-note";
import { IconButton } from "@/components/ui/icon-button";
import { Skeleton } from "@/components/ui/skeleton";
import { promptForgeApi } from "@/lib/api";
import { toErrorMessage } from "@/lib/errors";
import { notify } from "@/lib/notify";
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
  isLoading?: boolean;
  error?: string | null;
  projectId?: string | null;
  onOpenFile: (entry: WorkspaceEntry) => void;
  onCreateFile: (basePath?: string, path?: string) => void;
  onCreateFolder: (basePath?: string, path?: string) => void;
  onRename: (entry: WorkspaceEntry, path?: string) => void;
  onDelete: (entry: WorkspaceEntry) => void;
  onRefresh: () => void;
}

interface EditingState {
  mode: "create" | "rename";
  kind: "file" | "folder";
  path: string;
  value: string;
  entry?: WorkspaceEntry;
  basePath?: string;
}

interface ContextMenuState {
  x: number;
  y: number;
  node: TreeNode;
}

interface DraggedEntry {
  path: string;
  kind: "file" | "folder";
}

interface IconSpec {
  icon: LucideIcon;
  color: string;
}

interface VisibleRow {
  node: TreeNode;
  depth: number;
  parentPath: string | null;
}

const ICON_PALETTE = {
  blue: "#519aba",
  teal: "#00979d",
  navy: "#3572a5",
  tsBlue: "#3178c6",
  gold: "#cbcb41",
  yellow: "#e5c07b",
  green: "#98c379",
  mint: "#56b6c2",
  orange: "#d19a66",
  rust: "#e06c75",
  red: "#f14c4c",
  purple: "#c678dd",
  violet: "#a074c4",
  pink: "#f06292",
  gray: "#9aa4ad",
  slate: "#7d8891",
  tan: "#dcb67a",
  lime: "#7cb342",
  sky: "#61afef",
} as const;

const EXTENSION_ICONS: Record<string, IconSpec> = {
  c: { icon: FileCode2, color: ICON_PALETTE.blue },
  cpp: { icon: FileCode2, color: ICON_PALETTE.blue },
  cc: { icon: FileCode2, color: ICON_PALETTE.blue },
  cxx: { icon: FileCode2, color: ICON_PALETTE.blue },
  "c++": { icon: FileCode2, color: ICON_PALETTE.blue },
  h: { icon: FileCode2, color: ICON_PALETTE.violet },
  hpp: { icon: FileCode2, color: ICON_PALETTE.violet },
  hh: { icon: FileCode2, color: ICON_PALETTE.violet },
  hxx: { icon: FileCode2, color: ICON_PALETTE.violet },
  ino: { icon: FileCode2, color: ICON_PALETTE.teal },
  pde: { icon: FileCode2, color: ICON_PALETTE.teal },
  py: { icon: FileCode, color: ICON_PALETTE.navy },
  pyw: { icon: FileCode, color: ICON_PALETTE.navy },
  pyi: { icon: FileCode, color: ICON_PALETTE.navy },
  ipynb: { icon: Braces, color: ICON_PALETTE.orange },
  ts: { icon: FileType2, color: ICON_PALETTE.tsBlue },
  tsx: { icon: FileType2, color: ICON_PALETTE.tsBlue },
  mts: { icon: FileType2, color: ICON_PALETTE.tsBlue },
  cts: { icon: FileType2, color: ICON_PALETTE.tsBlue },
  js: { icon: FileType2, color: ICON_PALETTE.gold },
  jsx: { icon: FileType2, color: ICON_PALETTE.gold },
  mjs: { icon: FileType2, color: ICON_PALETTE.gold },
  cjs: { icon: FileType2, color: ICON_PALETTE.gold },
  json: { icon: Braces, color: ICON_PALETTE.yellow },
  jsonc: { icon: Braces, color: ICON_PALETTE.yellow },
  json5: { icon: Braces, color: ICON_PALETTE.yellow },
  md: { icon: FileText, color: ICON_PALETTE.sky },
  markdown: { icon: FileText, color: ICON_PALETTE.sky },
  mdx: { icon: FileText, color: ICON_PALETTE.sky },
  yaml: { icon: ListTree, color: ICON_PALETTE.rust },
  yml: { icon: ListTree, color: ICON_PALETTE.rust },
  toml: { icon: FileCog, color: ICON_PALETTE.gray },
  ini: { icon: FileCog, color: ICON_PALETTE.green },
  cfg: { icon: FileCog, color: ICON_PALETTE.green },
  conf: { icon: FileCog, color: ICON_PALETTE.green },
  properties: { icon: FileCog, color: ICON_PALETTE.green },
  xml: { icon: FileCode, color: ICON_PALETTE.orange },
  plist: { icon: FileCode, color: ICON_PALETTE.orange },
  html: { icon: Globe, color: ICON_PALETTE.orange },
  htm: { icon: Globe, color: ICON_PALETTE.orange },
  vue: { icon: Globe, color: ICON_PALETTE.green },
  svelte: { icon: Globe, color: ICON_PALETTE.red },
  css: { icon: Palette, color: ICON_PALETTE.tsBlue },
  scss: { icon: Palette, color: ICON_PALETTE.pink },
  sass: { icon: Palette, color: ICON_PALETTE.pink },
  less: { icon: Palette, color: ICON_PALETTE.navy },
  styl: { icon: Palette, color: ICON_PALETTE.green },
  sh: { icon: Terminal, color: ICON_PALETTE.mint },
  bash: { icon: Terminal, color: ICON_PALETTE.mint },
  zsh: { icon: Terminal, color: ICON_PALETTE.mint },
  fish: { icon: Terminal, color: ICON_PALETTE.mint },
  ps1: { icon: Terminal, color: ICON_PALETTE.tsBlue },
  bat: { icon: Terminal, color: ICON_PALETTE.slate },
  cmd: { icon: Terminal, color: ICON_PALETTE.slate },
  txt: { icon: FileText, color: ICON_PALETTE.gray },
  log: { icon: FileText, color: ICON_PALETTE.slate },
  csv: { icon: FileSpreadsheet, color: ICON_PALETTE.green },
  tsv: { icon: FileSpreadsheet, color: ICON_PALETTE.green },
  pdf: { icon: FileText, color: ICON_PALETTE.red },
  png: { icon: ImageIcon, color: ICON_PALETTE.violet },
  jpg: { icon: ImageIcon, color: ICON_PALETTE.violet },
  jpeg: { icon: ImageIcon, color: ICON_PALETTE.violet },
  gif: { icon: ImageIcon, color: ICON_PALETTE.violet },
  webp: { icon: ImageIcon, color: ICON_PALETTE.violet },
  bmp: { icon: ImageIcon, color: ICON_PALETTE.violet },
  ico: { icon: ImageIcon, color: ICON_PALETTE.violet },
  avif: { icon: ImageIcon, color: ICON_PALETTE.violet },
  svg: { icon: ImageIcon, color: ICON_PALETTE.lime },
  bin: { icon: Binary, color: ICON_PALETTE.rust },
  elf: { icon: Binary, color: ICON_PALETTE.rust },
  axf: { icon: Binary, color: ICON_PALETTE.rust },
  uf2: { icon: Binary, color: ICON_PALETTE.orange },
  hex: { icon: Binary, color: ICON_PALETTE.orange },
  o: { icon: Binary, color: ICON_PALETTE.slate },
  obj: { icon: Binary, color: ICON_PALETTE.slate },
  dll: { icon: Binary, color: ICON_PALETTE.slate },
  so: { icon: Binary, color: ICON_PALETTE.slate },
  dylib: { icon: Binary, color: ICON_PALETTE.slate },
  exe: { icon: Binary, color: ICON_PALETTE.slate },
  map: { icon: FileText, color: ICON_PALETTE.blue },
  lock: { icon: Lock, color: ICON_PALETTE.slate },
};

const DOTFILE_ICONS: Record<string, IconSpec> = {
  ".gitignore": { icon: FileCog, color: ICON_PALETTE.orange },
  ".gitattributes": { icon: FileCog, color: ICON_PALETTE.orange },
  ".editorconfig": { icon: FileCog, color: ICON_PALETTE.gray },
  ".npmrc": { icon: FileCog, color: ICON_PALETTE.red },
  ".env": { icon: Lock, color: ICON_PALETTE.green },
  ".dockerignore": { icon: FileCog, color: ICON_PALETTE.sky },
};

const DEFAULT_FILE_ICON: IconSpec = { icon: File, color: ICON_PALETTE.gray };
const FOLDER_ICON_COLOR = ICON_PALETTE.tan;
const SKELETON_WIDTHS = ["w-[64%]", "w-[88%]", "w-[76%]", "w-[96%]", "w-[70%]", "w-[82%]", "w-[58%]", "w-[90%]"];

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

function flattenNodes(nodes: TreeNode[]): TreeNode[] {
  return nodes.flatMap((node) => [node, ...flattenNodes(node.children)]);
}

function pruneTreeToPaths(nodes: TreeNode[], keep: Set<string>): TreeNode[] {
  const walk = (list: TreeNode[]): TreeNode[] =>
    list.flatMap((node) => {
      const children = walk(node.children);
      if (!keep.has(node.path) && children.length === 0) return [];
      return [{ ...node, children }];
    });
  return walk(nodes);
}

function ancestorsOf(path: string) {
  const parts = path.split("/").filter(Boolean);
  const ancestors: string[] = [];
  for (let index = 1; index < parts.length; index += 1) {
    ancestors.push(parts.slice(0, index).join("/"));
  }
  return ancestors;
}

function nameHighlightMask(matches: readonly FuseResultMatch[] | undefined, name: string): boolean[] | null {
  const nameMatches = matches?.filter((match) => match.key === "name");
  if (!nameMatches || nameMatches.length === 0) return null;
  const mask = new Array<boolean>(name.length).fill(false);
  for (const match of nameMatches) {
    for (const [start, end] of match.indices) {
      for (let index = start; index <= end && index < name.length; index += 1) {
        mask[index] = true;
      }
    }
  }
  return mask.some(Boolean) ? mask : null;
}

function uniqueDraftPath(entries: WorkspaceEntry[], kind: "file" | "folder", basePath?: string) {
  const used = new Set(entries.map((entry) => entry.path.toLowerCase()));
  const prefix = basePath ? `${basePath}/` : "";
  const base = kind === "file" ? "new_file" : "new_folder";
  const suffix = kind === "file" ? ".cpp" : "";
  let index = 1;
  let candidate = `${prefix}${base}${suffix}`;
  while (used.has(candidate.toLowerCase())) {
    index += 1;
    candidate = `${prefix}${base}_${index}${suffix}`;
  }
  return candidate;
}

function uniqueCopyPath(entries: WorkspaceEntry[], path: string) {
  const used = new Set(entries.map((entry) => entry.path.toLowerCase()));
  const dotIndex = path.lastIndexOf(".");
  const stem = dotIndex > 0 ? path.slice(0, dotIndex) : path;
  const extension = dotIndex > 0 ? path.slice(dotIndex) : "";
  let candidate = `${stem}-copy${extension}`;
  let index = 2;
  while (used.has(candidate.toLowerCase())) {
    candidate = `${stem}-copy-${index}${extension}`;
    index += 1;
  }
  return candidate;
}

function iconFor(node: TreeNode, open: boolean) {
  if (node.kind === "folder") {
    const Icon = open ? FolderOpen : Folder;
    return <Icon className="h-4 w-4 shrink-0" style={{ color: FOLDER_ICON_COLOR }} />;
  }
  const lower = node.name.toLowerCase();
  const spec =
    (lower === "platformio.ini"
      ? { icon: FileCog, color: ICON_PALETTE.lime }
      : DOTFILE_ICONS[lower]) ??
    (() => {
      const dotIndex = lower.lastIndexOf(".");
      const extension = dotIndex > 0 ? lower.slice(dotIndex + 1) : "";
      return EXTENSION_ICONS[extension];
    })() ??
    DEFAULT_FILE_ICON;
  const Icon = spec.icon;
  return <Icon className="h-4 w-4 shrink-0" style={{ color: spec.color }} />;
}

function HighlightedName({ name, mask }: { name: string; mask: boolean[] | null }) {
  if (!mask) return <>{name}</>;
  return (
    <>
      {name.split("").map((character, index) => (
        <span
          key={`${index}-${character}`}
          className={
            mask[index]
              ? "rounded-[2px] bg-[var(--fx-accent-soft)] font-semibold text-[var(--fx-text)]"
              : undefined
          }
        >
          {character}
        </span>
      ))}
    </>
  );
}

export function FileExplorer({
  entries,
  selectedPath,
  revealedPaths = [],
  projectName = "Workspace",
  boardName,
  isLoading = false,
  error = null,
  projectId = null,
  onOpenFile,
  onCreateFile,
  onCreateFolder,
  onRename,
  onDelete,
  onRefresh,
}: FileExplorerProps) {
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<EditingState | null>(null);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [menuPosition, setMenuPosition] = useState<{ left: number; top: number } | null>(null);
  const [expandedPaths, setExpandedPaths] = useState<Set<string> | null>(null);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [dragOverPath, setDragOverPath] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const rowRefs = useRef(new Map<string, HTMLDivElement>());
  const dragEntryRef = useRef<DraggedEntry | null>(null);
  const typeaheadRef = useRef({ value: "", timestamp: 0 });
  const editCancelledRef = useRef(false);
  const wasEditingRef = useRef(false);

  const draftEntries = useMemo(
    () => editing?.mode === "create"
      ? [...entries, { path: editing.path, kind: editing.kind }]
      : entries,
    [editing, entries],
  );
  const tree = useMemo(() => buildTree(draftEntries), [draftEntries]);
  const flatNodes = useMemo(() => flattenNodes(tree), [tree]);
  const knownPaths = useMemo(() => new Set(entries.map((entry) => entry.path)), [entries]);
  const filterQuery = filter.trim();
  const filterActive = filterQuery.length > 0;

  const fuse = useMemo(
    () =>
      new Fuse(flatNodes, {
        keys: ["name", "path"],
        threshold: 0.42,
        ignoreLocation: true,
        includeMatches: true,
        minMatchCharLength: 1,
      }),
    [flatNodes],
  );

  const { filteredTree, matchCount, highlightMasks } = useMemo(() => {
    if (!filterActive) {
      return {
        filteredTree: tree,
        matchCount: 0,
        highlightMasks: {} as Record<string, boolean[]>,
      };
    }
    const results = fuse.search(filterQuery);
    const keep = new Set<string>();
    const masks: Record<string, boolean[]> = {};
    for (const result of results) {
      keep.add(result.item.path);
      const mask = nameHighlightMask(result.matches, result.item.name);
      if (mask) masks[result.item.path] = mask;
    }
    for (const node of flatNodes) {
      if (!keep.has(node.path)) continue;
      for (const ancestor of ancestorsOf(node.path)) keep.add(ancestor);
    }
    return {
      filteredTree: pruneTreeToPaths(tree, keep),
      matchCount: results.length,
      highlightMasks: masks,
    };
  }, [filterActive, filterQuery, flatNodes, fuse, tree]);

  const defaultExpandedPaths = useMemo(
    () => new Set(tree.filter((node) => node.kind === "folder").map((node) => node.path)),
    [tree],
  );

  const isExpanded = (node: TreeNode, depth: number) => {
    if (filterActive) return node.children.length > 0;
    if (expandedPaths) return expandedPaths.has(node.path);
    return depth < 1;
  };

  const visibleRows = (() => {
    const rows: VisibleRow[] = [];
    const walk = (nodes: TreeNode[], depth: number, parentPath: string | null) => {
      for (const node of nodes) {
        rows.push({ node, depth, parentPath });
        if (node.kind === "folder" && isExpanded(node, depth) && node.children.length > 0) {
          walk(node.children, depth + 1, node.path);
        }
      }
    };
    walk(filteredTree, 0, null);
    return rows;
  })();

  const activeRowIndex = (() => {
    if (visibleRows.length === 0) return -1;
    const byCursor = visibleRows.findIndex((row) => row.node.path === activePath);
    if (byCursor >= 0) return byCursor;
    const bySelection = visibleRows.findIndex((row) => row.node.path === selectedPath);
    return bySelection >= 0 ? bySelection : 0;
  })();
  const activeNodePath = activeRowIndex >= 0 ? visibleRows[activeRowIndex].node.path : null;

  const registerRow = (path: string, element: HTMLDivElement | null) => {
    if (element) rowRefs.current.set(path, element);
    else rowRefs.current.delete(path);
  };

  const focusRow = (path: string) => {
    window.requestAnimationFrame(() => {
      rowRefs.current.get(path)?.focus();
    });
  };

  const setActiveAndFocus = (path: string) => {
    setActivePath(path);
    focusRow(path);
  };

  const mutateExpanded = (mutate: (next: Set<string>) => void) => {
    setExpandedPaths((current) => {
      const next = new Set(current ?? defaultExpandedPaths);
      mutate(next);
      return next;
    });
  };

  const toggleFolder = (node: TreeNode) => {
    mutateExpanded((next) => {
      if (next.has(node.path)) next.delete(node.path);
      else next.add(node.path);
    });
  };

  useEffect(() => {
    if (!selectedPath && revealedPaths.length === 0) return;
    const additions = [
      ...(selectedPath ? ancestorsOf(selectedPath) : []),
      ...revealedPaths.flatMap((path) => ancestorsOf(path)),
    ];
    if (additions.length === 0) return;
    mutateExpanded((next) => {
      for (const path of additions) next.add(path);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPath, revealedPaths]);

  useEffect(() => {
    if (!contextMenu) {
      setMenuPosition(null);
      return;
    }
    const element = menuRef.current;
    if (!element) return;
    const rect = element.getBoundingClientRect();
    const margin = 8;
    const left = Math.max(margin, Math.min(contextMenu.x, window.innerWidth - rect.width - margin));
    const top = Math.max(margin, Math.min(contextMenu.y, window.innerHeight - rect.height - margin));
    setMenuPosition({ left, top });
  }, [contextMenu]);

  useEffect(() => {
    if (!contextMenu) return;
    const onPointerDown = (event: PointerEvent) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      setContextMenu(null);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      setContextMenu(null);
    };
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("keydown", onKeyDown, true);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("keydown", onKeyDown, true);
    };
  }, [contextMenu]);

  useEffect(() => {
    if (wasEditingRef.current && !editing && activeNodePath) focusRow(activeNodePath);
    wasEditingRef.current = Boolean(editing);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  const cancelEdit = () => {
    editCancelledRef.current = true;
    setEditing(null);
  };

  const beginCreate = (kind: "file" | "folder", basePath?: string) => {
    const path = uniqueDraftPath(entries, kind, basePath);
    setEditing({ mode: "create", kind, path, value: path, basePath });
    setContextMenu(null);
  };

  const beginRename = (entry: WorkspaceEntry) => {
    setEditing({ mode: "rename", kind: entry.kind, path: entry.path, value: entry.path, entry });
    setContextMenu(null);
  };

  const commitEdit = () => {
    if (!editing) return;
    const value = editing.value.trim().replaceAll("\\", "/");
    setEditing(null);
    if (!value || value === editing.entry?.path) return;
    if (editing.mode === "create") {
      if (editing.kind === "file") onCreateFile(editing.basePath, value);
      else onCreateFolder(editing.basePath, value);
      return;
    }
    if (editing.entry) onRename(editing.entry, value);
  };

  const copyPath = async (node: TreeNode) => {
    try {
      await navigator.clipboard.writeText(node.path);
      notify.success(`Copied path: ${node.path}`);
    } catch {
      notify.error(`Could not copy path for ${node.name}`);
    }
  };

  const duplicateEntry = async (node: TreeNode) => {
    if (!projectId || node.kind !== "file") return;
    const target = uniqueCopyPath(entries, node.path);
    try {
      const source = await promptForgeApi.fileContent(projectId, node.path);
      await promptForgeApi.createFile({
        project_id: projectId,
        path: target,
        kind: "file",
        content: source.content,
        file_type: source.file_type,
      });
      notify.success(`Duplicated ${node.name} → ${target}`);
      onRefresh();
    } catch (err) {
      notify.error(toErrorMessage(err, `Could not duplicate ${node.name}`));
    }
  };

  const clearDrag = () => {
    dragEntryRef.current = null;
    setDragOverPath(null);
  };

  const performMove = (targetFolder: TreeNode | null) => {
    const dragged = dragEntryRef.current;
    clearDrag();
    if (!dragged) return;
    const name = dragged.path.split("/").at(-1) ?? dragged.path;
    const target = targetFolder ? `${targetFolder.path}/${name}` : name;
    if (target === dragged.path) return;
    if (dragged.kind === "folder" && target.startsWith(`${dragged.path}/`)) {
      notify.warning("Cannot move a folder into itself");
      return;
    }
    if (knownPaths.has(target)) {
      notify.warning(`"${target}" already exists`);
      return;
    }
    const entry =
      entries.find((candidate) => candidate.path === dragged.path) ??
      ({ path: dragged.path, kind: dragged.kind } as WorkspaceEntry);
    onRename(entry, target);
  };

  const handleTreeDragOver = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!dragEntryRef.current) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const target = event.target as HTMLElement | null;
    if (target?.closest("[data-tree-row]")) return;
    setDragOverPath("__root__");
  };

  const handleRowDragOver = (event: ReactDragEvent<HTMLDivElement>, node: TreeNode) => {
    if (!dragEntryRef.current || node.kind !== "folder") return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "move";
    setDragOverPath(node.path);
  };

  const moveActiveBy = (offset: number) => {
    if (visibleRows.length === 0) return;
    const nextIndex = Math.min(visibleRows.length - 1, Math.max(0, activeRowIndex + offset));
    setActiveAndFocus(visibleRows[nextIndex].node.path);
  };

  const handleRowKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>, row: VisibleRow) => {
    if (editing?.path === row.node.path) return;
    const { key } = event;

    switch (key) {
      case "ArrowDown":
        event.preventDefault();
        moveActiveBy(1);
        return;
      case "ArrowUp":
        event.preventDefault();
        moveActiveBy(-1);
        return;
      case "ArrowRight": {
        event.preventDefault();
        if (row.node.kind !== "folder") return;
        if (!isExpanded(row.node, row.depth)) {
          toggleFolder(row.node);
          return;
        }
        const child = visibleRows[activeRowIndex + 1];
        if (child?.parentPath === row.node.path) setActiveAndFocus(child.node.path);
        return;
      }
      case "ArrowLeft":
        event.preventDefault();
        if (row.node.kind === "folder" && isExpanded(row.node, row.depth)) {
          toggleFolder(row.node);
          return;
        }
        if (row.parentPath) setActiveAndFocus(row.parentPath);
        return;
      case "Home":
        event.preventDefault();
        if (visibleRows[0]) setActiveAndFocus(visibleRows[0].node.path);
        return;
      case "End": {
        event.preventDefault();
        const lastRow = visibleRows.at(-1);
        if (lastRow) setActiveAndFocus(lastRow.node.path);
        return;
      }
      case "Enter":
      case " ":
        event.preventDefault();
        if (row.node.kind === "folder") toggleFolder(row.node);
        else onOpenFile(row.node.entry);
        return;
      case "F2":
        event.preventDefault();
        beginRename(row.node.entry);
        return;
      case "Delete":
        event.preventDefault();
        onDelete(row.node.entry);
        return;
      default:
        break;
    }

    if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      const now = Date.now();
      const buffer =
        now - typeaheadRef.current.timestamp < 600
          ? typeaheadRef.current.value + key.toLowerCase()
          : key.toLowerCase();
      typeaheadRef.current = { value: buffer, timestamp: now };
      const start = visibleRows.findIndex((candidate) => candidate.node.path === activeNodePath);
      const scan = (from: number, to: number) => {
        for (let index = Math.max(0, from); index < to; index += 1) {
          if (visibleRows[index].node.name.toLowerCase().startsWith(buffer)) return index;
        }
        return -1;
      };
      const forward = scan(start + 1, visibleRows.length);
      const wrapped = forward >= 0 ? forward : scan(0, start + 1);
      if (wrapped >= 0) setActiveAndFocus(visibleRows[wrapped].node.path);
    }
  };

  const menuNode = contextMenu?.node ?? null;

  const menuItemClass =
    "flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-[var(--fx-hover)]";
  const shortcutClass =
    "ml-auto rounded border border-[var(--fx-border-soft)] px-1 text-[9px] uppercase text-[var(--fx-text-muted)]";

  return (
    <section className="fx-side-panel relative flex h-full min-h-0 flex-col border-r border-[var(--fx-border)]">
      <div className="flex h-9 shrink-0 items-center justify-between px-3">
        <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--fx-text-muted)]">Explorer</p>
        <div className="flex items-center gap-0.5 text-[var(--fx-text-muted)]">
          <IconButton label="Collapse all" onClick={() => setExpandedPaths(new Set())}>
            <ChevronsDownUp className="h-4 w-4" />
          </IconButton>
          <IconButton label="Refresh" onClick={onRefresh}>
            <RefreshCw className="h-4 w-4" />
          </IconButton>
          <IconButton label="New file" onClick={() => beginCreate("file")}>
            <FilePlus2 className="h-4 w-4" />
          </IconButton>
          <IconButton label="New folder" onClick={() => beginCreate("folder")}>
            <FolderPlus className="h-4 w-4" />
          </IconButton>
        </div>
      </div>
      <div className="flex h-8 shrink-0 items-center gap-1 border-y border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--fx-text)]">
        <ChevronDown className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate" title={projectName}>{projectName}</span>
        {boardName ? <span className="max-w-24 truncate rounded bg-[var(--fx-accent-soft)] px-1.5 py-0.5 text-[9px] font-medium normal-case tracking-normal text-[var(--fx-text-muted)]" title={boardName}>{boardName}</span> : null}
      </div>
      <label className="mx-2 mt-2 flex h-8 shrink-0 items-center gap-1.5 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-xs text-[var(--fx-text-muted)]">
        <Search className="h-3.5 w-3.5 shrink-0" />
        <input
          className="min-w-0 flex-1 bg-transparent text-[var(--fx-text)] outline-none placeholder:text-[var(--fx-text-muted)]"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Filter files"
        />
        {filter ? (
          <button type="button" className="rounded p-0.5 hover:bg-[var(--fx-hover)]" onClick={() => setFilter("")} title="Clear filter">
            <X className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </label>
      {filterActive ? (
        <p className="shrink-0 px-3 pt-1.5 text-[10px] uppercase tracking-wide text-[var(--fx-text-muted)]">
          {matchCount} {matchCount === 1 ? "match" : "matches"}
        </p>
      ) : null}
      <div
        className={`flex-1 overflow-auto px-1 py-1.5 ${dragOverPath === "__root__" ? "rounded ring-1 ring-inset ring-[var(--fx-info)]" : ""}`}
        onDragOver={handleTreeDragOver}
        onDragLeave={() => setDragOverPath((current) => (current === "__root__" ? null : current))}
        onDrop={(event) => {
          if (!dragEntryRef.current) return;
          event.preventDefault();
          performMove(null);
        }}
      >
        {isLoading ? (
          <div className="grid gap-1.5 px-1 pt-1">
            {SKELETON_WIDTHS.map((width) => (
              <Skeleton key={width} className={`h-7 rounded ${width}`} />
            ))}
          </div>
        ) : error ? (
          <div className="px-2 pt-2">
            <ErrorNote tone="error" message={error} onRetry={onRefresh} />
          </div>
        ) : entries.length === 0 ? (
          <div className="px-2 pt-4">
            <EmptyState
              icon={<FolderOpen className="h-8 w-8" style={{ color: FOLDER_ICON_COLOR }} />}
              title="No project files loaded."
              hint="Open a project from the sidebar, import a workspace folder, or generate one from a prompt to see its files here."
              action={
                <button
                  type="button"
                  className="rounded border border-[var(--fx-border)] px-2.5 py-1 text-xs text-[var(--fx-text)] hover:bg-[var(--fx-hover)]"
                  onClick={onRefresh}
                >
                  Check again
                </button>
              }
            />
          </div>
        ) : filteredTree.length === 0 ? (
          <div className="px-2 pt-4">
            <EmptyState
              icon={<Search className="h-7 w-7 text-[var(--fx-text-muted)]" />}
              title="No matches"
              hint={`Nothing in ${projectName} fuzzy-matches "${filterQuery}".`}
              action={
                <button
                  type="button"
                  className="rounded border border-[var(--fx-border)] px-2.5 py-1 text-xs text-[var(--fx-text)] hover:bg-[var(--fx-hover)]"
                  onClick={() => setFilter("")}
                >
                  Clear filter
                </button>
              }
            />
          </div>
        ) : (
          <ul className="space-y-0.5" role="tree" aria-label={`${projectName} files`}>
            {visibleRows.map((row) => (
              <li key={row.node.path}>
                <div
                  ref={(element) => registerRow(row.node.path, element)}
                  className={`group flex h-7 cursor-pointer items-center gap-1 rounded px-2 text-sm transition-colors duration-150 ${
                    revealedPaths.includes(row.node.path) ? "fx-tree-reveal" : ""
                  } ${
                    selectedPath === row.node.path
                      ? "bg-[var(--fx-accent-soft)] text-[var(--fx-text)]"
                      : "text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
                  } ${dragOverPath === row.node.path ? "ring-1 ring-inset ring-[var(--fx-info)]" : ""}`}
                  style={{ paddingLeft: `${8 + row.depth * 14}px` }}
                  draggable={!editing || editing.path !== row.node.path}
                  data-tree-row=""
                  role="treeitem"
                  aria-selected={selectedPath === row.node.path}
                  aria-expanded={row.node.kind === "folder" ? isExpanded(row.node, row.depth) : undefined}
                  aria-level={row.depth + 1}
                  tabIndex={activeNodePath === row.node.path ? 0 : -1}
                  onDragStart={(event) => {
                    event.dataTransfer.effectAllowed = "move";
                    event.dataTransfer.setData("text/plain", row.node.path);
                    dragEntryRef.current = { path: row.node.path, kind: row.node.kind };
                  }}
                  onDragEnd={clearDrag}
                  onDragOver={(event) => handleRowDragOver(event, row.node)}
                  onDragLeave={() =>
                    setDragOverPath((current) => (current === row.node.path ? null : current))
                  }
                  onDrop={(event) => {
                    if (!dragEntryRef.current) return;
                    event.preventDefault();
                    event.stopPropagation();
                    performMove(row.node);
                  }}
                  onClick={() => {
                    if (editing?.path === row.node.path) return;
                    if (row.node.kind === "folder") toggleFolder(row.node);
                    else onOpenFile(row.node.entry);
                  }}
                  onFocus={() => setActivePath(row.node.path)}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setActivePath(row.node.path);
                    setMenuPosition(null);
                    setContextMenu({ x: event.clientX, y: event.clientY, node: row.node });
                  }}
                  onKeyDown={(event) => handleRowKeyDown(event, row)}
                >
                  {row.node.kind === "folder" ? (
                    isExpanded(row.node, row.depth)
                      ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--fx-text-muted)]" />
                      : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--fx-text-muted)]" />
                  ) : (
                    <span className="w-3.5 shrink-0" />
                  )}
                  {iconFor(row.node, row.node.kind === "folder" && isExpanded(row.node, row.depth))}
                  {editing?.mode === "rename" && editing.path === row.node.path ? (
                    <input
                      autoFocus
                      className="min-w-0 flex-1 rounded border border-[var(--fx-info)] bg-[var(--fx-input)] px-1 py-0.5 text-xs text-[var(--fx-text)] outline-none"
                      value={editing.value}
                      onChange={(event) =>
                        setEditing((current) => (current ? { ...current, value: event.target.value } : current))
                      }
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          commitEdit();
                        }
                        if (event.key === "Escape") {
                          event.preventDefault();
                          cancelEdit();
                        }
                      }}
                      onBlur={() => {
                        if (editCancelledRef.current) {
                          editCancelledRef.current = false;
                          return;
                        }
                        commitEdit();
                      }}
                    />
                  ) : editing?.mode === "create" && editing.path === row.node.path ? (
                    <input
                      autoFocus
                      className="min-w-0 flex-1 rounded border border-[var(--fx-info)] bg-[var(--fx-input)] px-1 py-0.5 text-xs text-[var(--fx-text)] outline-none"
                      value={editing.value}
                      onChange={(event) =>
                        setEditing((current) => (current ? { ...current, value: event.target.value } : current))
                      }
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          commitEdit();
                        }
                        if (event.key === "Escape") {
                          event.preventDefault();
                          cancelEdit();
                        }
                      }}
                      onBlur={() => {
                        if (editCancelledRef.current) {
                          editCancelledRef.current = false;
                          return;
                        }
                        commitEdit();
                      }}
                    />
                  ) : (
                    <span className="min-w-0 flex-1 truncate">
                      <HighlightedName name={row.node.name} mask={highlightMasks[row.node.path] ?? null} />
                    </span>
                  )}
                  <div className="flex opacity-0 transition group-hover:opacity-100">
                    {row.node.kind === "folder" ? (
                      <>
                        <button
                          className="rounded p-1 hover:bg-[var(--fx-hover)]"
                          title="New file"
                          aria-label="New file"
                          onClick={(event) => {
                            event.stopPropagation();
                            beginCreate("file", row.node.path);
                          }}
                        >
                          <FilePlus2 className="h-3.5 w-3.5" />
                        </button>
                        <button
                          className="rounded p-1 hover:bg-[var(--fx-hover)]"
                          title="New folder"
                          aria-label="New folder"
                          onClick={(event) => {
                            event.stopPropagation();
                            beginCreate("folder", row.node.path);
                          }}
                        >
                          <FolderPlus className="h-3.5 w-3.5" />
                        </button>
                      </>
                    ) : null}
                    <button
                      className="rounded p-1 hover:bg-[var(--fx-hover)]"
                      title="Rename"
                      aria-label="Rename"
                      onClick={(event) => {
                        event.stopPropagation();
                        beginRename(row.node.entry);
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      className="rounded p-1 hover:bg-[var(--fx-hover)]"
                      title="Delete"
                      aria-label="Delete"
                      onClick={(event) => {
                        event.stopPropagation();
                        onDelete(row.node.entry);
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      {contextMenu && menuNode ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label={`${menuNode.name} actions`}
          className="fixed z-50 min-w-48 overflow-hidden rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] py-1 text-xs text-[var(--fx-text)] shadow-lg"
          style={{
            left: menuPosition?.left ?? contextMenu.x,
            top: menuPosition?.top ?? contextMenu.y,
            visibility: menuPosition ? "visible" : "hidden",
          }}
          onClick={(event) => event.stopPropagation()}
          onContextMenu={(event) => event.preventDefault()}
        >
          {menuNode.kind === "folder" ? (
            <>
              <button role="menuitem" className={menuItemClass} onClick={() => beginCreate("file", menuNode.path)}>
                <FilePlus2 className="h-3.5 w-3.5" />
                New file
              </button>
              <button role="menuitem" className={menuItemClass} onClick={() => beginCreate("folder", menuNode.path)}>
                <FolderPlus className="h-3.5 w-3.5" />
                New folder
              </button>
              <div className="my-1 border-t border-[var(--fx-border)]" />
            </>
          ) : null}
          {menuNode.kind === "file" ? (
            <button
              role="menuitem"
              className={menuItemClass}
              onClick={() => {
                setContextMenu(null);
                onOpenFile(menuNode.entry);
              }}
            >
              <File className="h-3.5 w-3.5" />
              Open
            </button>
          ) : null}
          <button
            role="menuitem"
            className={menuItemClass}
            onClick={() => {
              setContextMenu(null);
              void copyPath(menuNode);
            }}
          >
            <Copy className="h-3.5 w-3.5" />
            Copy path
          </button>
          <div className="my-1 border-t border-[var(--fx-border)]" />
          <button role="menuitem" className={menuItemClass} onClick={() => beginRename(menuNode.entry)}>
            <Pencil className="h-3.5 w-3.5" />
            Rename
            <kbd className={shortcutClass}>F2</kbd>
          </button>
          {menuNode.kind === "file" && projectId ? (
            <button
              role="menuitem"
              className={menuItemClass}
              onClick={() => {
                setContextMenu(null);
                void duplicateEntry(menuNode);
              }}
            >
              <Copy className="h-3.5 w-3.5" />
              Duplicate
            </button>
          ) : null}
          <div className="my-1 border-t border-[var(--fx-border)]" />
          <button
            role="menuitem"
            className={`${menuItemClass} text-[var(--fx-error)]`}
            onClick={() => {
              setContextMenu(null);
              onDelete(menuNode.entry);
            }}
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete
            <kbd className={shortcutClass}>Del</kbd>
          </button>
        </div>
      ) : null}
    </section>
  );
}
