"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { promptForgeApi } from "@/lib/api";
import { toErrorMessage } from "@/lib/errors";
import type { ProductAgentEvent, ProductAgentProvider, ProductAgentRun } from "@/types";

// SSE carries lifecycle events immediately. A short fallback poll also picks up
// streaming assistant text, which is intentionally kept out of event payloads.
const POLL_INTERVAL_MS = 750;
const terminal = (status?: string) => ["completed", "failed", "cancelled", "blocked", "timed_out", "interrupted"].includes(status ?? "");

export type ProductAgentConnectionState = "idle" | "connecting" | "live" | "polling";

function activeRunKey(projectId: string) {
  return `forgex-agent-active-run:${projectId}`;
}

function recoverConversationRunId(projectId: string) {
  try {
    const raw = JSON.parse(window.localStorage.getItem(`forgex-agent-conversation:${projectId}`) ?? "[]");
    if (!Array.isArray(raw)) return null;
    for (let index = raw.length - 1; index >= 0; index -= 1) {
      const runId = raw[index]?.runId;
      if (typeof runId === "string" && runId.startsWith("agent-run-")) return runId;
    }
  } catch {
    // Older or manually edited conversation state is ignored safely.
  }
  return null;
}

export function useProductAgentRun(projectId: string | null) {
  const [enabled, setEnabled] = useState(false);
  const [providers, setProviders] = useState<ProductAgentProvider[]>([]);
  const [run, setRun] = useState<ProductAgentRun | null>(null);
  const [events, setEvents] = useState<ProductAgentEvent[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [connectionState, setConnectionState] = useState<ProductAgentConnectionState>("idle");
  const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const source = useRef<EventSource | null>(null);
  const poll = useRef<number | null>(null);
  const refreshInFlight = useRef<Promise<ProductAgentRun> | null>(null);
  const runRef = useRef<ProductAgentRun | null>(null);

  const stop = useCallback(() => {
    source.current?.close();
    source.current = null;
    if (poll.current !== null) window.clearInterval(poll.current);
    poll.current = null;
    refreshInFlight.current = null;
    setConnectionState("idle");
  }, []);

  const refreshProviders = useCallback(async () => {
    try {
      const result = await promptForgeApi.agentRuntimeProviders();
      setEnabled(result.enabled);
      setProviders(result.providers);
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause, "Agent runtime status is unavailable."));
    }
  }, []);

  const refresh = useCallback(async (runId: string) => {
    if (refreshInFlight.current) return refreshInFlight.current;
    const request = promptForgeApi.agentRuntimeRun(runId)
      .then((result) => {
        runRef.current = result.run;
        setRun(result.run);
        setLastSyncedAt(new Date().toISOString());
        setError(null);
        if (terminal(result.run.status)) stop();
        return result.run;
      })
      .finally(() => {
        if (refreshInFlight.current === request) refreshInFlight.current = null;
      });
    refreshInFlight.current = request;
    return request;
  }, [stop]);

  const startPolling = useCallback((runId: string) => {
    if (poll.current !== null) return;
    setConnectionState((current) => current === "live" ? current : "polling");
    poll.current = window.setInterval(() => {
      void refresh(runId).catch((cause) => {
        setConnectionState("polling");
        setError(toErrorMessage(cause, "Agent status could not be refreshed."));
      });
    }, POLL_INTERVAL_MS);
  }, [refresh]);

  const connect = useCallback((runId: string) => {
    stop();
    setConnectionState("connecting");
    startPolling(runId);
    const stream = new EventSource(promptForgeApi.agentRuntimeEventsUrl(runId));
    source.current = stream;
    stream.onopen = () => setConnectionState("live");
    stream.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as ProductAgentEvent;
        if (event.run_id !== runId) return;
        setEvents((current) => current.some((item) => item.sequence === event.sequence)
          ? current
          : [...current, event].sort((a, b) => a.sequence - b.sequence).slice(-100));
        setLastSyncedAt(new Date().toISOString());
        void refresh(runId).catch(() => setConnectionState("polling"));
      } catch { /* sanitized malformed events are ignored */ }
    };
    stream.onerror = () => {
      stream.close();
      source.current = null;
      if (!terminal(runRef.current?.status)) {
        setConnectionState("polling");
        startPolling(runId);
      }
    };
  }, [refresh, startPolling, stop]);

  const submit = useCallback(async (instruction: string, providerId = "fake_planner") => {
    if (!projectId || submitting) return null;
    setSubmitting(true);
    setError(null);
    setEvents([]);
    try {
      const result = await promptForgeApi.startAgentRuntimeRun({
        project_id: projectId,
        instruction,
        provider_id: providerId,
        timeout_seconds: 300,
      });
      window.localStorage.setItem(activeRunKey(projectId), result.run.run_id);
      runRef.current = result.run;
      setRun(result.run);
      setLastSyncedAt(new Date().toISOString());
      connect(result.run.run_id);
      return result.run;
    } catch (cause) {
      setError(toErrorMessage(cause, "Agent runtime could not start."));
      return null;
    } finally {
      setSubmitting(false);
    }
  }, [connect, projectId, submitting]);

  const cancel = useCallback(async () => {
    const current = runRef.current;
    if (!current?.cancellable || cancelling) return;
    setCancelling(true);
    try {
      const next = (await promptForgeApi.cancelAgentRuntimeRun(current.run_id)).run;
      runRef.current = next;
      setRun(next);
      await refresh(next.run_id);
    } catch (cause) {
      setError(toErrorMessage(cause, "Agent runtime could not be cancelled."));
    } finally {
      setCancelling(false);
    }
  }, [cancelling, refresh]);

  const resolveApproval = useCallback(async (approvalId: string, decision: "approve_once" | "approve_session" | "decline" | "cancel") => {
    const current = runRef.current;
    if (!current) return;
    try {
      await promptForgeApi.resolveAgentRuntimeApproval(current.run_id, approvalId, decision);
      await refresh(current.run_id);
    } catch (cause) {
      setError(toErrorMessage(cause, "Agent approval could not be resolved."));
    }
  }, [refresh]);

  useEffect(() => {
    void refreshProviders();
  }, [refreshProviders]);

  useEffect(() => {
    stop();
    setRun(null);
    runRef.current = null;
    setEvents([]);
    setLastSyncedAt(null);
    if (!projectId) return;
    const savedRunId = window.localStorage.getItem(activeRunKey(projectId)) || recoverConversationRunId(projectId);
    if (!savedRunId) return;
    window.localStorage.setItem(activeRunKey(projectId), savedRunId);
    let disposed = false;
    setRestoring(true);
    void refresh(savedRunId)
      .then((restored) => {
        if (!disposed && !terminal(restored.status)) connect(restored.run_id);
      })
      .catch(() => {
        window.localStorage.removeItem(activeRunKey(projectId));
      })
      .finally(() => {
        if (!disposed) setRestoring(false);
      });
    return () => {
      disposed = true;
      stop();
    };
  }, [connect, projectId, refresh, stop]);

  useEffect(() => stop, [stop]);

  return {
    enabled,
    providers,
    run,
    events,
    submitting,
    cancelling,
    restoring,
    connectionState,
    lastSyncedAt,
    error,
    refreshProviders,
    refreshRun: refresh,
    submit,
    cancel,
    resolveApproval,
  };
}
