"use client";

import { Eraser, Plus, SquareTerminal } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useTerminalSession } from "@/components/terminal/use-terminal-session";
import type { ConsoleEntry, ProjectResponse } from "@/types";

interface ForgeXTerminalProps {
  activeProject: ProjectResponse | null;
  workflowLogs: ConsoleEntry[];
}

export function ForgeXTerminal({ activeProject, workflowLogs }: ForgeXTerminalProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<import("@xterm/xterm").Terminal | null>(null);
  const fitRef = useRef<import("@xterm/addon-fit").FitAddon | null>(null);
  const pendingDataRef = useRef("");
  const writtenLogIdsRef = useRef(new Set<string>());
  const previousLogCountRef = useRef(0);
  const [ready, setReady] = useState(false);

  const writeToTerminal = useCallback((data: string) => {
    const terminal = terminalRef.current;
    if (terminal) {
      terminal.write(data);
      return;
    }
    pendingDataRef.current += data;
  }, []);

  const resetTerminal = useCallback(() => {
    pendingDataRef.current = "";
    terminalRef.current?.clear();
  }, []);

  const terminalSession = useTerminalSession({
    activeProject,
    onData: writeToTerminal,
    onReset: resetTerminal,
  });
  const { write, resize } = terminalSession;

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
        scrollback: 5000,
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
      dataDisposable = terminal.onData((data) => {
        void write(data);
      });
      resizeDisposable = terminal.onResize(({ cols, rows }) => {
        void resize(cols, rows);
      });
      terminalRef.current = terminal;
      fitRef.current = fit;
      if (pendingDataRef.current) {
        terminal.write(pendingDataRef.current);
        pendingDataRef.current = "";
      }
      setReady(true);
    }

    void mountTerminal();

    return () => {
      disposed = true;
      dataDisposable?.dispose();
      resizeDisposable?.dispose();
      terminalRef.current?.dispose();
      terminalRef.current = null;
      fitRef.current = null;
      setReady(false);
    };
  }, [resize, write]);

  useEffect(() => {
    if (!hostRef.current) return;
    const observer = new ResizeObserver(() => {
      fitRef.current?.fit();
    });
    observer.observe(hostRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (terminal) {
      terminal.options.theme = xtermTheme();
    }
  });

  useEffect(() => {
    if (workflowLogs.length === 0) {
      if (previousLogCountRef.current > 0) {
        writtenLogIdsRef.current.clear();
        terminalRef.current?.clear();
      }
      previousLogCountRef.current = 0;
      return;
    }

    for (const log of workflowLogs) {
      if (writtenLogIdsRef.current.has(log.id)) continue;
      writtenLogIdsRef.current.add(log.id);
      writeToTerminal(formatWorkflowLog(log));
    }
    previousLogCountRef.current = workflowLogs.length;
  }, [workflowLogs, writeToTerminal]);

  if (!activeProject) {
    return (
      <div className="flex h-full items-center justify-center bg-[var(--fx-bg)] text-sm text-[var(--fx-text-muted)]">
        Open a workspace folder to start a terminal.
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--fx-bg)] text-[var(--fx-terminal-text)]">
      <div className="flex h-8 shrink-0 items-center justify-between gap-2 border-b border-[var(--fx-border-soft)] bg-[var(--fx-bg)] px-3 text-xs">
        <div className="flex min-w-0 items-center gap-2">
          <SquareTerminal className="h-3.5 w-3.5 shrink-0 text-[var(--fx-accent)]" />
          <span className="shrink-0 font-medium text-[var(--fx-text)]">{terminalSession.session?.shell ?? "PowerShell"}</span>
          <span className="truncate text-[10px] text-[var(--fx-text-muted)]" title={terminalSession.session?.cwd ?? activeProject.project_path}>
            {terminalSession.session?.cwd ?? activeProject.project_path}
          </span>
          {terminalSession.starting || !ready ? (
            <span className="shrink-0 text-[var(--fx-text-muted)]">starting</span>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            className="flex h-7 w-7 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
            onClick={() => void terminalSession.restart()}
            title="New terminal"
            aria-label="New terminal"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
          <button
            className="flex h-7 w-7 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
            onClick={() => void terminalSession.clear()}
            title="Clear terminal"
            aria-label="Clear terminal"
          >
            <Eraser className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      <div className="relative min-h-0 flex-1 overflow-hidden">
        <div ref={hostRef} className="h-full w-full px-2.5 py-1.5" onClick={() => terminalRef.current?.focus()} />
        {terminalSession.error ? (
          <div className="absolute bottom-2 left-2 right-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-2 py-1 text-xs text-[var(--fx-error)]">
            {terminalSession.error}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function formatWorkflowLog(log: ConsoleEntry) {
  const color = log.channel === "error"
    ? "\u001b[31m"
    : log.channel === "build"
      ? "\u001b[36m"
      : log.channel === "workflow"
        ? "\u001b[35m"
        : "\u001b[90m";
  const timestamp = new Date(log.timestamp).toLocaleTimeString([], { hour12: false });
  const safeMessage = log.message.replaceAll("\u001b", "").replace(/\r?\n/g, "\r\n");
  return `\r\n${color}[${timestamp}] [${log.channel}]\u001b[0m ${safeMessage}\r\n`;
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
