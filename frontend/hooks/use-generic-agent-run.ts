"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { promptForgeApi } from "@/lib/api";
import { appendGenericEvent, isGenericRunTerminal, mergeGenericRun } from "@/lib/generic-agent-state";
import { toErrorMessage } from "@/lib/errors";
import type { GenericProviderResponse, GenericRunDetailResponse, GenericRunEvent } from "@/types";

export type AgentConnectionState =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "polling"
  | "resync_required";

const POLL_INTERVAL_MS = 2_000;

function storageKey(projectId: string) {
  return `forgex.generic-agent.run.${projectId}`;
}

export function useGenericAgentRun(projectId: string | null, qaFixtureState: string | null = null) {
  const [providers, setProviders] = useState<GenericProviderResponse[]>([]);
  const [providersLoading, setProvidersLoading] = useState(true);
  const [run, setRun] = useState<GenericRunDetailResponse | null>(null);
  const [events, setEvents] = useState<GenericRunEvent[]>([]);
  const [connectionState, setConnectionState] = useState<AgentConnectionState>("idle");
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const pollRef = useRef<number | null>(null);
  const submitLockRef = useRef(false);
  const qaFixtureStateRef = useRef(qaFixtureState);
  qaFixtureStateRef.current = qaFixtureState;
  const runRef = useRef<GenericRunDetailResponse | null>(null);
  const refreshRef = useRef<{ runId: string; promise: Promise<GenericRunDetailResponse> } | null>(null);

  const stopTransport = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const updateRun = useCallback((incoming: GenericRunDetailResponse) => {
    setRun((current) => {
      const merged = mergeGenericRun(current, incoming);
      runRef.current = merged;
      return merged;
    });
  }, []);

  const refreshRun = useCallback(async (runId: string) => {
    if (refreshRef.current?.runId === runId) return refreshRef.current.promise;
    const promise = promptForgeApi.genericRun(runId).then((detail) => {
      updateRun(detail);
      return detail;
    }).finally(() => {
      if (refreshRef.current?.promise === promise) refreshRef.current = null;
    });
    refreshRef.current = { runId, promise };
    return promise;
  }, [updateRun]);

  const startPolling = useCallback((runId: string) => {
    if (pollRef.current !== null) return;
    sourceRef.current?.close();
    sourceRef.current = null;
    setConnectionState("polling");
    const poll = async () => {
      try {
        const detail = await refreshRun(runId);
        if (isGenericRunTerminal(detail.status) && pollRef.current !== null) {
          window.clearInterval(pollRef.current);
          pollRef.current = null;
          setConnectionState("idle");
        }
      } catch (cause) {
        setError(toErrorMessage(cause, "The ForgeX backend is unavailable."));
      }
    };
    pollRef.current = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    void poll();
  }, [refreshRun]);

  const connectEvents = useCallback((runId: string) => {
    stopTransport();
    setConnectionState("connecting");
    const source = new EventSource(promptForgeApi.genericRunEventsUrl(runId));
    sourceRef.current = source;
    source.onopen = () => setConnectionState("connected");
    source.onmessage = (message) => {
      let event: GenericRunEvent;
      try {
        event = JSON.parse(message.data) as GenericRunEvent;
      } catch {
        return;
      }
      if (event.run_id !== runId) return;
      if (event.event_type === "resync_required") {
        source.close();
        sourceRef.current = null;
        setConnectionState("resync_required");
        void refreshRun(runId).finally(() => startPolling(runId));
        return;
      }
      setEvents((current) => appendGenericEvent(current, event, runId));
      void refreshRun(runId).then((detail) => {
        if (isGenericRunTerminal(detail.status)) {
          source.close();
          sourceRef.current = null;
          setConnectionState("idle");
        }
      }).catch(() => {
        source.close();
        sourceRef.current = null;
        setConnectionState("reconnecting");
        window.setTimeout(() => startPolling(runId), 500);
      });
    };
    source.onerror = () => {
      if (isGenericRunTerminal(runRef.current?.status)) return;
      source.close();
      sourceRef.current = null;
      setConnectionState("reconnecting");
      window.setTimeout(() => startPolling(runId), 500);
    };
  }, [refreshRun, startPolling, stopTransport]);

  const loadProviders = useCallback(async () => {
    setProvidersLoading(true);
    try {
      if (qaFixtureState) {
        const fixture = await promptForgeApi.genericAgentQaFixture(qaFixtureState);
        stopTransport();
        setProviders(fixture.providers);
        setRun(fixture.run ?? null);
        runRef.current = fixture.run ?? null;
        setEvents(fixture.events);
        setConnectionState(fixture.connection_state);
        setSubmitting(fixture.submitting);
        setError(fixture.error ?? null);
        return;
      }
      const response = await promptForgeApi.genericProviders();
      if (qaFixtureStateRef.current) return;
      setProviders(Array.isArray(response.providers) ? response.providers : []);
      setError(null);
    } catch (cause) {
      if (qaFixtureStateRef.current && !qaFixtureState) return;
      setProviders([]);
      setError(toErrorMessage(cause, "The ForgeX backend is unavailable."));
    } finally {
      if (!qaFixtureStateRef.current || qaFixtureState) setProvidersLoading(false);
    }
  }, [qaFixtureState, stopTransport]);

  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);

  useEffect(() => {
    stopTransport();
    setEvents([]);
    setRun(null);
    runRef.current = null;
    if (qaFixtureState) return;
    if (!projectId) return;
    const restored = window.localStorage.getItem(storageKey(projectId));
    if (!restored) return;
    void refreshRun(restored)
      .then((detail) => {
        if (!isGenericRunTerminal(detail.status)) connectEvents(detail.run_id);
      })
      .catch(() => window.localStorage.removeItem(storageKey(projectId)));
    return stopTransport;
  }, [connectEvents, projectId, qaFixtureState, refreshRun, stopTransport]);

  const submit = useCallback(async (instruction: string, timeoutSeconds = 300) => {
    if (qaFixtureState || !projectId || submitLockRef.current || !instruction.trim()) return null;
    submitLockRef.current = true;
    setSubmitting(true);
    setError(null);
    setEvents([]);
    const idempotencyKey = `agent-${crypto.randomUUID()}`;
    try {
      const started = await promptForgeApi.startGenericRun({
        provider_id: "agy",
        project_id: projectId,
        instruction,
        timeout_seconds: timeoutSeconds,
        idempotency_key: idempotencyKey,
      });
      window.localStorage.setItem(storageKey(projectId), started.run_id);
      const detail = await refreshRun(started.run_id);
      if (!isGenericRunTerminal(detail.status)) connectEvents(detail.run_id);
      return detail;
    } catch (cause) {
      setError(toErrorMessage(cause, "The agent run could not be started."));
      return null;
    } finally {
      submitLockRef.current = false;
      setSubmitting(false);
    }
  }, [connectEvents, projectId, qaFixtureState, refreshRun]);

  const cancel = useCallback(async () => {
    if (qaFixtureState) return;
    const current = runRef.current;
    if (!current?.cancellable || cancelling) return;
    setCancelling(true);
    setError(null);
    try {
      await promptForgeApi.cancelGenericRun(current.run_id);
      await refreshRun(current.run_id);
    } catch (cause) {
      setError(toErrorMessage(cause, "The agent run could not be cancelled."));
    } finally {
      setCancelling(false);
    }
  }, [cancelling, qaFixtureState, refreshRun]);

  useEffect(() => stopTransport, [stopTransport]);

  return {
    providers,
    provider: providers.find((item) => item.provider_id === "agy") ?? null,
    providersLoading,
    reloadProviders: loadProviders,
    run,
    events,
    connectionState,
    submitting,
    cancelling,
    error,
    qaFixtureActive: Boolean(qaFixtureState),
    submit,
    cancel,
  };
}
