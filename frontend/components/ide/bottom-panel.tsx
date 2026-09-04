"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import {
  AlertTriangle,
  ArrowDown,
  Bug,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  CircleAlert,
  Copy,
  Info,
  Maximize2,
  Minimize2,
  Radio,
  Search,
  SquareTerminal,
  Trash2,
  TriangleAlert,
  WrapText,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";

import { ForgeXTerminal } from "@/components/terminal/forgex-terminal";
import { useForgeXSettings } from "@/hooks/use-forgex-settings";
import type { ConsoleEntry, ProjectResponse, SerialMonitorEvent, WorkspaceLog, WorkflowStage } from "@/types";

type BottomTab = "problems" | "terminal" | "debug" | "serial";
type Severity = "error" | "warning" | "info";

export interface ProblemItem {
  severity: Severity;
  message: string;
  file?: string;
  line?: number;
  time?: string;
}

interface BottomPanelProps {
  logs: ConsoleEntry[];
  workspaceLogs: WorkspaceLog[];
  serialEvents?: SerialMonitorEvent[];
  stages: WorkflowStage[];
  activeProject: ProjectResponse | null;
  height: number;
  collapsed: boolean;
  maximized: boolean;
  onToggleCollapsed: () => void;
  onToggleMaximized: () => void;
  onClearLogs: () => void;
  problems?: ProblemItem[];
  onOpenFile?: (path: string, line?: number) => void;
  activeTerminalId?: string;
  defaultTab?: BottomTab;
}

interface AnsiSpan {
  text: string;
  color?: string;
  background?: string;
  bold?: boolean;
  italic?: boolean;
}

interface OutputLine {
  key: string;
  time?: string;
  text: string;
  spans: AnsiSpan[];
  tone?: Severity;
}

interface OutputListProps {
  lines: OutputLine[];
  wrap: boolean;
  onRowContextMenu: (event: ReactMouseEvent, text: string) => void;
}

interface ProblemGroup {
  file: string;
  items: ProblemItem[];
}

interface ContextMenuState {
  x: number;
  y: number;
  text: string;
}

const HEADER_HEIGHT = 36;
const ROW_HEIGHT = 20;
const ROW_OVERSCAN = 10;
const STICK_THRESHOLD = 24;

const SEVERITY_ICONS: Record<Severity, LucideIcon> = { error: CircleAlert, warning: TriangleAlert, info: Info };
const SEVERITY_COLORS: Record<Severity, string> = {
  error: "var(--fx-error)",
  warning: "var(--fx-warning)",
  info: "var(--fx-info)",
};

