"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PromptForgeApiError, promptForgeApi } from "@/lib/api";
import type { ForgeComposerContext } from "@/components/nexus/universal-composer";
import { toErrorMessage } from "@/lib/errors";
import { notify } from "@/lib/notify";
import {
  agentEventKey,
  remainingActivityMs,
  shouldAcceptActivity,
  shouldRefreshRunForEvent,
  snapshotFromActivityEvent,
  snapshotFromRunEvent,
  type ForgeAgentActivitySnapshot,
} from "@/lib/forge-agent-activity";
import type { AgentExecutionGraph, ForgeAgentActivityEvent, ForgeAgentMessage, ForgeAgentSession, ProductAgentEvent, ProductAgentProvider, ProductAgentRun } from "@/types";

const POLL_INTERVAL_MS = 750;
const terminal = (status?: string) => ["completed", "failed", "cancelled", "blocked", "timed_out"].includes(status ?? "");
const stable = (status?: string) => terminal(status) || status === "awaiting_flash_confirmation";

export type ForgeAgentConnectionState = "idle" | "connecting" | "live" | "polling";

function activeSessionKey(projectId: string) {
  return `forgex-agent-active-session:${projectId}`;
}

export function useForgeAgentSession(projectId: string | null) {
  const [enabled, setEnabled] = useState(false);
  const [providers, setProviders] = useState<ProductAgentProvider[]>([]);
  const [sessions, setSessions] = useState<ForgeAgentSession[]>([]);
  const [activeSession, setActiveSession] = useState<ForgeAgentSession | null>(null);
  const [run, setRun] = useState<ProductAgentRun | null>(null);
  const [events, setEvents] = useState<ProductAgentEvent[]>([]);
  const [optimisticMessages, setOptimisticMessages] = useState<ForgeAgentMessage[]>([]);
  const [streamingMessage, setStreamingMessage] = useState<ForgeAgentMessage | null>(null);
  const [graph, setGraph] = useState<AgentExecutionGraph | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [applyingChanges, setApplyingChanges] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [connectionState, setConnectionState] = useState<ForgeAgentConnectionState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [currentActivity, setCurrentActivity] = useState<ForgeAgentActivitySnapshot | null>(null);
  const source = useRef<EventSource | null>(null);
  const activitySource = useRef<EventSource | null>(null);
  const poll = useRef<number | null>(null);
  const activitySequences = useRef<Record<string, number>>({});
  const activityClearTimer = useRef<number | null>(null);
  const runRef = useRef<ProductAgentRun | null>(null);
  const activeSessionRef = useRef<ForgeAgentSession | null>(null);
  const appliedDeltaSequences = useRef<Record<string, Set<number>>>({});
  const pendingDeltaText = useRef<Record<string, string>>({});
  const announcedRun = useRef<{ key: string; status: string }>({ key: "", status: "" });
  const processedRunEvents = useRef<Set<string>>(new Set());

  const routeableProviders = useMemo(() => providers.filter((provider) => provider.routeable), [providers]);
  const messages = useMemo(() => {
    const merged = [...(activeSession?.messages ?? []), ...optimisticMessages];
    if (streamingMessage) merged.push(streamingMessage);
    return merged;
  }, [activeSession, optimisticMessages, streamingMessage]);
  const defaultProviderId = useMemo(
    () => routeableProviders.find((provider) => provider.selected)?.provider_id
      ?? routeableProviders.find((provider) => provider.kind === "api_planner")?.provider_id
      ?? routeableProviders.find((provider) => provider.provider_id === "verified_template")?.provider_id
      ?? routeableProviders[0]?.provider_id
      ?? "verified_template",
    [routeableProviders],
  );

  const stopPolling = useCallback(() => {
    if (poll.current !== null) window.clearInterval(poll.current);
    poll.current = null;
  }, []);

  const stopStream = useCallback(() => {
    source.current?.close();
    source.current = null;
    stopPolling();
    setConnectionState("idle");
  }, [stopPolling]);

  const announce = useCallback((detail: string) => {
    window.dispatchEvent(new CustomEvent("forgex-announce", { detail }));
  }, []);

  const ensureStreamingPlaceholder = useCallback((runId: string) => {
    if (!runId) return;
    setStreamingMessage((current) => (
      current && current.run_id === runId
        ? current
        : {
            message_id: `forgex-streaming-${runId}`,
            role: "assistant",
            content: pendingDeltaText.current[runId] ?? "",
            created_at: new Date().toISOString(),
            run_id: runId,
            streaming: true,
          }
    ));
    pendingDeltaText.current[runId] = "";
  }, []);

  const addRunEvent = useCallback((event: ProductAgentEvent) => {
    setEvents((current) => {
      const key = agentEventKey(event);
      return current.some((item) => agentEventKey(item) === key)
        ? current
        : [...current, event].sort((a, b) => a.sequence - b.sequence).slice(-400);
    });
  }, []);

  const handleDeltaEvent = useCallback((event: ProductAgentEvent) => {
    if (event.event_type !== "message.delta" || !event.run_id || !event.delta) return;
    const runId = event.run_id;
    const delta = event.delta;
    let applied = appliedDeltaSequences.current[runId];
    if (!applied) {
      applied = new Set<number>();
      appliedDeltaSequences.current[runId] = applied;
    }
    if (applied.has(event.sequence)) return;
    applied.add(event.sequence);
    if (applied.size > 2000) {
      const oldest = applied.values().next();
      if (!oldest.done) applied.delete(oldest.value);
    }
    setStreamingMessage((current) => {
      if (!current || (current.run_id && current.run_id !== runId)) {
        pendingDeltaText.current[runId] = `${pendingDeltaText.current[runId] ?? ""}${delta}`;
        return current;
      }
      return { ...current, content: current.content + delta };
    });
  }, []);

  const cancelActivityClear = useCallback(() => {
    if (activityClearTimer.current !== null) window.clearTimeout(activityClearTimer.current);
    activityClearTimer.current = null;
  }, []);

  const clearActivity = useCallback((force = false) => {
    cancelActivityClear();
    setCurrentActivity((current) => {
      if (!current || force) return null;
      const remaining = remainingActivityMs(current.startedAt);
      if (remaining <= 0) return null;
      activityClearTimer.current = window.setTimeout(() => setCurrentActivity(null), remaining);
      return current;
    });
    return null;
  }, [cancelActivityClear]);

  const showActivity = useCallback((snapshot: ForgeAgentActivitySnapshot | null) => {
    if (!snapshot) return;
    const sessionId = activeSessionRef.current?.session_id;
    const runId = runRef.current?.run_id;
    if (!shouldAcceptActivity(snapshot, { activeSessionId: sessionId, activeRunId: snapshot.runId ? runId : null })) return;
    cancelActivityClear();
    setCurrentActivity(snapshot);
  }, [cancelActivityClear]);

  const handleActivityEvent = useCallback((event: ForgeAgentActivityEvent) => {
    if (!event.session_id) return;
    activitySequences.current[event.session_id] = Math.max(activitySequences.current[event.session_id] ?? 0, Number(event.sequence) || 0);
    if (event.phase === "completed" || event.event_type === "activity.completed") {
      clearActivity();
      return;
    }
    showActivity(snapshotFromActivityEvent(event));
  }, [clearActivity, showActivity]);

  const handleRunEvent = useCallback((event: ProductAgentEvent, sessionId?: string | null) => {
    const key = agentEventKey(event);
    if (processedRunEvents.current.has(key)) return false;
    processedRunEvents.current.add(key);
    if (processedRunEvents.current.size > 2_000) {
      const oldest = processedRunEvents.current.values().next();
      if (!oldest.done) processedRunEvents.current.delete(oldest.value);
    }
    addRunEvent(event);
    handleDeltaEvent(event);
    showActivity(snapshotFromRunEvent(event, sessionId));
    return true;
  }, [addRunEvent, handleDeltaEvent, showActivity]);

  const stopActivityStream = useCallback(() => {
    activitySource.current?.close();
    activitySource.current = null;
  }, []);

  const connectActivity = useCallback((sessionId: string) => {
    if (!sessionId) return;
    const currentUrl = activitySource.current ? (activitySource.current as EventSource & { url?: string }).url : "";
    if (currentUrl.includes(`/sessions/${encodeURIComponent(sessionId)}/events`)) return;
    stopActivityStream();
    const after = activitySequences.current[sessionId] ?? 0;
    const stream = new EventSource(promptForgeApi.agentRuntimeSessionEventsUrl(sessionId, after));
    activitySource.current = stream;
    stream.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as ForgeAgentActivityEvent & ProductAgentEvent;
        activitySequences.current[sessionId] = Math.max(
          activitySequences.current[sessionId] ?? 0,
          Number(event.sequence) || 0,
        );
        if (event.activity && event.label) handleActivityEvent(event);
        if (event.run_id) {
          handleRunEvent(event, sessionId);
          if (event.node_id || event.event_type.startsWith("node.")) {
            void promptForgeApi.agentRuntimeGraph(event.run_id)
              .then((result) => setGraph(result.graph))
              .catch(() => null);
          }
        }
      } catch {
        // Activity is presentation-only; malformed transient updates are ignored.
      }
    };
    stream.onerror = () => {
      // EventSource reconnects automatically and retains the session cursor.
    };
  }, [handleActivityEvent, handleRunEvent, stopActivityStream]);

  const refreshGraph = useCallback(async (runId: string) => {
    try {
      const result = await promptForgeApi.agentRuntimeGraph(runId);
      setGraph(result.graph);
      return result.graph;
    } catch {
      setGraph(null);
      return null;
    }
  }, []);

  const refreshProviders = useCallback(async () => {
    try {
      const result = await promptForgeApi.agentRuntimeProviders();
      setEnabled(result.enabled);
      setProviders(Array.isArray(result.providers) ? result.providers : []);
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause, "Agent runtime status is unavailable."));
    }
  }, []);

  const refreshSessions = useCallback(async () => {
    if (!projectId) {
      setSessions([]);
      return [];
    }
    try {
      const result = await promptForgeApi.agentRuntimeSessions(projectId);
      const next = Array.isArray(result.sessions) ? result.sessions : [];
      setSessions(next);
      return next;
    } catch (cause) {
      setSessions([]);
      setError(toErrorMessage(cause, "Agent sessions could not be loaded."));
      return [];
    }
  }, [projectId]);

  const refreshSession = useCallback(async (sessionId: string) => {
    const result = await promptForgeApi.agentRuntimeSession(sessionId);
    activeSessionRef.current = result.session;
    setActiveSession(result.session);
    setStreamingMessage((current) => {
      if (!current) return null;
      const settled = (result.session.messages ?? []).some(
        (item) => item.role === "assistant" && item.run_id === current.run_id,
      );
      return settled ? null : current;
    });
    connectActivity(result.session.session_id);
    if (projectId) window.localStorage.setItem(activeSessionKey(projectId), result.session.session_id);
    void refreshSessions().catch(() => null);
    return result.session;
  }, [connectActivity, projectId, refreshSessions]);

  const createSession = useCallback(async (title?: string | null) => {
    if (!projectId) return null;
    try {
      const created = await promptForgeApi.createAgentRuntimeSession({ project_id: projectId, title });
      activeSessionRef.current = created.session;
      setActiveSession(created.session);
      connectActivity(created.session.session_id);
      setRun(null);
      runRef.current = null;
      setEvents([]);
      setOptimisticMessages([]);
      setStreamingMessage(null);
      pendingDeltaText.current = {};
      appliedDeltaSequences.current = {};
      setGraph(null);
      stopStream();
      window.localStorage.setItem(activeSessionKey(projectId), created.session.session_id);
      void refreshSessions().catch(() => null);
      return created.session;
    } catch (cause) {
      const message = toErrorMessage(cause, "A new Forge task could not be created.");
      setError(message);
      notify.error("Forge task could not be created", { description: message });
      return null;
    }
  }, [connectActivity, projectId, refreshSessions, stopStream]);

  const refreshRun = useCallback(async (runId: string) => {
    const result = await promptForgeApi.agentRuntimeRun(runId);
    runRef.current = result.run;
    setRun(result.run);
    void refreshGraph(runId);
    setError(null);
    if (stable(result.run.status)) {
      stopStream();
      clearActivity();
      setStreamingMessage(null);
      const sessionId = activeSessionRef.current?.session_id;
      if (sessionId) {
        window.setTimeout(() => void refreshSession(sessionId).catch(() => null), 150);
      }
    }
    return result.run;
  }, [clearActivity, refreshGraph, refreshSession, stopStream]);

  const startPolling = useCallback((runId: string) => {
    if (poll.current !== null) return;
    setConnectionState((current) => current === "live" ? current : "polling");
    poll.current = window.setInterval(() => {
      void refreshRun(runId).catch((cause) => {
        setConnectionState("polling");
        setError(toErrorMessage(cause, "Agent status could not be refreshed."));
      });
    }, POLL_INTERVAL_MS);
  }, [refreshRun]);

  const connect = useCallback((runId: string) => {
    stopStream();
    setConnectionState("connecting");
    ensureStreamingPlaceholder(runId);
    startPolling(runId);
    const stream = new EventSource(promptForgeApi.agentRuntimeEventsUrl(runId));
    source.current = stream;
    stream.onopen = () => {
      stopPolling();
      setConnectionState("live");
    };
    stream.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as ProductAgentEvent;
        if (event.run_id !== runId) return;
        if (!handleRunEvent(event, activeSessionRef.current?.session_id)) return;
        if (shouldRefreshRunForEvent(event)) {
          void refreshRun(runId).catch(() => setConnectionState("polling"));
        }
      } catch {
        setConnectionState("polling");
      }
    };
    stream.onerror = () => {
      stream.close();
      source.current = null;
      if (!stable(runRef.current?.status)) {
        setConnectionState("polling");
        startPolling(runId);
        notify.error("Live Forge connection interrupted", { description: "Continuing with slower polling until it reconnects." });
      }
    };
  }, [ensureStreamingPlaceholder, handleRunEvent, refreshRun, startPolling, stopPolling, stopStream]);

  const resumeSession = useCallback(async (sessionId: string) => {
    setRestoring(true);
    setError(null);
    setOptimisticMessages([]);
    setStreamingMessage(null);
    pendingDeltaText.current = {};
    appliedDeltaSequences.current = {};
    try {
      const session = await refreshSession(sessionId);
      if (session.active_run_id) {
        const nextRun = await refreshRun(session.active_run_id);
        setEvents([]);
        if (!stable(nextRun.status)) connect(nextRun.run_id);
      } else {
        setRun(null);
        setEvents([]);
        stopStream();
        clearActivity(true);
      }
      return session;
    } catch (cause) {
      if (cause instanceof PromptForgeApiError && cause.status === 404 && projectId) {
        window.localStorage.removeItem(activeSessionKey(projectId));
        return createSession();
      }
      setError(toErrorMessage(cause, "Agent session could not be loaded."));
      return null;
    } finally {
      setRestoring(false);
    }
  }, [clearActivity, connect, createSession, projectId, refreshRun, refreshSession, stopStream]);

  const ensureSession = useCallback(async () => {
    if (!projectId) return null;
    if (activeSessionRef.current?.project_id === projectId) return activeSessionRef.current;
    return createSession();
  }, [createSession, projectId]);

  const sendMessage = useCallback(async (
    content: string,
    providerId?: string,
    options: {
      autonomy?: "auto" | "staged_changes" | "plan_only" | "build_only" | "build_then_confirm_flash";
      boardPort?: string | null;
      boardType?: string | null;
      environment?: string | null;
      startMonitorAfterFlash?: boolean;
      context?: ForgeComposerContext;
    } = {},
  ) => {
    if (!projectId || submitting) return null;
    const prompt = content.trim();
    if (!prompt) return null;
    setSubmitting(true);
    setError(null);
    const tempId = `forgex-temp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setOptimisticMessages((current) => [
      ...current,
      { message_id: tempId, role: "user", content: prompt, created_at: new Date().toISOString() },
    ]);
    const dropTempMessage = () => setOptimisticMessages((current) => current.filter((item) => item.message_id !== tempId));
    try {
      const session = await ensureSession();
      if (!session) {
        dropTempMessage();
        return null;
      }
      connectActivity(session.session_id);
      const result = await promptForgeApi.sendAgentRuntimeSessionMessage(session.session_id, {
        content: prompt,
        provider_id: providerId || defaultProviderId,
        autonomy: options.autonomy ?? "auto",
        board_port: options.boardPort ?? undefined,
        board_type: options.boardType ?? undefined,
        environment: options.environment ?? undefined,
        start_monitor_after_flash: options.startMonitorAfterFlash ?? false,
        context: options.context,
      });
      // The server session now owns the persisted user message.
      dropTempMessage();
      activeSessionRef.current = result.session;
      setActiveSession(result.session);
      for (const event of result.activity_events ?? []) handleActivityEvent(event);
      void refreshSessions().catch(() => null);
      if (result.run) {
        runRef.current = result.run;
        setRun(result.run);
        void refreshGraph(result.run.run_id);
        if (!stable(result.run.status)) connect(result.run.run_id);
        else void refreshSession(result.session.session_id).catch(() => null);
      } else {
        const currentRun = runRef.current;
        const sessionStillOwnsRun = Boolean(
          result.session.active_run_id
          && currentRun
          && result.session.active_run_id === currentRun.run_id
          && !stable(currentRun.status),
        );
        if (!sessionStillOwnsRun) {
          runRef.current = null;
          setRun(null);
          setGraph(null);
          setStreamingMessage(null);
          stopStream();
          clearActivity();
        }
      }
      return result;
    } catch (cause) {
      dropTempMessage();
      const message = toErrorMessage(cause, "Agent message could not be sent.");
      setError(message);
      notify.error("Forge could not send your message", { description: message });
      return null;
    } finally {
      setSubmitting(false);
    }
  }, [clearActivity, connect, connectActivity, defaultProviderId, ensureSession, handleActivityEvent, projectId, refreshGraph, refreshSession, refreshSessions, stopStream, submitting]);

  const cancel = useCallback(async () => {
    const session = activeSessionRef.current;
    if (!session || cancelling) return null;
    setCancelling(true);
    try {
      const result = await promptForgeApi.cancelAgentRuntimeSession(session.session_id);
      activeSessionRef.current = result.session;
      setActiveSession(result.session);
      for (const event of result.activity_events ?? []) handleActivityEvent(event);
      if (result.run) {
        runRef.current = result.run;
        setRun(result.run);
      }
      clearActivity();
      await refreshSession(session.session_id).catch(() => null);
      return result;
    } catch (cause) {
      setError(toErrorMessage(cause, "Agent session could not be cancelled."));
      return null;
    } finally {
      setCancelling(false);
    }
  }, [cancelling, clearActivity, handleActivityEvent, refreshSession]);

  const confirmFlash = useCallback(async (options: {
    port?: string | null;
    boardType?: string | null;
    environment?: string | null;
    startMonitorAfterFlash?: boolean | null;
  } = {}) => {
    const current = runRef.current;
    if (!current || current.status !== "awaiting_flash_confirmation") return null;
    setError(null);
    try {
      const result = await promptForgeApi.confirmAgentRuntimeFlash(current.run_id, {
        port: options.port ?? undefined,
        board_type: options.boardType ?? undefined,
        environment: options.environment ?? undefined,
        start_monitor_after_flash: options.startMonitorAfterFlash ?? undefined,
      });
      runRef.current = result.run;
      setRun(result.run);
      connect(result.run.run_id);
      return result.run;
    } catch (cause) {
      setError(toErrorMessage(cause, "Flash could not be started."));
      return null;
    }
  }, [connect]);

  const applyChanges = useCallback(async () => {
    const current = runRef.current;
    if (!current?.change_set_id || applyingChanges) return null;
    setApplyingChanges(true);
    setError(null);
    try {
      const result = await promptForgeApi.applyAgentRuntimeChanges(current.run_id);
      runRef.current = result.run;
      setRun(result.run);
      await refreshGraph(result.run.run_id);
      const sessionId = activeSessionRef.current?.session_id;
      if (sessionId) await refreshSession(sessionId).catch(() => null);
      return result.run;
    } catch (cause) {
      setError(toErrorMessage(cause, "The staged changes could not be applied."));
      return null;
    } finally {
      setApplyingChanges(false);
    }
  }, [applyingChanges, refreshGraph, refreshSession]);

  useEffect(() => {
    activeSessionRef.current = activeSession;
  }, [activeSession]);

  useEffect(() => {
    const runId = run?.run_id ?? "";
    const status = run?.status ?? "";
    if (announcedRun.current.key !== runId) announcedRun.current = { key: runId, status: "" };
    if (!status || announcedRun.current.status === status) return;
    announcedRun.current.status = status;
    if (!stable(status)) {
      announce("Forge started working");
      return;
    }
    announce(status === "completed" ? "Forge finished" : "Forge needs attention");
  }, [announce, run]);

  useEffect(() => {
    void refreshProviders();
  }, [refreshProviders]);

  useEffect(() => {
    stopStream();
    stopActivityStream();
    clearActivity(true);
    setActiveSession(null);
    activeSessionRef.current = null;
    setRun(null);
    runRef.current = null;
    setEvents([]);
      setOptimisticMessages([]);
      setStreamingMessage(null);
      pendingDeltaText.current = {};
    appliedDeltaSequences.current = {};
    processedRunEvents.current.clear();
    announcedRun.current = { key: "", status: "" };
    setGraph(null);
    if (!projectId) {
      setSessions([]);
      return;
    }
    let disposed = false;
    setRestoring(true);
    void refreshSessions()
      .then((loaded) => {
        if (disposed) return;
        const saved = window.localStorage.getItem(activeSessionKey(projectId));
        const selected = loaded.find((session) => session.session_id === saved) ?? loaded[0] ?? null;
        if (selected) {
          void resumeSession(selected.session_id);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (!disposed) setRestoring(false);
      });
    return () => {
      disposed = true;
      stopStream();
      stopActivityStream();
    };
  }, [clearActivity, projectId, refreshSessions, resumeSession, stopActivityStream, stopStream]);

  useEffect(() => () => {
    stopStream();
    stopActivityStream();
    cancelActivityClear();
  }, [cancelActivityClear, stopActivityStream, stopStream]);

  return {
    enabled,
    providers,
    routeableProviders,
    defaultProviderId,
    sessions,
    activeSession,
    messages,
    run,
    events,
    graph,
    currentActivity,
    submitting,
    cancelling,
    applyingChanges,
    restoring,
    connectionState,
    error,
    refreshProviders,
    refreshSessions,
    refreshSession,
    createSession,
    resumeSession,
    sendMessage,
    confirmFlash,
    applyChanges,
    cancel,
  };
}
