"use client";

import { RotateCcw, SquareTerminal, Trash2 } from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { promptForgeApi } from "@/lib/api";
import type { ProjectResponse, TerminalSessionResponse } from "@/types";

interface IntegratedTerminalProps {
  activeProject: ProjectResponse | null;
}

export function IntegratedTerminal({ activeProject }: IntegratedTerminalProps) {
  const [session, setSession] = useState<TerminalSessionResponse | null>(null);
  const [cursor, setCursor] = useState(0);
  const [transcript, setTranscript] = useState("");
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const outputRef = useRef<HTMLDivElement | null>(null);

  const start = useCallback(async () => {
    if (!activeProject || starting) return;
    setStarting(true);
    setError(null);
    try {
      const next = await promptForgeApi.startTerminal({ project_id: activeProject.project_id });
      setSession(next);
      setCursor(0);
      setTranscript("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Terminal could not be started");
    } finally {
      setStarting(false);
    }
  }, [activeProject, starting]);

  useEffect(() => {
    if (!activeProject) {
      setSession(null);
      setCursor(0);
      setTranscript("");
      return;
    }
    if (!session && !starting) {
      void start();
    }
  }, [activeProject, session, start, starting]);

  useEffect(() => {
    if (!session) return;
    let stopped = false;
    const poll = async () => {
      try {
        const response = await promptForgeApi.terminalOutput(session.session_id, cursor);
        if (stopped) return;
        if (response.events.length > 0) {
          setTranscript((current) => current + response.events.map((event) => event.data).join(""));
          setCursor(response.events.at(-1)?.sequence ?? cursor);
        }
        setSession((current) => (current ? { ...current, closed: response.closed } : current));
      } catch (err) {
        if (!stopped) {
          setError(err instanceof Error ? err.message : "Terminal output could not be loaded");
        }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 600);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [cursor, session]);

  useEffect(() => {
    outputRef.current?.scrollTo({ top: outputRef.current.scrollHeight });
  }, [transcript]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!session || !input.trim()) return;
    const command = input;
    setInput("");
    setTranscript((current) => `${current}${command}\r\n`);
    try {
      await promptForgeApi.writeTerminal(session.session_id, `${command}\r\n`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Terminal input failed");
    }
  };

  const clear = async () => {
    if (!session) {
      setTranscript("");
      return;
    }
    await promptForgeApi.clearTerminal(session.session_id).catch(() => null);
    setCursor(0);
    setTranscript("");
  };

  const restart = async () => {
    if (session) {
      await promptForgeApi.stopTerminal(session.session_id).catch(() => null);
    }
    setSession(null);
    setCursor(0);
    setTranscript("");
    await start();
  };

  if (!activeProject) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-[var(--fx-text-muted)]">
        Open a workspace folder to start a terminal.
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--fx-bg)] text-[var(--fx-terminal-text)]">
      <div className="flex min-h-9 items-center justify-between gap-2 border-b border-[var(--fx-border)] bg-[var(--fx-panel)] px-3 text-xs">
        <div className="flex min-w-0 items-center gap-2">
          <SquareTerminal className="h-3.5 w-3.5 shrink-0 text-[var(--fx-accent)]" />
          <span className="shrink-0 text-[var(--fx-text)]">{session?.shell ?? "PowerShell"}</span>
          <span className="truncate text-[var(--fx-text-muted)]">{session?.cwd ?? activeProject.project_path}</span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button className="rounded px-2 py-1 hover:bg-[var(--fx-hover)]" onClick={clear} title="Clear terminal">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
          <button className="rounded px-2 py-1 hover:bg-[var(--fx-hover)]" onClick={() => void restart()} title="Restart terminal">
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      <div
        ref={outputRef}
        className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap p-3 font-mono leading-5"
        style={{ fontSize: "var(--fx-terminal-font-size)" }}
      >
        {starting ? "Starting terminal...\r\n" : transcript || "Terminal ready.\r\n"}
        {error ? <span className="text-[var(--fx-error)]">{`\r\n${error}\r\n`}</span> : null}
      </div>
      <form className="flex shrink-0 items-center border-t border-[var(--fx-border)] bg-[var(--fx-panel)] px-3 py-2 font-mono" onSubmit={submit}>
        <span className="mr-2 text-[var(--fx-text-muted)]">{session?.shell === "PowerShell" ? "PS>" : "$"}</span>
        <input
          className="min-w-0 flex-1 bg-transparent text-[var(--fx-text)] outline-none"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          disabled={!session || session.closed}
          spellCheck={false}
          autoCapitalize="off"
          autoComplete="off"
        />
      </form>
    </div>
  );
}
