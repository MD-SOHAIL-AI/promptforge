"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { promptForgeApi } from "@/lib/api";
import type { ProjectResponse, TerminalSessionResponse } from "@/types";

interface UseTerminalSessionOptions {
  activeProject: ProjectResponse | null;
  onData: (data: string) => void;
  onReset: () => void;
}

export function useTerminalSession({
  activeProject,
  onData,
  onReset,
}: UseTerminalSessionOptions) {
  const [session, setSession] = useState<TerminalSessionResponse | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sessionRef = useRef<TerminalSessionResponse | null>(null);
  const cursorRef = useRef(0);
  const startingRef = useRef(false);

  const start = useCallback(async () => {
    if (!activeProject || startingRef.current) return null;
    startingRef.current = true;
    setStarting(true);
    setError(null);
    try {
      const next = await promptForgeApi.startTerminal({ project_id: activeProject.project_id });
      sessionRef.current = next;
      cursorRef.current = 0;
      setSession(next);
      return next;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Terminal could not be started");
      return null;
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  }, [activeProject]);

  const write = useCallback(async (data: string) => {
    const current = sessionRef.current;
    if (!current || current.closed) return;
    try {
      await promptForgeApi.writeTerminal(current.session_id, data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Terminal input failed");
    }
  }, []);

  const clear = useCallback(async () => {
    const current = sessionRef.current;
    onReset();
    cursorRef.current = 0;
    if (!current) return;
    await promptForgeApi.clearTerminal(current.session_id).catch(() => null);
  }, [onReset]);

  const restart = useCallback(async () => {
    const current = sessionRef.current;
    if (current) {
      await promptForgeApi.stopTerminal(current.session_id).catch(() => null);
    }
    sessionRef.current = null;
    cursorRef.current = 0;
    setSession(null);
    onReset();
    await start();
  }, [onReset, start]);

  const resize = useCallback(async (cols: number, rows: number) => {
    const current = sessionRef.current;
    if (!current) return;
    await promptForgeApi.resizeTerminal(current.session_id, cols, rows).catch(() => null);
  }, []);

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  useEffect(() => {
    if (!activeProject) {
      sessionRef.current = null;
      cursorRef.current = 0;
      setSession(null);
      onReset();
      return;
    }
    void start();
  }, [activeProject?.project_id, activeProject, onReset, start]);

  const sessionId = session?.session_id;

  useEffect(() => {
    if (!sessionId) return;
    let stopped = false;
    const poll = async () => {
      const current = sessionRef.current;
      if (!current || stopped) return;
      try {
        const response = await promptForgeApi.terminalOutput(current.session_id, cursorRef.current);
        if (stopped) return;
        if (response.events.length > 0) {
          cursorRef.current = response.events.at(-1)?.sequence ?? cursorRef.current;
          response.events.forEach((event) => onData(event.data));
        }
        sessionRef.current = { ...current, closed: response.closed };
        setSession(sessionRef.current);
      } catch (err) {
        if (!stopped) {
          setError(err instanceof Error ? err.message : "Terminal output could not be loaded");
        }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 150);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [onData, sessionId]);

  useEffect(() => {
    return () => {
      const current = sessionRef.current;
      if (current) {
        void promptForgeApi.stopTerminal(current.session_id).catch(() => null);
      }
    };
  }, []);

  return {
    session,
    starting,
    error,
    start,
    write,
    clear,
    restart,
    resize,
  };
}
