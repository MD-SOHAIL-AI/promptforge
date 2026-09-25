"use client";

import {
  Eraser,
  Pencil,
  Plus,
  RotateCw,
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

import { useTerminalSession } from "@/components/terminal/use-terminal-session";
import { promptForgeApi } from "@/lib/api";
import type { ProjectResponse, TerminalProfileResponse } from "@/types";

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

export function ForgeXTerminal({ activeProject }: ForgeXTerminalProps) {
  const [profiles, setProfiles] = useState<TerminalProfileResponse[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [tabs, setTabs] = useState<TerminalTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [renamingTabId, setRenamingTabId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const nextIdRef = useRef(0);
  const defaultProfileIdRef = useRef<string | undefined>(undefined);
  const profilesRef = useRef<TerminalProfileResponse[]>([]);

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
      })
      .catch(() => {
        if (!disposed) setProfiles([]);
      });
    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    setRenamingTabId(null);
    if (!activeProject?.project_id) {
      setTabs([]);
      setActiveTabId(null);
      return;
    }
    const initial = createTab(defaultProfileIdRef.current);
    setTabs([initial]);
    setActiveTabId(initial.id);
  }, [activeProject?.project_id, createTab]);

  const addTab = useCallback(() => {
    const next = createTab(selectedProfileId || defaultProfileIdRef.current);
    setTabs((current) => [...current, next]);
    setActiveTabId(next.id);
  }, [createTab, selectedProfileId]);

  const closeTab = useCallback((tabId: string) => {
    const index = tabs.findIndex((tab) => tab.id === tabId);
    const remaining = tabs.filter((tab) => tab.id !== tabId);
    setTabs(remaining);
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
                <button
                  className="mr-1 flex h-5 w-5 shrink-0 items-center justify-center rounded opacity-50 hover:bg-[var(--fx-hover)] hover:opacity-100 group-hover:opacity-100"
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
}: {
  active: boolean;
  activeProject: ProjectResponse;
  profileId?: string;
  title: string;
  restartToken: number;
  clearToken: number;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<import("@xterm/xterm").Terminal | null>(null);
  const fitRef = useRef<import("@xterm/addon-fit").FitAddon | null>(null);
  const pendingDataRef = useRef("");
  const frameRef = useRef<number | null>(null);
  const previousTitleRef = useRef(title);
  const previousRestartRef = useRef(restartToken);
  const previousClearRef = useRef(clearToken);
  const [ready, setReady] = useState(false);

  const flushPendingData = useCallback(() => {
    frameRef.current = null;
    const terminal = terminalRef.current;
    if (!terminal || !pendingDataRef.current) return;
    const data = pendingDataRef.current;
    pendingDataRef.current = "";
    terminal.write(data);
  }, []);

  const writeToTerminal = useCallback((data: string) => {
    pendingDataRef.current += data;
    if (frameRef.current === null) {
      frameRef.current = window.requestAnimationFrame(flushPendingData);
    }
  }, [flushPendingData]);

  const resetTerminal = useCallback(() => {
    pendingDataRef.current = "";
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
    let dataDisposable: { dispose: () => void } | null = null;
    let resizeDisposable: { dispose: () => void } | null = null;

    async function mountTerminal() {
      if (!hostRef.current || terminalRef.current) return;
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
      ]);
      if (disposed || !hostRef.current) return;
      const terminal = new Terminal({
        cursorBlink: true,
        convertEol: true,
        fontFamily: "'Cascadia Code', 'Cascadia Mono', 'SFMono-Regular', Consolas, monospace",
        fontSize: Number.parseInt(
          getComputedStyle(document.documentElement).getPropertyValue("--fx-terminal-font-size"),
          10,
        ) || 13,
        theme: xtermTheme(),
        scrollback: 5_000,
        lineHeight: 1.2,
        fontWeight: 400,
        fontWeightBold: 600,
        cursorStyle: "block",
        cursorInactiveStyle: "outline",
      });
      const fit = new FitAddon();
      terminal.loadAddon(fit);
      terminal.open(hostRef.current);
      fit.fit();
      dataDisposable = terminal.onData((data) => void write(data));
      resizeDisposable = terminal.onResize(({ cols, rows }) => void resize(cols, rows));
      terminalRef.current = terminal;
      fitRef.current = fit;
      flushPendingData();
      setReady(true);
    }

    void mountTerminal();
    return () => {
      disposed = true;
      dataDisposable?.dispose();
      resizeDisposable?.dispose();
      if (frameRef.current !== null) window.cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      terminalRef.current?.dispose();
      terminalRef.current = null;
      fitRef.current = null;
      setReady(false);
    };
  }, [flushPendingData, resize, write]);

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
    window.addEventListener("forgex-theme-change", applyTheme);
    return () => window.removeEventListener("forgex-theme-change", applyTheme);
  }, [ready]);

  const status = useMemo(() => {
    if (terminalSession.starting || !ready) return "starting";
    if (terminalSession.session?.closed) {
      return terminalSession.session.exit_code === null
        ? "closed"
        : `exited ${terminalSession.session.exit_code}`;
    }
    return terminalSession.streaming ? "ConPTY connected" : "reconnecting";
  }, [ready, terminalSession.session, terminalSession.starting, terminalSession.streaming]);

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
          {terminalSession.session?.process_capability === "pipe" && !terminalSession.session.closed
            ? "pipe fallback"
            : status}
        </span>
      </div>
      <div className="relative min-h-0 flex-1 overflow-hidden">
        <div
          ref={hostRef}
          className="h-full w-full px-2.5 py-1.5"
          onClick={() => terminalRef.current?.focus()}
        />
        {terminalSession.error ? (
          <div className="absolute bottom-2 left-2 right-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-2 py-1 text-xs text-[var(--fx-error)]">
            {terminalSession.error}
          </div>
        ) : null}
      </div>
    </div>
  );
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
  };
}
