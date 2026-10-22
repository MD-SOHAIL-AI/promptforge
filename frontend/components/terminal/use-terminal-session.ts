"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { promptForgeApi } from "@/lib/api";
import { websocketBaseUrl } from "@/lib/websocket";
import type {
  ProjectResponse,
  TerminalOutputEvent,
  TerminalSessionResponse,
} from "@/types";

interface UseTerminalSessionOptions {
  activeProject: ProjectResponse | null;
  profileId?: string;
  title?: string;
  onData: (data: string) => void;
  onReset: () => void;
}

const MIN_COLS = 20;
const MIN_ROWS = 5;

export function useTerminalSession({
  activeProject,
  profileId,
  title,
  onData,
  onReset,
}: UseTerminalSessionOptions) {
  const projectId = activeProject?.project_id ?? null;
  const [session, setSession] = useState<TerminalSessionResponse | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const sessionRef = useRef<TerminalSessionResponse | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const cursorRef = useRef(0);
  const startingRef = useRef(false);
  const generationRef = useRef(0);
  const titleRef = useRef(title);
  const dimensionsRef = useRef({ cols: 80, rows: 24 });

  useEffect(() => {
    titleRef.current = title;
  }, [title]);

  const replaceSession = useCallback((next: TerminalSessionResponse | null) => {
    sessionRef.current = next;
    setSession(next);
  }, []);

  const closeSocket = useCallback(() => {
    const socket = socketRef.current;
    socketRef.current = null;
    if (socket && socket.readyState < WebSocket.CLOSING) {
      socket.close(1000, "Terminal view closed");
    }
    setStreaming(false);
  }, []);

  const createSession = useCallback(async (generation: number) => {
    if (!projectId || startingRef.current) return null;
    startingRef.current = true;
    setStarting(true);
    setError(null);
    try {
      const next = await promptForgeApi.startTerminal({
        project_id: projectId,
        profile_id: profileId,
        title: titleRef.current,
        cols: dimensionsRef.current.cols,
        rows: dimensionsRef.current.rows,
      });
      if (generation !== generationRef.current) {
        await promptForgeApi.stopTerminal(next.session_id).catch(() => null);
        return null;
      }
      cursorRef.current = 0;
      replaceSession(next);
      return next;
    } catch (err) {
      if (generation === generationRef.current) {
        setError(err instanceof Error ? err.message : "Terminal could not be started");
      }
      return null;
    } finally {
      startingRef.current = false;
      if (generation === generationRef.current) setStarting(false);
    }
  }, [profileId, projectId, replaceSession]);

  const start = useCallback(async () => {
    const generation = ++generationRef.current;
    return createSession(generation);
  }, [createSession]);

  const write = useCallback(async (data: string) => {
    const current = sessionRef.current;
    if (!current || current.closed) return;
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "input", data }));
      return;
    }
    try {
      await promptForgeApi.writeTerminal(current.session_id, data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Terminal input failed");
    }
  }, []);

  const clear = useCallback(async () => {
    const current = sessionRef.current;
    onReset();
    if (!current) return;
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "clear" }));
      return;
    }
    try {
      replaceSession(await promptForgeApi.clearTerminal(current.session_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Terminal could not be cleared");
    }
  }, [onReset, replaceSession]);

  const restart = useCallback(async () => {
    const generation = ++generationRef.current;
    const current = sessionRef.current;
    closeSocket();
    replaceSession(null);
    cursorRef.current = 0;
    onReset();
    if (current) {
      await promptForgeApi.stopTerminal(current.session_id).catch(() => null);
    }
    await createSession(generation);
  }, [closeSocket, createSession, onReset, replaceSession]);

  const rename = useCallback(async (nextTitle: string) => {
    titleRef.current = nextTitle;
    const current = sessionRef.current;
    if (!current) return;
    try {
      replaceSession(await promptForgeApi.renameTerminal(current.session_id, nextTitle));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Terminal could not be renamed");
    }
  }, [replaceSession]);

  const resize = useCallback(async (cols: number, rows: number) => {
    const dimensions = {
      cols: Math.max(MIN_COLS, Math.round(cols)),
      rows: Math.max(MIN_ROWS, Math.round(rows)),
    };
    dimensionsRef.current = dimensions;
    const current = sessionRef.current;
    if (!current || current.closed) return;
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "resize", ...dimensions }));
      return;
    }
    try {
      replaceSession(await promptForgeApi.resizeTerminal(
        current.session_id,
        dimensions.cols,
        dimensions.rows,
      ));
    } catch {
      // The next fit/stream reconnect retries with the latest dimensions.
    }
  }, [replaceSession]);

  useEffect(() => {
    const generation = ++generationRef.current;
    closeSocket();
    const previous = sessionRef.current;
    replaceSession(null);
    cursorRef.current = 0;
    onReset();
    if (previous) {
      void promptForgeApi.stopTerminal(previous.session_id).catch(() => null);
    }
    if (projectId) void createSession(generation);

    return () => {
      generationRef.current += 1;
      closeSocket();
      const current = sessionRef.current;
      sessionRef.current = null;
      if (current) {
        void promptForgeApi.stopTerminal(current.session_id).catch(() => null);
      }
    };
  }, [closeSocket, createSession, onReset, projectId, replaceSession]);

  const sessionId = session?.session_id;

  useEffect(() => {
    if (!sessionId) return;
    let disposed = false;
    let reconnectAttempt = 0;
    let reconnectTimer: number | null = null;

    const deliver = (event: Pick<TerminalOutputEvent, "sequence" | "data">) => {
      const sequence = Number(event.sequence ?? 0);
      if (sequence <= cursorRef.current) return;
      cursorRef.current = sequence;
      onData(String(event.data ?? ""));
    };

    const scheduleReconnect = () => {
      if (disposed || sessionRef.current?.closed) return;
      const delay = document.hidden
        ? 5_000
        : Math.min(5_000, 400 * (2 ** reconnectAttempt));
      reconnectAttempt += 1;
      reconnectTimer = window.setTimeout(connect, delay);
    };

    const connect = () => {
      if (disposed || sessionRef.current?.closed) return;
      const socket = new WebSocket(
        `${websocketBaseUrl()}/terminal/sessions/${encodeURIComponent(sessionId)}/stream?after=${cursorRef.current}`,
      );
      socketRef.current = socket;
      socket.addEventListener("open", () => {
        if (disposed) return;
        reconnectAttempt = 0;
        setStreaming(true);
        setError(null);
        socket.send(JSON.stringify({ type: "resize", ...dimensionsRef.current }));
      });
      socket.addEventListener("message", (message) => {
        try {
          const payload = JSON.parse(String(message.data)) as {
            type?: string;
            event?: TerminalOutputEvent;
            session?: TerminalSessionResponse;
            code?: string;
          };
          if (payload.type === "output" && payload.event) deliver(payload.event);
          if (payload.session) replaceSession(payload.session);
          if (payload.type === "error" && payload.code) setError(payload.code);
        } catch {
          setError("Terminal stream returned invalid data");
        }
      });
      socket.addEventListener("close", () => {
        if (socketRef.current === socket) socketRef.current = null;
        if (disposed) return;
        setStreaming(false);
        scheduleReconnect();
      });
      socket.addEventListener("error", () => {
        if (disposed) return;
        setError("Terminal stream disconnected; reconnecting.");
        socket.close();
      });
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      const socket = socketRef.current;
      if (socket) {
        socketRef.current = null;
        socket.close(1000, "Terminal stream replaced");
      }
      setStreaming(false);
    };
  }, [onData, replaceSession, sessionId]);

  useEffect(() => {
    if (!sessionId || streaming) return;
    let stopped = false;
    const poll = async () => {
      const current = sessionRef.current;
      if (!current || stopped || document.hidden) return;
      try {
        const response = await promptForgeApi.terminalOutput(
          current.session_id,
          cursorRef.current,
        );
        if (stopped) return;
        for (const event of response.events) {
          if (event.sequence <= cursorRef.current) continue;
          cursorRef.current = event.sequence;
          onData(event.data);
        }
        replaceSession(response);
      } catch (err) {
        if (!stopped) {
          setError(err instanceof Error ? err.message : "Terminal output could not be loaded");
        }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1_000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [onData, replaceSession, sessionId, streaming]);

  return {
    session,
    starting,
    error,
    streaming,
    start,
    write,
    clear,
    restart,
    rename,
    resize,
  };
}
