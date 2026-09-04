"use client";

import { CircuitBoard, Code2, FileDiff, FolderOpen, Hammer, History, Home, Radio, Search, Settings, Sparkles, TerminalSquare } from "lucide-react";
import Fuse from "fuse.js";
import { Command } from "cmdk";
import { useEffect, useMemo, useState } from "react";

import { FxOverlay } from "@/components/ui/fx-overlay";
import { Kbd } from "@/components/ui/kbd";
import type { NexusCanvas } from "@/components/nexus/work-canvases";

interface CommandCenterProps {
  open: boolean;
  onClose: () => void;
  onNavigate: (target: "home" | "task" | "settings") => void;
  onCanvas: (canvas: NexusCanvas) => void;
  onOpenProject: () => void;
  onAskForge: (prompt: string) => void;
}

type CommandItem = {
  id: string;
  label: string;
  detail: string;
  icon: typeof Home;
  group: "Navigate" | "Files" | "Devices" | "Settings";
  keywords?: string;
  target?: "home" | "task" | "settings";
  canvas?: NexusCanvas;
  action?: "project";
};

const commandItems: CommandItem[] = [
  { id: "home", label: "Go home", detail: "ForgeX Nexus home", icon: Home, group: "Navigate", target: "home", keywords: "start overview dashboard" },
  { id: "forge", label: "Open Forge", detail: "Current engineering task", icon: Sparkles, group: "Navigate", target: "task", keywords: "agent chat task run" },
  { id: "terminal", label: "Open engineering console", detail: "Terminal, problems and raw output", icon: TerminalSquare, group: "Navigate", canvas: "terminal", keywords: "logs shell output problems" },
  { id: "project", label: "Open project", detail: "Import an existing workspace", icon: FolderOpen, group: "Files", action: "project", keywords: "folder import workspace directory" },
  { id: "code", label: "Open code canvas", detail: "Manual source workspace", icon: Code2, group: "Files", canvas: "code", keywords: "editor source files workspace" },
  { id: "review", label: "Review changes", detail: "Diff and staged ChangeSet", icon: FileDiff, group: "Files", canvas: "review", keywords: "diff changeset git stage apply" },
  { id: "build", label: "Open build artifact", detail: "Build status and firmware evidence", icon: Hammer, group: "Devices", canvas: "build", keywords: "compile firmware flash status" },
  { id: "hardware", label: "Open hardware canvas", detail: "Devices, board and firmware", icon: CircuitBoard, group: "Devices", canvas: "hardware", keywords: "board device port detect" },
  { id: "serial", label: "Open live device", detail: "Serial telemetry and insights", icon: Radio, group: "Devices", canvas: "serial", keywords: "monitor uart telemetry stream" },
  { id: "settings", label: "Settings", detail: "Forge, models, hardware and appearance", icon: Settings, group: "Settings", target: "settings", keywords: "preferences theme models api keys" },
];

const groups: Array<CommandItem["group"]> = ["Navigate", "Files", "Devices", "Settings"];

const fuse = new Fuse(commandItems, {
  keys: ["label", "detail", "keywords"],
  threshold: 0.38,
  ignoreLocation: true,
});

const RECENTS_KEY = "forgex-command-recents";
const RECENTS_LIMIT = 5;

