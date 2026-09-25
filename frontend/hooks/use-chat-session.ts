"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { promptForgeApi } from "@/lib/api";
import { mergeChatMessage, mergeChatSession } from "@/lib/chat-stream";
import type { ForgeChatSession } from "@/types";

const ACTIVE_CHAT_RECONCILE_MS = 750;

export function useChatSession() {
  const [sessions, setSessions] = useState<ForgeChatSession[]>([]);
  const [session, setSession] = useState<ForgeChatSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState("connecting");
  const active = useRef<string | null>(null);
  const stream = useRef<EventSource | null>(null);
  const refresh = useCallback(async () => {
    const result = await promptForgeApi.chatSessions();
    setSessions(result.sessions);
    return result.sessions;
  }, []);
  const select = useCallback(
    async (id: string) => {
      active.current = id;
      stream.current?.close();
      setConnection("connecting");
      setError(null);
      const result = await promptForgeApi.chatSession(id);
      if (active.current !== id) return;
      setSession(result.session);
      localStorage.setItem("forgex-chat-session", id);
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
        if (stream.current !== source || active.current !== id) return;
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
            void promptForgeApi
              .chatSession(id)
              .then((next) => {
                if (active.current === id && stream.current === source)
                  setSession((old) => mergeChatSession(old, next.session));
              })
              .catch(() => null);
            void refresh().catch(() => null);
          }
        } catch {
          /* Discard malformed packets; the durable session is authoritative. */
        }
      };
    },
    [refresh],
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
      setBusy(true);
      setError(null);
      try {
        const id = active.current ?? (await create());
        const result = await promptForgeApi.sendChatMessage(id, {
          content,
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
    let inFlight = false;
    const reconcile = async () => {
      if (disposed || inFlight || active.current !== sessionId) return;
      inFlight = true;
      try {
        const next = await promptForgeApi.chatSession(sessionId);
        if (!disposed && active.current === sessionId)
          setSession((old) => mergeChatSession(old, next.session));
      } catch {
        // EventSource reconnects independently; a later reconciliation can recover.
      } finally {
        inFlight = false;
      }
    };
    const timer = window.setInterval(
      () => void reconcile(),
      ACTIVE_CHAT_RECONCILE_MS,
    );
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [running, sessionId]);
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
      active.current = null;
      stream.current?.close();
      stream.current = null;
    };
  }, [refresh, select]);
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
