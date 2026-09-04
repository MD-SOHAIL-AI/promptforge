"use client";

import {
  ChevronDown,
  ChevronUp,
  CircleAlert,
  Eraser,
  Pencil,
  Plus,
  RotateCw,
  Search,
  SquareTerminal,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { useForgeXSettings } from "@/hooks/use-forgex-settings";
import { useTerminalSession } from "@/components/terminal/use-terminal-session";
import { useForgeXThemeVersion } from "@/lib/theme-context";
import { promptForgeApi } from "@/lib/api";
import type {
  ForgeXSettingValue,
  ProjectResponse,
  TerminalProfileResponse,
} from "@/types";

interface ForgeXTerminalProps {
  activeProject: ProjectResponse | null;
}

interface TerminalTab {
  id: string;
  title: string;
  profileId?: string;
  restartToken: number;
  clearToken: number;
}

interface TabExitStatus {
  closed: boolean;
  exitCode: number | null;
}

export function ForgeXTerminal({ activeProject }: ForgeXTerminalProps) {
  const { settings } = useForgeXSettings();
  const [profiles, setProfiles] = useState<TerminalProfileResponse[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [profilesLoaded, setProfilesLoaded] = useState(false);
  const [tabs, setTabs] = useState<TerminalTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [renamingTabId, setRenamingTabId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [exitStatuses, setExitStatuses] = useState<Record<string, TabExitStatus>>({});
  const nextIdRef = useRef(0);
  const defaultProfileIdRef = useRef<string | undefined>(undefined);
  const profilesRef = useRef<TerminalProfileResponse[]>([]);

  const scrollback = clampNumberSetting(
    settings["terminal.scrollback_lines"],
    5_000,
    200,
    20_000,
  );
  const fontSizeSetting = clampNumberSetting(settings["terminal.font_size"], null, 11, 22);
  const showTimestamps =
    settings["terminal.show_timestamps"] === true ||
    settings["terminal.show_timestamps"] === "true";

  const handleExitStatus = useCallback((tabId: string, status: TabExitStatus) => {
    setExitStatuses((current) => {
      const previous = current[tabId];
      if (
        previous &&
        previous.closed === status.closed &&
        previous.exitCode === status.exitCode
      ) {
        return current;
      }
      return { ...current, [tabId]: status };
    });
  }, []);

  const createTab = useCallback((profileId?: string) => {
    const sequence = ++nextIdRef.current;
    const profile = profilesRef.current.find((item) => item.profile_id === profileId);
    const baseTitle = profile?.name ?? "Terminal";
    return {
      id: `terminal-tab-${sequence}`,
      title: sequence === 1 ? baseTitle : `${baseTitle} ${sequence}`,
      profileId,
      restartToken: 0,
      clearToken: 0,
    } satisfies TerminalTab;
  }, []);

  useEffect(() => {
    let disposed = false;
    void promptForgeApi.terminalProfiles()
      .then((response) => {
        if (disposed) return;
        const available = response.profiles.filter((profile) => profile.available);
        profilesRef.current = available;
        setProfiles(available);
        defaultProfileIdRef.current = response.default_profile_id;
        setSelectedProfileId(response.default_profile_id);
        setProfilesLoaded(true);
      })
      .catch(() => {
        if (!disposed) {
          setProfiles([]);
          setProfilesLoaded(true);
        }
      });
    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    setRenamingTabId(null);
    setExitStatuses({});
    if (!activeProject?.project_id || !profilesLoaded) {
      setTabs([]);
      setActiveTabId(null);
      return;
    }
    const initial = createTab(defaultProfileIdRef.current);
    setTabs([initial]);
    setActiveTabId(initial.id);
  }, [activeProject?.project_id, createTab, profilesLoaded]);

  const addTab = useCallback(() => {
    const next = createTab(selectedProfileId || defaultProfileIdRef.current);
    setTabs((current) => [...current, next]);
    setActiveTabId(next.id);
  }, [createTab, selectedProfileId]);

  const closeTab = useCallback((tabId: string) => {
    const index = tabs.findIndex((tab) => tab.id === tabId);
    const remaining = tabs.filter((tab) => tab.id !== tabId);
    setTabs(remaining);
    setExitStatuses((current) => {
      if (!(tabId in current)) return current;
      const next = { ...current };
      delete next[tabId];
      return next;
    });
    if (activeTabId === tabId) {
      const fallback = remaining[Math.min(Math.max(index, 0), remaining.length - 1)];
      setActiveTabId(fallback?.id ?? null);
    }
    if (renamingTabId === tabId) setRenamingTabId(null);
  }, [activeTabId, renamingTabId, tabs]);

  const updateTab = useCallback((tabId: string, update: Partial<TerminalTab>) => {
    setTabs((current) => current.map((tab) => (
      tab.id === tabId ? { ...tab, ...update } : tab
    )));
  }, []);

  const beginRename = useCallback((tab: TerminalTab) => {
    setActiveTabId(tab.id);
    setRenamingTabId(tab.id);
    setRenameValue(tab.title);
  }, []);

  const commitRename = useCallback(() => {
    const value = renameValue.trim().slice(0, 80);
    if (renamingTabId && value) updateTab(renamingTabId, { title: value });
    setRenamingTabId(null);
  }, [renameValue, renamingTabId, updateTab]);

  const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? null;

  if (!activeProject) {
    return (
      <div className="flex h-full items-center justify-center bg-[var(--fx-bg)] text-sm text-[var(--fx-text-muted)]">
        Open a workspace folder to start a terminal.
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--fx-bg)] text-[var(--fx-terminal-text)]">
      <div className="flex h-9 shrink-0 items-center border-b border-[var(--fx-border-soft)] bg-[var(--fx-panel)]">
        <div className="flex h-full min-w-0 flex-1 overflow-x-auto" role="tablist" aria-label="Terminal sessions">
          {tabs.map((tab) => {
            const selected = tab.id === activeTabId;
            const exit = exitStatuses[tab.id];
            return (
              <div
                key={tab.id}
                className={`group relative flex h-full max-w-52 shrink-0 items-center border-r border-[var(--fx-border-soft)] ${
                  selected
                    ? "bg-[var(--fx-bg)] text-[var(--fx-text)]"
                    : "text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
                }`}
              >
                {renamingTabId === tab.id ? (
                  <input
                    autoFocus
                    className="mx-1 h-6 w-32 rounded border border-[var(--fx-accent)] bg-[var(--fx-panel-elevated)] px-1.5 text-xs text-[var(--fx-text)] outline-none"
                    maxLength={80}
                    value={renameValue}
                    onBlur={commitRename}
                    onChange={(event) => setRenameValue(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") commitRename();
                      if (event.key === "Escape") setRenamingTabId(null);
                    }}
                    aria-label="Terminal name"
                  />
                ) : (
                  <button
                    className="flex h-full min-w-0 items-center gap-1.5 pl-2.5 pr-1 text-xs"
                    onClick={() => setActiveTabId(tab.id)}
                    onDoubleClick={() => beginRename(tab)}
                    role="tab"
                    aria-selected={selected}
                    title={`${tab.title} — double-click to rename`}
                  >
                    <SquareTerminal className="h-3.5 w-3.5 shrink-0 text-[var(--fx-accent)]" />
                    <span className="truncate">{tab.title}</span>
                  </button>
                )}
                {exit?.closed ? (
                  exit.exitCode === 0 ? (
                    <span
                      className="mr-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--fx-success)]"
                      title="Exited (0)"
                    />
                  ) : (
                    <span
                      className="mr-0.5 flex shrink-0 items-center gap-1 text-[10px] font-medium text-[var(--fx-error)]"
                      title={`Exit code ${exit.exitCode ?? "unknown"}`}
                    >
                      <span className="h-1.5 w-1.5 rounded-full bg-[var(--fx-error)]" />
                      {exit.exitCode ?? "?"}
                    </span>
                  )
                ) : null}
                <button
                  className={`mr-1 flex h-5 w-5 shrink-0 items-center justify-center rounded hover:bg-[var(--fx-hover)] ${
                    selected ? "opacity-80 hover:opacity-100" : "opacity-50 hover:opacity-100 group-hover:opacity-100"
                  }`}
                  onClick={() => closeTab(tab.id)}
                  title={`Close ${tab.title}`}
                  aria-label={`Close ${tab.title}`}
                >
                  <X className="h-3 w-3" />
                </button>
                {selected ? <span className="absolute inset-x-0 bottom-0 h-px bg-[var(--fx-accent)]" /> : null}
              </div>
            );
          })}
        </div>
        <div className="flex h-full shrink-0 items-center gap-0.5 border-l border-[var(--fx-border-soft)] px-1">
          <select
            className="h-6 max-w-36 rounded border border-[var(--fx-border-soft)] bg-[var(--fx-bg)] px-1 text-[10px] text-[var(--fx-text-muted)] outline-none focus:border-[var(--fx-accent)]"
            value={selectedProfileId}
            onChange={(event) => setSelectedProfileId(event.target.value)}
            title="Profile for new terminal"
            aria-label="Profile for new terminal"
          >
            {profiles.length === 0 ? <option value="">Default shell</option> : null}
            {profiles.map((profile) => (
              <option key={profile.profile_id} value={profile.profile_id}>{profile.name}</option>
            ))}
          </select>
          <TerminalToolbarButton label="New terminal" onClick={addTab}>
            <Plus className="h-3.5 w-3.5" />
          </TerminalToolbarButton>
          <TerminalToolbarButton
            disabled={!activeTab}
            label="Rename terminal"
            onClick={() => activeTab && beginRename(activeTab)}
          >
            <Pencil className="h-3.5 w-3.5" />
          </TerminalToolbarButton>
          <TerminalToolbarButton
            disabled={!activeTab}
            label="Restart terminal"
            onClick={() => activeTab && updateTab(activeTab.id, { restartToken: activeTab.restartToken + 1 })}
          >
            <RotateCw className="h-3.5 w-3.5" />
          </TerminalToolbarButton>
          <TerminalToolbarButton
            disabled={!activeTab}
            label="Clear terminal"
            onClick={() => activeTab && updateTab(activeTab.id, { clearToken: activeTab.clearToken + 1 })}
          >
            <Eraser className="h-3.5 w-3.5" />
          </TerminalToolbarButton>
        </div>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden">
        {tabs.length === 0 ? (
          <button
            className="absolute inset-0 m-auto flex h-9 w-fit items-center gap-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-3 text-xs text-[var(--fx-text)] hover:bg-[var(--fx-hover)]"
            onClick={addTab}
          >
            <Plus className="h-3.5 w-3.5" />
            New terminal
          </button>
        ) : null}
        {tabs.map((tab) => (
          <TerminalTabPane
            key={tab.id}
            active={tab.id === activeTabId}
            activeProject={activeProject}
            profileId={tab.profileId}
            title={tab.title}
            restartToken={tab.restartToken}
            clearToken={tab.clearToken}
            scrollback={scrollback}
            fontSize={fontSizeSetting}
            showTimestamps={showTimestamps}
            onExitStatus={(status) => handleExitStatus(tab.id, status)}
          />
        ))}
      </div>
    </div>
  );
}

function TerminalToolbarButton({
  children,
  disabled = false,
  label,
  onClick,
}: {
  children: React.ReactNode;
  disabled?: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      className="flex h-7 w-7 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:cursor-not-allowed disabled:opacity-30"
      disabled={disabled}
      onClick={onClick}
      title={label}
      aria-label={label}
    >
      {children}
    </button>
  );
}

function TerminalTabPane({
  active,
  activeProject,
  profileId,
  title,
  restartToken,
  clearToken,
  scrollback,
  fontSize,
  showTimestamps,
  onExitStatus,
}: {
  active: boolean;
  activeProject: ProjectResponse;
  profileId?: string;
  title: string;
  restartToken: number;
  clearToken: number;
  scrollback: number;
  fontSize: number | null;
  showTimestamps: boolean;
  onExitStatus: (status: TabExitStatus) => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<import("@xterm/xterm").Terminal | null>(null);
  const fitRef = useRef<import("@xterm/addon-fit").FitAddon | null>(null);
  const webglRef = useRef<import("@xterm/addon-webgl").WebglAddon | null>(null);
  const linksRef = useRef<import("@xterm/addon-web-links").WebLinksAddon | null>(null);
  const searchRef = useRef<import("@xterm/addon-search").SearchAddon | null>(null);
  const pendingDataRef = useRef("");
  const frameRef = useRef<number | null>(null);
  const previousTitleRef = useRef(title);
  const previousRestartRef = useRef(restartToken);
  const previousClearRef = useRef(clearToken);
  const atLineStartRef = useRef(true);
  const showTimestampsRef = useRef(showTimestamps);
  const fontSizeRef = useRef(fontSize);
  const [ready, setReady] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchMatches, setSearchMatches] = useState<{ index: number; count: number } | null>(null);

  const themeVersion = useForgeXThemeVersion();

  const stampChunk = useCallback((data: string) => {
    if (!showTimestampsRef.current || !data) return data;
    const now = new Date();
    const stamp = `\x1b[90m${
      [
        now.getHours(),
        now.getMinutes(),
        now.getSeconds(),
      ].map((unit) => String(unit).padStart(2, "0")).join(":")
    }\x1b[0m `;
    const segments = data.split(/(\r?\n)/);
    let output = "";
    for (const segment of segments) {
      if (!segment) continue;
      if (segment === "\n" || segment === "\r\n") {
        output += segment;
        atLineStartRef.current = true;
        continue;
      }
      if (atLineStartRef.current) output += stamp;
      output += segment;
      atLineStartRef.current = false;
    }
    return output;
  }, []);

  const flushPendingData = useCallback(() => {
    frameRef.current = null;
    const terminal = terminalRef.current;
    if (!terminal || !pendingDataRef.current) return;
    const data = pendingDataRef.current;
    pendingDataRef.current = "";
    terminal.write(data);
  }, []);

  const writeToTerminal = useCallback((data: string, meta?: { replay: boolean }) => {
    pendingDataRef.current += meta?.replay ? data : stampChunk(data);
    if (frameRef.current === null) {
      frameRef.current = window.requestAnimationFrame(flushPendingData);
    }
  }, [flushPendingData, stampChunk]);

  const resetTerminal = useCallback(() => {
    pendingDataRef.current = "";
    atLineStartRef.current = true;
    if (frameRef.current !== null) {
      window.cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    terminalRef.current?.clear();
  }, []);

  const terminalSession = useTerminalSession({
    activeProject,
    profileId,
    title,
    onData: writeToTerminal,
    onReset: resetTerminal,
  });
  const { clear, rename, resize, restart, write } = terminalSession;

  useEffect(() => {
    showTimestampsRef.current = showTimestamps;
  }, [showTimestamps]);

  useEffect(() => {
    fontSizeRef.current = fontSize;
    const terminal = terminalRef.current;
    if (!ready || !terminal || !fontSize || terminal.options.fontSize === fontSize) return;
    terminal.options.fontSize = fontSize;
    fitRef.current?.fit();
  }, [fontSize, ready]);

  useEffect(() => {
    const session = terminalSession.session;
    if (!session) return;
    onExitStatus({
      closed: session.closed,
      exitCode: session.closed ? session.exit_code : null,
    });
  }, [onExitStatus, terminalSession.session]);

  useEffect(() => {
    if (previousTitleRef.current === title) return;
    previousTitleRef.current = title;
    void rename(title);
  }, [rename, title]);

  useEffect(() => {
    if (previousRestartRef.current === restartToken) return;
    previousRestartRef.current = restartToken;
    void restart();
  }, [restart, restartToken]);

  useEffect(() => {
    if (previousClearRef.current === clearToken) return;
    previousClearRef.current = clearToken;
    void clear();
  }, [clear, clearToken]);

  useEffect(() => {
    let disposed = false;
    const disposables: Array<{ dispose: () => void }> = [];

    async function mountTerminal() {
      if (!hostRef.current || terminalRef.current) return;
      const [
        { Terminal },
        { FitAddon },
        { WebLinksAddon },
        { SearchAddon },
        { WebglAddon },
      ] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
        import("@xterm/addon-web-links"),
        import("@xterm/addon-search"),
        import("@xterm/addon-webgl"),
      ]);
      if (disposed || !hostRef.current) return;
      const configuredFontSize = fontSizeRef.current;
      const cssFontSize = Number.parseInt(
        getComputedStyle(document.documentElement).getPropertyValue("--fx-terminal-font-size"),
        10,
      ) || 13;
      const terminal = new Terminal({
        cursorBlink: true,
        convertEol: true,
        fontFamily: "'Cascadia Code', 'Cascadia Mono', 'SFMono-Regular', Consolas, monospace",
        fontSize: configuredFontSize && configuredFontSize > 0 ? configuredFontSize : cssFontSize,
        theme: xtermTheme(),
        scrollback,
        lineHeight: 1.2,
        fontWeight: 400,
        fontWeightBold: 600,
        cursorStyle: "block",
        cursorInactiveStyle: "outline",
      });
      const fit = new FitAddon();
      terminal.loadAddon(fit);
      terminal.open(hostRef.current);
      try {
        const webgl = new WebglAddon();
        terminal.loadAddon(webgl);
        webglRef.current = webgl;
        webgl.onContextLoss(() => {
          webgl.dispose();
          if (webglRef.current === webgl) webglRef.current = null;
        });
      } catch {
        webglRef.current = null;
      }
      const links = new WebLinksAddon();
      terminal.loadAddon(links);
      linksRef.current = links;
      const search = new SearchAddon();
      terminal.loadAddon(search);
      searchRef.current = search;
      disposables.push(search.onDidChangeResults((matches) => {
        setSearchMatches(matches ? { index: matches.resultIndex, count: matches.resultCount } : null);
      }));
      terminal.attachCustomKeyEventHandler((event) => {
        if (event.type === "keydown" && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
          setSearchOpen(true);
          return false;
        }
        return true;
      });
      fit.fit();
      disposables.push(terminal.onData((data) => void write(data)));
      disposables.push(terminal.onResize(({ cols, rows }) => void resize(cols, rows)));
      terminalRef.current = terminal;
      fitRef.current = fit;
      flushPendingData();
      setReady(true);
    }

    void mountTerminal();
    return () => {
      disposed = true;
      for (const disposable of disposables) disposable.dispose();
      webglRef.current?.dispose();
      webglRef.current = null;
      linksRef.current?.dispose();
      linksRef.current = null;
      searchRef.current?.dispose();
      searchRef.current = null;
      fitRef.current?.dispose();
      fitRef.current = null;
      if (frameRef.current !== null) window.cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      terminalRef.current?.dispose();
      terminalRef.current = null;
      pendingDataRef.current = "";
      setReady(false);
      setSearchOpen(false);
      setSearchMatches(null);
    };
  }, [flushPendingData, resize, scrollback, write]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(() => {
      if (active) fitRef.current?.fit();
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, [active]);

  useEffect(() => {
    if (!active) return;
    const frame = window.requestAnimationFrame(() => {
      fitRef.current?.fit();
      terminalRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active]);

  useEffect(() => {
    const applyTheme = () => {
      const terminal = terminalRef.current;
      if (terminal) terminal.options.theme = xtermTheme();
    };
    applyTheme();
  }, [ready, themeVersion]);

  const runSearch = useCallback((term: string, direction: "next" | "previous") => {
    const search = searchRef.current;
    if (!search || !term) return;
    if (direction === "next") search.findNext(term);
    else search.findPrevious(term);
  }, []);

  const closeSearch = useCallback(() => {
    setSearchOpen(false);
    setSearchMatches(null);
    searchRef.current?.clearDecorations();
    if (active) terminalRef.current?.focus();
  }, [active]);

  const changeSearchQuery = useCallback((value: string) => {
    setSearchQuery(value);
    if (!value) {
      setSearchMatches(null);
      searchRef.current?.clearDecorations();
      return;
    }
    runSearch(value, "next");
  }, [runSearch]);

  const phase = useMemo<"starting" | "live" | "reconnecting" | "closed">(() => {
    if (terminalSession.starting || !ready) return "starting";
    if (terminalSession.session?.closed) return "closed";
    return terminalSession.streaming ? "live" : "reconnecting";
  }, [ready, terminalSession.session, terminalSession.starting, terminalSession.streaming]);

  const status = useMemo(() => {
    if (terminalSession.session?.process_capability === "pipe" && !terminalSession.session.closed) {
      return "pipe fallback";
    }
    if (phase === "starting") return "starting";
    if (phase === "closed") return `exited ${terminalSession.session?.exit_code}`;
    const capability = terminalSession.session?.process_capability === "pty" ? "PTY" : "ConPTY";
    return phase === "live" ? `${capability} connected` : "reconnecting";
  }, [phase, terminalSession.session]);

  const searchResultLabel = searchMatches
    ? searchMatches.count > 0
      ? `${searchMatches.index + 1}/${searchMatches.count}`
      : "0/0"
    : "";

  return (
    <div
      className={`absolute inset-0 flex min-h-0 flex-col bg-[var(--fx-bg)] ${
        active ? "visible z-10" : "invisible pointer-events-none z-0"
      }`}
      aria-hidden={!active}
    >
      <div className="flex h-7 shrink-0 items-center gap-2 border-b border-[var(--fx-border-soft)] px-2.5 text-[10px] text-[var(--fx-text-muted)]">
        <span className="font-medium text-[var(--fx-text)]">{terminalSession.session?.shell ?? title}</span>
        <span className="truncate" title={terminalSession.session?.cwd ?? activeProject.project_path}>
          {terminalSession.session?.cwd ?? activeProject.project_path}
        </span>
        <span className="ml-auto shrink-0">
          {status}
        </span>
        <button
          className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:cursor-not-allowed disabled:opacity-30"
          disabled={!ready}
          onClick={() => setSearchOpen(true)}
          title="Search terminal (Ctrl+F)"
          aria-label="Search terminal"
        >
          <Search className="h-3 w-3" />
        </button>
      </div>
      <div className="relative min-h-0 flex-1 overflow-hidden">
        <div
          ref={hostRef}
          className="h-full w-full px-2.5 py-1.5"
          onClick={() => terminalRef.current?.focus()}
        />
        {phase === "starting" ? (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-[var(--fx-bg)]">
            <div className="flex animate-pulse flex-col items-center gap-2">
              <div className="h-3 w-44 rounded bg-[var(--fx-panel-elevated)]" />
              <div className="h-3 w-28 rounded bg-[var(--fx-panel-elevated)]" />
            </div>
            <span className="text-xs text-[var(--fx-text-muted)]" role="status">
              Starting terminal…
            </span>
          </div>
        ) : null}
        {searchOpen ? (
          <div className="absolute right-2 top-1.5 z-30 flex items-center gap-1 rounded-md border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-1.5 py-1 shadow-lg">
            <Search className="h-3 w-3 shrink-0 text-[var(--fx-text-muted)]" />
            <input
              autoFocus
              className="h-5 w-40 rounded border border-transparent bg-transparent text-xs text-[var(--fx-text)] outline-none placeholder:text-[var(--fx-text-muted)] focus:border-[var(--fx-accent)]"
              placeholder="Find in terminal"
              value={searchQuery}
              aria-label="Search terminal"
              onChange={(event) => changeSearchQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  runSearch(searchQuery, event.shiftKey ? "previous" : "next");
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  closeSearch();
                }
              }}
            />
            <span className="w-10 shrink-0 text-right text-[10px] tabular-nums text-[var(--fx-text-muted)]">
              {searchResultLabel}
            </span>
            <button
              className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:cursor-not-allowed disabled:opacity-30"
              disabled={!searchQuery}
              onClick={() => runSearch(searchQuery, "previous")}
              title="Previous match (Shift+Enter)"
              aria-label="Previous match"
            >
              <ChevronUp className="h-3 w-3" />
            </button>
            <button
              className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:cursor-not-allowed disabled:opacity-30"
              disabled={!searchQuery}
              onClick={() => runSearch(searchQuery, "next")}
              title="Next match (Enter)"
              aria-label="Next match"
            >
              <ChevronDown className="h-3 w-3" />
            </button>
            <button
              className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
              onClick={closeSearch}
              title="Close search (Escape)"
              aria-label="Close search"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        ) : null}
        {terminalSession.error ? (
          <div
            className="absolute inset-x-2 bottom-2 z-30 flex items-center gap-2 rounded-md border border-[var(--fx-border)] border-l-2 border-l-[var(--fx-error)] bg-[var(--fx-panel-elevated)] px-2.5 py-1.5 text-xs shadow-lg"
            role="alert"
          >
            <CircleAlert className="h-3.5 w-3.5 shrink-0 text-[var(--fx-error)]" />
            <span className="min-w-0 flex-1 truncate text-[var(--fx-text)]" title={terminalSession.error}>
              {terminalSession.error}
            </span>
            <button
              className="flex shrink-0 items-center gap-1 rounded border border-[var(--fx-border)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--fx-text)] hover:border-[var(--fx-accent)] hover:text-[var(--fx-accent)] disabled:cursor-not-allowed disabled:opacity-40"
              onClick={() => void restart()}
              disabled={terminalSession.starting}
              title="Restart terminal session"
            >
              <RotateCw className="h-3 w-3" />
              Retry
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function clampNumberSetting(
  value: ForgeXSettingValue | undefined,
  fallback: number,
  minimum: number,
  maximum: number,
): number;
function clampNumberSetting(
  value: ForgeXSettingValue | undefined,
  fallback: null,
  minimum: number,
  maximum: number,
): number | null;
function clampNumberSetting(
  value: ForgeXSettingValue | undefined,
  fallback: number | null,
  minimum: number,
  maximum: number,
): number | null {
  const parsed = typeof value === "number"
    ? value
    : typeof value === "string"
      ? Number.parseFloat(value)
      : Number.NaN;
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, Math.round(parsed)));
}

function xtermTheme() {
  const styles = getComputedStyle(document.documentElement);
  return {
    background: styles.getPropertyValue("--fx-bg").trim(),
    foreground: styles.getPropertyValue("--fx-terminal-text").trim(),
    cursor: styles.getPropertyValue("--fx-text").trim(),
    selectionBackground: styles.getPropertyValue("--fx-accent-soft").trim(),
    black: styles.getPropertyValue("--fx-bg").trim(),
    brightBlack: styles.getPropertyValue("--fx-text-muted").trim(),
    red: styles.getPropertyValue("--fx-error").trim(),
    green: styles.getPropertyValue("--fx-success").trim(),
    yellow: styles.getPropertyValue("--fx-warning").trim(),
    blue: styles.getPropertyValue("--fx-info").trim(),
    magenta: styles.getPropertyValue("--fx-accent").trim(),
    cyan: styles.getPropertyValue("--fx-info").trim(),
    white: styles.getPropertyValue("--fx-text").trim(),
  } satisfies import("@xterm/xterm").ITheme;
}