function loadRecents(): string[] {
  try {
    const raw = window.localStorage.getItem(RECENTS_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function saveRecents(ids: string[]) {
  try {
    window.localStorage.setItem(RECENTS_KEY, JSON.stringify(ids.slice(0, RECENTS_LIMIT)));
  } catch {
    return;
  }
}

function ItemRow({ item, hint }: { item: CommandItem; hint?: string }) {
  const Icon = item.icon;
  return (
    <>
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[var(--fx-panel-elevated)] text-[var(--fx-text-muted)] group-data-[selected=true]:bg-[var(--fx-accent-soft)] group-data-[selected=true]:text-[var(--fx-accent)]">
        <Icon className="h-4 w-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[11px] font-medium text-[var(--fx-text)]">{item.label}</span>
        <span className="mt-0.5 block truncate text-[9px] text-[var(--fx-text-muted)]">{item.detail}</span>
      </span>
      {hint ? <Kbd>{hint}</Kbd> : null}
    </>
  );
}

export function CommandCenter({ open, onClose, onNavigate, onCanvas, onOpenProject, onAskForge }: CommandCenterProps) {
  const [query, setQuery] = useState("");
  const [recents, setRecents] = useState<string[]>([]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setRecents(loadRecents());
  }, [open]);

  const forcedAsk = query.startsWith(">");
  const askQuery = forcedAsk ? query.slice(1).trim() : query.trim();
  const searching = !forcedAsk && askQuery.length > 0;

  const matched = useMemo(() => {
    if (!searching || forcedAsk) return [];
    return fuse.search(askQuery).map((result) => result.item);
  }, [askQuery, forcedAsk, searching]);

  const recentItems = useMemo(
    () => recents.map((id) => commandItems.find((item) => item.id === id)).filter((item): item is CommandItem => Boolean(item)),
    [recents],
  );

  const grouped = useMemo(
    () =>
      groups
        .map((group) => ({ group, items: (searching ? matched : commandItems).filter((item) => item.group === group) }))
        .filter(({ items }) => items.length),
    [matched, searching],
  );

  const pushRecent = (id: string) => {
    const next = [id, ...recents.filter((recent) => recent !== id)].slice(0, RECENTS_LIMIT);
    setRecents(next);
    saveRecents(next);
  };

  const execute = (item: CommandItem) => {
    if (item.target) onNavigate(item.target);
    else if (item.canvas) onCanvas(item.canvas);
    else if (item.action === "project") onOpenProject();
    pushRecent(item.id);
    onClose();
  };

  const ask = () => {
    if (!askQuery) return;
    onAskForge(askQuery);
    onClose();
  };

  return (
    <FxOverlay open={open} onOpenChange={(next) => { if (!next) onClose(); }} title="Command Center" size="md">
      <Command shouldFilter={false} loop className="flex min-h-0 flex-col outline-none">
        <div className="flex items-center gap-3 border-b border-[var(--fx-border-soft)] px-4 py-3">
          <Search className="h-4 w-4 shrink-0 text-[var(--fx-text-muted)]" />
          <Command.Input
            value={query}
            onValueChange={setQuery}
            placeholder="Search commands or type > to ask Forge…"
            className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--fx-text)] outline-none placeholder:text-[color-mix(in_srgb,var(--fx-text-muted)_72%,transparent)]"
          />
          <Kbd>esc</Kbd>
        </div>
        <Command.List className="max-h-[430px] min-h-[120px] overflow-y-auto overscroll-contain p-2">
          <Command.Empty className="px-3 py-8 text-center text-[10px] text-[var(--fx-text-muted)]">
            {forcedAsk && !askQuery ? "Type your question for Forge…" : "No matching commands."}
          </Command.Empty>

          {!searching && recentItems.length ? (
            <Command.Group heading="Recent" className="[&>[cmdk-group-heading]]:px-3 [&>[cmdk-group-heading]]:pb-1.5 [&>[cmdk-group-heading]]:pt-1 [&>[cmdk-group-heading]]:text-[9px] [&>[cmdk-group-heading]]:font-semibold [&>[cmdk-group-heading]]:uppercase [&>[cmdk-group-heading]]:tracking-[.16em] [&>[cmdk-group-heading]]:text-[var(--fx-text-muted)]">
              {recentItems.map((item) => (
                <Command.Item key={`recent-${item.id}`} value={`recent-${item.id}`} onSelect={() => execute(item)} className="group flex w-full cursor-pointer items-center gap-3 rounded-xl p-3 text-left outline-none data-[selected=true]:bg-[var(--fx-hover)]">
                  <ItemRow item={item} />
                </Command.Item>
              ))}
            </Command.Group>
          ) : null}

          {grouped.map(({ group, items }) => (
            <Command.Group key={group} heading={group} className="mb-1 [&>[cmdk-group-heading]]:px-3 [&>[cmdk-group-heading]]:pb-1.5 [&>[cmdk-group-heading]]:pt-1 [&>[cmdk-group-heading]]:text-[9px] [&>[cmdk-group-heading]]:font-semibold [&>[cmdk-group-heading]]:uppercase [&>[cmdk-group-heading]]:tracking-[.16em] [&>[cmdk-group-heading]]:text-[var(--fx-text-muted)]">
              {items.map((item) => (
                <Command.Item key={item.id} value={item.id} onSelect={() => execute(item)} className="group flex w-full cursor-pointer items-center gap-3 rounded-xl p-3 text-left outline-none data-[selected=true]:bg-[var(--fx-hover)]">
                  <ItemRow item={item} />
                </Command.Item>
              ))}
            </Command.Group>
          ))}

          {askQuery ? (
            <Command.Group heading="Ask Forge" className="[&>[cmdk-group-heading]]:px-3 [&>[cmdk-group-heading]]:pb-1.5 [&>[cmdk-group-heading]]:pt-1 [&>[cmdk-group-heading]]:text-[9px] [&>[cmdk-group-heading]]:font-semibold [&>[cmdk-group-heading]]:uppercase [&>[cmdk-group-heading]]:tracking-[.16em] [&>[cmdk-group-heading]]:text-[var(--fx-text-muted)]">
              <Command.Item value="ask-forge" onSelect={ask} className="group flex w-full cursor-pointer items-center gap-3 rounded-xl border border-[color-mix(in_srgb,var(--fx-accent)_28%,var(--fx-border))] bg-[var(--fx-accent-faint)] p-3 text-left outline-none data-[selected=true]:bg-[color-mix(in_srgb,var(--fx-accent)_14%,transparent)]">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[var(--fx-accent-soft)] text-[var(--fx-accent)]"><Sparkles className="h-4 w-4" /></span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[11px] font-semibold text-[var(--fx-text)]">Ask Forge</span>
                  <span className="block truncate text-[9px] text-[var(--fx-text-muted)]">{askQuery}</span>
                </span>
                <Kbd>↵</Kbd>
              </Command.Item>
            </Command.Group>
          ) : null}
        </Command.List>
        <div className="flex shrink-0 items-center justify-between border-t border-[var(--fx-border-soft)] px-4 py-2 text-[8px] uppercase tracking-[.13em] text-[var(--fx-text-muted)]">
          <span className="flex items-center gap-2"><History className="h-3 w-3" /> Recents are remembered locally</span>
          <span className="flex items-center gap-1.5"><Kbd>↑</Kbd><Kbd>↓</Kbd> navigate · <Kbd>↵</Kbd> select · <Kbd>&gt;</Kbd> ask</span>
        </div>
      </Command>
    </FxOverlay>
  );
}
