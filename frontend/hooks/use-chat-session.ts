"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { promptForgeApi } from "@/lib/api";
import { localSession } from "@/lib/local-session";
import { messageValidationError } from "@/lib/message-validation";
import { mergeChatMessage, mergeChatSession } from "@/lib/chat-stream";
import type { ForgeChatSession } from "@/types";

const ACTIVE_CHAT_RECONCILE_MS = 750;
const RECONCILE_WINDOW_MS = 150;

export function useChatSession() {
  const [sessions, setSessions] = useState<ForgeChatSession[]>([]);
  const [session, setSession] = useState<ForgeChatSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState("connecting");
  const active = useRef<string | null>(null);
  const stream = useRef<EventSource | null>(null);
  const selectionGeneration = useRef(0);
  const invalidateSelection = useCallback(() => { selectionGeneration.current++; }, []);
  const reconcileState = useRef<{timer: number | null; inFlight: boolean; queued: boolean}>({timer: null, inFlight: false, queued: false});
  useEffect(() => localSession.subscribe(() => {
    if (!localSession.getSnapshot()) return;
    stream.current?.close();
    stream.current = null;
    if (reconcileState.current.timer !== null) window.clearTimeout(reconcileState.current.timer);
    reconcileState.current.timer = null;
    setConnection("authentication_required");
  }), []);
  const refresh = useCallback(async () => {
    const result = await promptForgeApi.chatSessions();
    setSessions(result.sessions);
    return result.sessions;
  }, []);
  const scheduleReconcile = useCallback((id: string, generation: number) => {
    if (localSession.getSnapshot()) return;
    const state = reconcileState.current;
    if (state.timer !== null || generation !== selectionGeneration.current || active.current !== id) return;
    state.timer = window.setTimeout(() => {
      state.timer = null;
      if (generation !== selectionGeneration.current || active.current !== id) return;
      if (state.inFlight) {
        state.queued = true;
        return;
      }
      state.inFlight = true;
      void Promise.all([promptForgeApi.chatSession(id), promptForgeApi.chatSessions()])
        .then(([detail, list]) => {
          if (generation !== selectionGeneration.current || active.current !== id) return;
          setSession((old) => mergeChatSession(old, detail.session));
          setSessions(list.sessions);
        })
        .catch(() => null)
        .finally(() => {
          state.inFlight = false;
          if (state.queued) {
            state.queued = false;
            scheduleReconcile(id, generation);
          }
        });
    }, RECONCILE_WINDOW_MS);
  }, []);
  const select = useCallback(
    async (id: string) => {
      if (active.current === id && stream.current) return;
      const generation = ++selectionGeneration.current;
      if (reconcileState.current.timer !== null) window.clearTimeout(reconcileState.current.timer);
      reconcileState.current = {timer: null, inFlight: false, queued: false};
      active.current = id;
      stream.current?.close();
      stream.current = null;
      setConnection("connecting");
      setError(null);
      const result = await promptForgeApi.chatSession(id);
      if (active.current !== id || selectionGeneration.current !== generation) return;
      setSession(result.session);
      localStorage.setItem("forgex-chat-session", id);
      if (localSession.getSnapshot()) return;
      const source = new EventSource(
        promptForgeApi.chatSessionEventsUrl(id),
      );
      stream.current = source;
      let sequence = 0;
      source.onopen = () => {
        if (stream.current === source) setConnection("live");
      };
      source.onerror = () => {
        if (stream.current === source) setConnection("reconnecting");
      };
      source.onmessage = (message) => {
        if (stream.current !== source || active.current !== id || selectionGeneration.current !== generation) return;
        try {
          const event = JSON.parse(message.data);
          if (
            event.session_id !== id ||
            !Number.isFinite(event.sequence) ||
            event.sequence <= sequence
          )
            return;
          sequence = event.sequence;
          if (
            !String(event.event_type).startsWith("message.") ||
            typeof event.content !== "string"
          )
            return;
          setSession((current) => {
            if (!current || current.session_id !== id) return current;
            const old = current.messages.find(
              (m) => m.message_id === event.message_id,
            );
            const next = mergeChatMessage(old, {
              message_id: event.message_id,
              role: "assistant",
              created_at: event.created_at,
              content: event.content,
              metadata: { ...old?.metadata, status: event.message_status },
            });
            const messages = old
              ? current.messages.map((m) => (m === old ? next : m))
              : [...current.messages, next];
            return {
              ...current,
              status: messages.some((m) => m.metadata?.status === "streaming")
                ? "running"
                : "idle",
              messages,
            };
          });
          if (event.event_type !== "message.delta") {
            scheduleReconcile(id, generation);
          }
        } catch {
          /* Discard malformed packets; the durable session is authoritative. */
        }
      };
    },
    [scheduleReconcile],
  );
  const create = useCallback(async () => {
    const result = await promptForgeApi.createChatSession();
    await refresh();
    await select(result.session.session_id);
    return result.session.session_id;
  }, [refresh, select]);
  const send = useCallback(
    async (
      content: string,
      options: {
        provider_id?: string;
        model_id?: string;
        context_project_id?: string;
        context_paths?: string[];
        retry?: boolean;
      } = {},
    ) => {
      if (busy) return false;
      const prompt = content.trim();
      if (!prompt) return false;
      const validationError = messageValidationError(prompt);
      if (validationError) {
        setError(validationError);
        return false;
      }
      setBusy(true);
      setError(null);
      try {
        const id = active.current ?? (await create());
        const result = await promptForgeApi.sendChatMessage(id, {
          content: prompt,
          ...options,
        });
        if (active.current === id)
          setSession((old) => mergeChatSession(old, result.session));
        await refresh();
        return true;
      } catch (cause) {
        setError(
          cause instanceof Error ? cause.message : "Chat could not start.",
        );
        return false;
      } finally {
        setBusy(false);
      }
    },
    [busy, create, refresh],
  );
  const cancel = useCallback(async () => {
    if (!active.current) return;
    try {
      await promptForgeApi.cancelChatSession(active.current);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not stop the response.",
      );
    }
  }, []);
  const sessionId = session?.session_id ?? null;
  const running =
    session?.messages.some((m) => m.metadata?.status === "streaming") ?? false;
  useEffect(() => {
    if (!sessionId || !running) return;
    let disposed = false;
    const generation = selectionGeneration.current;
    const timer = window.setInterval(
      () => { if (!disposed) scheduleReconcile(sessionId, generation); },
      ACTIVE_CHAT_RECONCILE_MS,
    );
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [running, sessionId, scheduleReconcile]);
  useEffect(() => {
    let disposed = false;
    void refresh()
      .then((items) => {
        if (disposed) return;
        const saved = localStorage.getItem("forgex-chat-session");
        const selected = items.find((s) => s.session_id === saved) ?? items[0];
        if (selected) void select(selected.session_id).catch(() => null);
        else setConnection("idle");
      })
      .catch(() =>
        setError("Chat history is unavailable. Check the backend connection."),
      );
    return () => {
      disposed = true;
      invalidateSelection();
      if (reconcileState.current.timer !== null) window.clearTimeout(reconcileState.current.timer);
      reconcileState.current = {timer: null, inFlight: false, queued: false};
      active.current = null;
      stream.current?.close();
      stream.current = null;
    };
  }, [invalidateSelection, refresh, select]);
  return {
    sessions,
    session,
    busy,
    error,
    connection,
    create,
    select,
    send,
    cancel,
    refresh,
    running,
  };
}