const ANSI_PATTERN = /\x1b\[([0-9;]*)m|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b\[[0-9:;<=>?]*[ -/]*[@-~]|\x1b[\s\S]/g;

const ANSI_FG: Record<number, string> = {
  30: "var(--fx-text-muted)",
  31: "var(--fx-error)",
  32: "var(--fx-success)",
  33: "var(--fx-warning)",
  34: "var(--fx-info)",
  35: "var(--fx-accent)",
  36: "var(--fx-info)",
  37: "var(--fx-text)",
};

const ANSI_BG: Record<number, string> = {
  41: "var(--fx-error-soft)",
  42: "var(--fx-success-soft)",
  43: "var(--fx-warning-soft)",
  44: "var(--fx-accent-soft)",
  45: "var(--fx-accent-soft)",
  46: "var(--fx-accent-soft)",
  47: "var(--fx-hover)",
};

function parseAnsi(input: string): AnsiSpan[] {
  if (!input.includes("\x1b")) return [{ text: input }];
  const spans: AnsiSpan[] = [];
  let color: string | undefined;
  let background: string | undefined;
  let bold = false;
  let italic = false;
  const push = (text: string) => {
    if (text) spans.push({ text, color, background, bold, italic });
  };
  let last = 0;
  for (const match of input.matchAll(ANSI_PATTERN)) {
    const index = match.index ?? 0;
    push(input.slice(last, index));
    if (match[1] !== undefined) {
      for (const raw of match[1].split(";")) {
        const code = raw === "" ? 0 : Number.parseInt(raw, 10);
        if (Number.isNaN(code)) continue;
        if (code === 0) {
          color = undefined;
          background = undefined;
          bold = false;
          italic = false;
        } else if (code === 1) {
          bold = true;
        } else if (code === 3) {
          italic = true;
        } else if (code === 22) {
          bold = false;
        } else if (code === 23) {
          italic = false;
        } else if (code >= 30 && code <= 37) {
          color = ANSI_FG[code];
        } else if (code >= 90 && code <= 97) {
          color = ANSI_FG[code - 60];
        } else if (code >= 40 && code <= 47) {
          background = ANSI_BG[code];
        } else if (code >= 100 && code <= 107) {
          const token = ANSI_BG[code - 59];
          if (token) background = token;
        }
      }
    }
    last = index + match[0].length;
  }
  push(input.slice(last));
  return spans.length > 0 ? spans : [{ text: "" }];
}

function renderAnsiSpans(spans: AnsiSpan[]) {
  return spans.map((span, index) => (
    <span
      key={index}
      style={{
        color: span.color,
        background: span.background,
        fontWeight: span.bold ? 600 : undefined,
        fontStyle: span.italic ? "italic" : undefined,
      }}
    >
      {span.text}
    </span>
  ));
}

function time(value: string) {
  try {
    return new Date(value).toLocaleTimeString();
  } catch {
    return "--:--:--";
  }
}

function baseName(path: string) {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

function resolveTabValue(value: unknown): BottomTab | null {
  if (typeof value !== "string") return null;
  switch (value.trim().toLowerCase()) {
    case "problems":
      return "problems";
    case "debug":
    case "debugconsole":
    case "debug console":
      return "debug";
    case "terminal":
      return "terminal";
    case "serial":
    case "serialmonitor":
    case "serial monitor":
      return "serial";
    default:
      return null;
  }
}

function OutputList({ lines, wrap, onRowContextMenu }: OutputListProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [atBottom, setAtBottom] = useState(true);

  const virtualizer = useVirtualizer({
    count: lines.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: ROW_OVERSCAN,
  });

  useEffect(() => {
    if (!atBottom) return;
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [atBottom, lines.length]);

  const handleScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    setAtBottom(element.scrollHeight - element.scrollTop - element.clientHeight < STICK_THRESHOLD);
  };

  const jumpToLatest = () => {
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
    setAtBottom(true);
  };

  return (
    <div className="relative min-h-0" role="tabpanel">
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className={`h-full overflow-auto px-3 font-mono leading-5 text-[var(--fx-terminal-text)] ${wrap ? "" : "whitespace-pre"}`}
        style={{ height: "100%", fontSize: "max(12px, var(--fx-terminal-font-size))" }}
      >
        {lines.length === 0 ? (
          <div className="py-2 text-[var(--fx-text-muted)]">No matching lines.</div>
        ) : (
          <div className="relative" style={{ height: virtualizer.getTotalSize() }}>
            {virtualizer.getVirtualItems().map((item) => {
              const line = lines[item.index];
              if (!line) return null;
              return (
                <div
                  key={line.key}
                  data-index={item.index}
                  ref={virtualizer.measureElement}
                  className={`absolute left-0 top-0 ${wrap ? "w-full whitespace-pre-wrap break-words" : "w-max min-w-full"}`}
                  style={{
                    transform: `translateY(${item.start}px)`,
                    minHeight: ROW_HEIGHT,
                    color: line.tone ? SEVERITY_COLORS[line.tone] : undefined,
                  }}
                  onContextMenu={(event) => onRowContextMenu(event, line.time ? `[${line.time}] ${line.text}` : line.text)}
                >
                  {line.time ? <span className="mr-2 text-[var(--fx-text-muted)]">[{line.time}]</span> : null}
                  {renderAnsiSpans(line.spans)}
                </div>
              );
            })}
          </div>
        )}
      </div>
      {!atBottom && lines.length > 0 ? (
        <button
          className="absolute bottom-3 right-4 flex items-center gap-1 rounded-full border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-2.5 py-1 text-[11px] text-[var(--fx-text-muted)] shadow-sm hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
          onClick={jumpToLatest}
          title="Jump to latest"
        >
          <ArrowDown className="h-3 w-3" />
          Latest
        </button>
      ) : null}
    </div>
  );
}

const tabs = [
  { id: "problems" as const, label: "Problems", icon: AlertTriangle },
  { id: "terminal" as const, label: "Terminal", icon: SquareTerminal },
  { id: "debug" as const, label: "Debug Console", icon: Bug },
  { id: "serial" as const, label: "Serial Monitor", icon: Radio },
];

export function BottomPanel({
  logs,
  workspaceLogs,
  serialEvents = [],
  stages,
  activeProject,
  height,
  collapsed,
  maximized,
  onToggleCollapsed,
  onToggleMaximized,
  onClearLogs,
  problems: structuredProblems,
  onOpenFile,
  activeTerminalId,
  defaultTab,
}: BottomPanelProps) {
  const { settings } = useForgeXSettings();

  const [active, setActive] = useState<BottomTab>(() => resolveTabValue(defaultTab) ?? "terminal");
  const tabTouchedRef = useRef(false);
  const [filters, setFilters] = useState<Partial<Record<BottomTab, string>>>({});
  const [wrap, setWrap] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const [menu, setMenu] = useState<ContextMenuState | null>(null);

  const settingDefaultTab = resolveTabValue(settings["terminal.default_tab"]);
  useEffect(() => {
    if (defaultTab || tabTouchedRef.current || !settingDefaultTab) return;
    setActive(settingDefaultTab);
  }, [defaultTab, settingDefaultTab]);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", close);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", close);
      window.removeEventListener("blur", close);
    };
  }, [menu]);

  const showTimestamps = settings["terminal.show_timestamps"] !== false;

  const legacyProblems = useMemo<ProblemItem[]>(
    () => logs.filter((log) => log.channel === "error").map((log) => ({ severity: "error", message: log.message, time: time(log.timestamp) })),
    [logs],
  );
  const problemItems = structuredProblems ?? legacyProblems;
  const structuredMode = structuredProblems !== undefined;

  const problemGroups = useMemo<ProblemGroup[]>(() => {
    const map = new Map<string, ProblemItem[]>();
    for (const item of problemItems) {
      const key = item.file ?? "(no file)";
      const list = map.get(key) ?? [];
      list.push(item);
      map.set(key, list);
    }
    return [...map.entries()].map(([file, items]) => ({ file, items }));
  }, [problemItems]);

  const problemCounts = useMemo(() => {
    let errors = 0;
    let warnings = 0;
    for (const item of problemItems) {
      if (item.severity === "error") errors += 1;
      else if (item.severity === "warning") warnings += 1;
    }
    return { total: problemItems.length, errors, warnings };
  }, [problemItems]);

  const serialLogs = logs.filter((log) => log.channel === "serial");
  const workspaceSerial = workspaceLogs.filter((log) => log.log_type === "monitor");

  const outputLines = useMemo<OutputLine[]>(() => {
    const nowLabel = new Date().toLocaleTimeString();
    const make = (key: string, text: string, tone?: Severity, stamp?: string): OutputLine => ({
      key,
      time: showTimestamps ? stamp ?? nowLabel : undefined,
      text,
      spans: parseAnsi(text),
      tone,
    });

    if (active === "problems") {
      const lines = legacyProblems.map((problem, index) => make(`p-${index}`, problem.message, "error", problem.time));
      return lines.length > 0 ? lines : [make("p-empty", "No problems detected.")];
    }
    if (active === "debug") {
      return stages.map((stage, index) =>
        make(`d-${index}`, `${stage.label.padEnd(12)} ${stage.status} - ${stage.description}`),
      );
    }
    if (active === "serial") {
      const lines = [
        ...serialEvents.map((event) => make(
          `live-${event.sequence}`,
          event.line,
          event.source === "OVERFLOW" ? "warning" : undefined,
          undefined,
        )),
        ...serialLogs.map((log, index) => make(`s-${index}`, log.message, undefined, time(log.timestamp))),
        ...workspaceSerial
          .map((log, index) => make(`w-${index}`, `[${log.execution_id}] ${log.content.trim()}`))
          .filter((line) => line.text !== ""),
      ];
      return lines.length > 0 ? lines : [make("s-empty", "Serial monitor idle.")];
    }
    const lines = logs.map((log, index) =>
      make(`o-${index}`, `[${log.channel}] ${log.message}`, log.channel === "error" ? "error" : undefined, time(log.timestamp)),
    );
    return lines.length > 0 ? lines : [make("o-empty", "ForgeX output ready.")];
  }, [active, logs, legacyProblems, serialEvents, serialLogs, stages, workspaceSerial, showTimestamps]);

  const query = (filters[active] ?? "").trim().toLowerCase();
  const visibleLines = useMemo(() => {
    if (!query) return outputLines;
    return outputLines.filter((line) => line.text.toLowerCase().includes(query) || (line.time ?? "").includes(query));
  }, [outputLines, query]);

  const copyText = (value: string) => {
    void navigator.clipboard?.writeText(value).catch(() => null);
  };

  const openMenu = (event: ReactMouseEvent, text: string) => {
    event.preventDefault();
    setMenu({
      x: Math.min(event.clientX, window.innerWidth - 180),
      y: Math.min(event.clientY, window.innerHeight - 90),
      text,
    });
  };

  const bodyHeight = Math.max(0, height - HEADER_HEIGHT);
  const isOutputTab = active === "problems" || active === "debug" || active === "serial";
  const showStructuredProblems = active === "problems" && structuredMode;

  return (
    <section
      className="fx-bottom-panel min-h-0 min-w-0 shrink-0 overflow-hidden border-t border-[var(--fx-border)] bg-[var(--fx-bg)] transition-[height] duration-200 ease-out"
      style={{ height: collapsed ? HEADER_HEIGHT : height }}
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
                onClick={() => {
                  tabTouchedRef.current = true;
                  setActive(tab.id);
                }}
                role="tab"
                aria-selected={selected}
              >
                <Icon className="h-3 w-3" />
                {tab.label}
                {tab.id === "problems" && problemCounts.total > 0 ? (
                  <span
                    className={`rounded-full px-1.5 text-[10px] font-semibold normal-case tracking-normal ${
                      problemCounts.errors > 0
                        ? "bg-[var(--fx-error-soft)] text-[var(--fx-error)]"
                        : problemCounts.warnings > 0
                          ? "bg-[var(--fx-warning-soft)] text-[var(--fx-warning)]"
                          : "bg-[var(--fx-hover)] text-[var(--fx-text-muted)]"
                    }`}
                  >
                    {problemCounts.total}
                  </span>
                ) : null}
                {selected ? <span className="absolute inset-x-2 bottom-0 h-px bg-[var(--fx-accent)]" /> : null}
              </button>
            );
          })}
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          {isOutputTab && !showStructuredProblems ? (
            <>
              <label className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2">
                <Search className="h-3 w-3 text-[var(--fx-text-muted)]" />
                <input
                  className="w-32 bg-transparent text-xs text-[var(--fx-text)] outline-none placeholder:text-[var(--fx-text-muted)]"
                  value={filters[active] ?? ""}
                  onChange={(event) => setFilters((current) => ({ ...current, [active]: event.target.value }))}
                  placeholder="Filter"
                  aria-label={`Filter ${active} output`}
                />
                {filters[active] ? (
                  <>
                    <span className="shrink-0 text-[10px] tabular-nums text-[var(--fx-text-muted)]">
                      {visibleLines.length}/{outputLines.length}
                    </span>
                    <button
                      className="text-[var(--fx-text-muted)] hover:text-[var(--fx-text)]"
                      onClick={() => setFilters((current) => ({ ...current, [active]: "" }))}
                      aria-label="Clear filter"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </>
                ) : null}
              </label>
              <button
                className={`flex h-7 w-7 items-center justify-center rounded ${
                  wrap ? "bg-[var(--fx-accent-soft)] text-[var(--fx-accent)]" : "text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
                }`}
                onClick={() => setWrap((current) => !current)}
                title={wrap ? "Disable word wrap" : "Enable word wrap"}
                aria-label={wrap ? "Disable word wrap" : "Enable word wrap"}
                aria-pressed={wrap}
              >
                <WrapText className="h-3.5 w-3.5" />
              </button>
              <button
                className="flex items-center gap-1 rounded px-2 py-1 text-xs text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
                onClick={() => copyText(visibleLines.map((line) => (line.time ? `[${line.time}] ${line.text}` : line.text)).join("\n"))}
                title="Copy all visible lines"
              >
                <Copy className="h-3.5 w-3.5" />
                Copy All
              </button>
            </>
          ) : null}
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
        <div
          className="min-h-0"
          style={{ height: bodyHeight }}
          role="tabpanel"
          data-active-terminal-id={activeTerminalId}
          aria-label={activeTerminalId ? `Terminal session ${activeTerminalId}` : "Terminal sessions"}
        >
          <ForgeXTerminal activeProject={activeProject} />
        </div>
      ) : showStructuredProblems ? (
        <div
          className="overflow-auto px-2 py-1.5 font-mono"
          style={{ height: bodyHeight, fontSize: "max(12px, var(--fx-terminal-font-size))" }}
          role="tabpanel"
        >
          {problemGroups.length === 0 ? (
            <div className="px-1 py-1 text-[var(--fx-text-muted)]">No problems detected.</div>
          ) : (
            problemGroups.map((group) => {
              const open = !collapsedGroups[group.file];
              return (
                <div key={group.file}>
                  <button
                    className="flex w-full items-center gap-1.5 rounded px-1 py-1 text-left text-xs hover:bg-[var(--fx-hover)]"
                    onClick={() => setCollapsedGroups((current) => ({ ...current, [group.file]: !current[group.file] }))}
                    aria-expanded={open}
                  >
                    {open ? (
                      <ChevronDown className="h-3 w-3 shrink-0 text-[var(--fx-text-muted)]" />
                    ) : (
                      <ChevronRight className="h-3 w-3 shrink-0 text-[var(--fx-text-muted)]" />
                    )}
                    <span className="truncate text-[var(--fx-text)]" title={group.file}>
                      {baseName(group.file)}
                    </span>
                    <span className="shrink-0 rounded-full bg-[var(--fx-hover)] px-1.5 text-[10px] text-[var(--fx-text-muted)]">
                      {group.items.length}
                    </span>
                  </button>
                  {open
                    ? group.items.map((item, index) => {
                        const Icon = SEVERITY_ICONS[item.severity];
                        return (
                          <div key={`${group.file}-${index}`} className="flex items-center gap-2 py-0.5 pl-6 pr-2 leading-5">
                            <Icon className="h-3 w-3 shrink-0" style={{ color: SEVERITY_COLORS[item.severity] }} />
                            <span className="min-w-0 flex-1 truncate text-[var(--fx-code-text)]" title={item.message}>
                              {item.message}
                            </span>
                            {item.time ? (
                              <span className="shrink-0 text-[11px] text-[var(--fx-text-muted)]">{item.time}</span>
                            ) : null}
                            {onOpenFile && item.file ? (
                              <button
                                className="shrink-0 text-[11px] text-[var(--fx-text-muted)] underline-offset-2 hover:text-[var(--fx-accent)] hover:underline"
                                onClick={() => onOpenFile(item.file ?? "", item.line)}
                                title={`Open ${item.file}${item.line != null ? `:${item.line}` : ""}`}
                              >
                                {baseName(item.file)}
                                {item.line != null ? `:${item.line}` : ""}
                              </button>
                            ) : item.file ? (
                              <span className="shrink-0 text-[11px] text-[var(--fx-text-muted)]">
                                {baseName(item.file)}
                                {item.line != null ? `:${item.line}` : ""}
                              </span>
                            ) : null}
                          </div>
                        );
                      })
                    : null}
                </div>
              );
            })
          )}
        </div>
      ) : (
        <div style={{ height: bodyHeight }}>
          <OutputList key={active} lines={visibleLines} wrap={wrap} onRowContextMenu={openMenu} />
        </div>
      )}
      {menu ? (
        <div
          className="fixed z-50 min-w-[150px] overflow-hidden rounded-md border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] py-1 shadow-lg"
          style={{ left: menu.x, top: menu.y }}
          onPointerDown={(event) => event.stopPropagation()}
          role="menu"
        >
          <button
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-[var(--fx-text)] hover:bg-[var(--fx-hover)]"
            onClick={() => {
              copyText(menu.text);
              setMenu(null);
            }}
            role="menuitem"
          >
            <Copy className="h-3 w-3" />
            Copy line
          </button>
          <button
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-[var(--fx-text)] hover:bg-[var(--fx-hover)]"
            onClick={() => {
              copyText(visibleLines.map((line) => (line.time ? `[${line.time}] ${line.text}` : line.text)).join("\n"));
              setMenu(null);
            }}
            role="menuitem"
          >
            <Copy className="h-3 w-3" />
            Copy all
          </button>
        </div>
      ) : null}
    </section>
  );
}
