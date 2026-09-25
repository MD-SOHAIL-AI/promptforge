"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { mergeActivityEvents } from "@/lib/activity-stream";
import { promptForgeApi } from "@/lib/api";
import {
  RUN_POLL_INTERVAL_MS,
  SSE_OPEN_FALLBACK_MS,
  isStableRunStatus,
  shouldPollAfterSseEvent,
  shouldStartPollingAfterSseDisconnect,
} from "@/lib/agent-stream-policy";
import { emitRuntimeDiagnostic, serializeError } from "@/lib/diagnostics";
import { toErrorMessage } from "@/lib/errors";
import {
  remainingActivityMs,
  shouldAcceptActivity,
  snapshotFromActivityEvent,
  snapshotFromRunEvent,
  type ForgeAgentActivitySnapshot,
} from "@/lib/forge-agent-activity";
import type {
  AgentExecutionGraph,
  AgentGuidanceQueueItem,
  ForgeAgentActivityEvent,
  ForgeAgentSession,
  ProductAgentEvent,
  ProductAgentProvider,
  ProductAgentRun,
} from "@/types";

const stable = isStableRunStatus;

export type ForgeAgentConnectionState =
  "idle" | "connecting" | "live" | "polling";

function activeSessionKey(projectId: string) {
  return `forgex-agent-active-session:${projectId}`;
}

export function useForgeAgentSession(projectId: string | null) {
  const [enabled, setEnabled] = useState(false);
  const [providers, setProviders] = useState<ProductAgentProvider[]>([]);
  const [sessions, setSessions] = useState<ForgeAgentSession[]>([]);
  const [activeSession, setActiveSession] = useState<ForgeAgentSession | null>(
    null,
  );
  const [run, setRun] = useState<ProductAgentRun | null>(null);
  const [events, setEvents] = useState<ProductAgentEvent[]>([]);
  const [graph, setGraph] = useState<AgentExecutionGraph | null>(null);
  const [guidanceQueue, setGuidanceQueue] = useState<AgentGuidanceQueueItem[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [applyingChanges, setApplyingChanges] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [connectionState, setConnectionState] =
    useState<ForgeAgentConnectionState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [currentActivity, setCurrentActivity] =
    useState<ForgeAgentActivitySnapshot | null>(null);
  const source = useRef<EventSource | null>(null);
  const activitySource = useRef<EventSource | null>(null);
  const poll = useRef<number | null>(null);
  const streamOpenFallback = useRef<number | null>(null);
  const activitySequences = useRef<Record<string, number>>({});
  const activityClearTimer = useRef<number | null>(null);
  const runRef = useRef<ProductAgentRun | null>(null);
  const activeSessionRef = useRef<ForgeAgentSession | null>(null);
  const selectedSessionRef = useRef<string | null>(null);
  const projectRef = useRef(projectId);
  projectRef.current = projectId;

  const routeableProviders = useMemo(
    () => providers.filter((provider) => provider.routeable),
    [providers],
  );
  const defaultProviderId = useMemo(
    () =>
      routeableProviders.find((provider) => provider.selected)?.provider_id ??
      routeableProviders.find((provider) => provider.kind === "api_planner")
        ?.provider_id ??
      routeableProviders[0]?.provider_id ??
      "",
    [routeableProviders],
  );

  const stopPolling = useCallback(() => {
    if (poll.current !== null) window.clearInterval(poll.current);
    poll.current = null;
  }, []);

  const stopStreamOpenFallback = useCallback(() => {
    if (streamOpenFallback.current !== null)
      window.clearTimeout(streamOpenFallback.current);
    streamOpenFallback.current = null;
  }, []);

  const stopStream = useCallback(() => {
    source.current?.close();
    source.current = null;
    stopStreamOpenFallback();
    stopPolling();
    setConnectionState("idle");
  }, [stopPolling, stopStreamOpenFallback]);

  const cancelActivityClear = useCallback(() => {
    if (activityClearTimer.current !== null)
      window.clearTimeout(activityClearTimer.current);
    activityClearTimer.current = null;
  }, []);

  const clearActivity = useCallback(
    (force = false) => {
      cancelActivityClear();
      setCurrentActivity((current) => {
        if (!current || force) return null;
        const remaining = remainingActivityMs(current.startedAt);
        if (remaining <= 0) return null;
        activityClearTimer.current = window.setTimeout(
          () => setCurrentActivity(null),
          remaining,
        );
        return current;
      });
      return null;
    },
    [cancelActivityClear],
  );

  const showActivity = useCallback(
    (snapshot: ForgeAgentActivitySnapshot | null) => {
      if (!snapshot) return;
      const sessionId = activeSessionRef.current?.session_id;
      const runId = runRef.current?.run_id;
      if (
        !shouldAcceptActivity(snapshot, {
          activeSessionId: sessionId,
          activeRunId: snapshot.runId ? runId : null,
        })
      )
        return;
      cancelActivityClear();
      setCurrentActivity(snapshot);
    },
    [cancelActivityClear],
  );

  const handleActivityEvent = useCallback(
    (event: ForgeAgentActivityEvent) => {
      if (!event.session_id) return;
      activitySequences.current[event.session_id] = Math.max(
        activitySequences.current[event.session_id] ?? 0,
        Number(event.sequence) || 0,
      );
      if (
        event.phase === "completed" ||
        event.event_type === "activity.completed"
      ) {
        clearActivity();
        return;
      }
      showActivity(snapshotFromActivityEvent(event));
    },
    [clearActivity, showActivity],
  );

  const stopActivityStream = useCallback(() => {
    activitySource.current?.close();
    activitySource.current = null;
  }, []);

  const connectActivity = useCallback(
    (sessionId: string) => {
      if (!sessionId) return;
      const currentUrl = activitySource.current
        ? (activitySource.current as EventSource & { url?: string }).url
        : "";
      if (
        currentUrl.includes(`/sessions/${encodeURIComponent(sessionId)}/events`)
      )
        return;
      stopActivityStream();
      const after = 0; // Replay persisted snapshots when reopening a session.
      const stream = new EventSource(
        promptForgeApi.agentRuntimeSessionEventsUrl(sessionId, after),
      );
      activitySource.current = stream;
      stream.onmessage = (message) => {
        try {
          if (
            activitySource.current !== stream ||
            activeSessionRef.current?.session_id !== sessionId
          )
            return;
          const event = JSON.parse(message.data) as ForgeAgentActivityEvent &
            ProductAgentEvent;
          activitySequences.current[sessionId] = Math.max(
            activitySequences.current[sessionId] ?? 0,
            Number(event.sequence) || 0,
          );
          if (event.activity && event.label) handleActivityEvent(event);
          if (
            activitySource.current !== stream ||
            activeSessionRef.current?.session_id !== sessionId
          )
            return;
          if (event.run_id) {
            setEvents((current) => mergeActivityEvents(current, event));
            showActivity(snapshotFromRunEvent(event, sessionId));
            if (event.node_id || event.event_type.startsWith("node.")) {
              void promptForgeApi
                .agentRuntimeGraph(event.run_id)
                .then((result) => setGraph(result.graph))
                .catch(() => null);
            }
          }
        } catch {
          // Activity is presentation-only; malformed transient updates are ignored.
        }
      };
      stream.onerror = () => {
        // EventSource reconnects with Last-Event-ID; retain the subscription.
      };
    },
    [handleActivityEvent, showActivity, stopActivityStream],
  );

  const refreshGraph = useCallback(async (runId: string) => {
    try {
      const result = await promptForgeApi.agentRuntimeGraph(runId);
      if (runRef.current?.run_id !== runId) return result.graph;
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
    const result = await promptForgeApi.agentRuntimeSessions(projectId);
    if (projectRef.current !== projectId) return [];
    const next = Array.isArray(result.sessions) ? result.sessions : [];
    setSessions(next);
    return next;
  }, [projectId]);

  const refreshSession = useCallback(
    async (sessionId: string) => {
      const result = await promptForgeApi.agentRuntimeSession(sessionId);
      if (
        projectRef.current !== projectId ||
        (selectedSessionRef.current && selectedSessionRef.current !== sessionId)
      )
        return result.session;
      activeSessionRef.current = result.session;
      setActiveSession(result.session);
      connectActivity(result.session.session_id);
      if (projectId)
        window.localStorage.setItem(
          activeSessionKey(projectId),
          result.session.session_id,
        );
      void refreshSessions().catch(() => null);
      return result.session;
    },
    [connectActivity, projectId, refreshSessions],
  );

  const createSession = useCallback(
    async (title?: string | null) => {
      if (!projectId) return null;
      const created = await promptForgeApi.createAgentRuntimeSession({
        project_id: projectId,
        title,
      });
      if (projectRef.current !== projectId) return null;
      selectedSessionRef.current = created.session.session_id;
      activeSessionRef.current = created.session;
      setActiveSession(created.session);
      connectActivity(created.session.session_id);
      setRun(null);
      runRef.current = null;
      setEvents([]);
      setGraph(null);
      setGuidanceQueue([]);
      stopStream();
      window.localStorage.setItem(
        activeSessionKey(projectId),
        created.session.session_id,
      );
      void refreshSessions().catch(() => null);
      return created.session;
    },
    [connectActivity, projectId, refreshSessions, stopStream],
  );

  const refreshGuidanceQueue = useCallback(async (runId?: string | null) => {
    const target = runId ?? runRef.current?.run_id;
    if (!target) {
      setGuidanceQueue([]);
      return [];
    }
    try {
      const result = await promptForgeApi.agentRuntimeGuidanceQueue(target);
      if (runRef.current?.run_id === target || !runRef.current)
        setGuidanceQueue(Array.isArray(result.items) ? result.items : []);
      return result.items ?? [];
    } catch {
      return [];
    }
  }, []);

  const editGuidance = useCallback(async (itemId: string, content: string) => {
    const target = runRef.current?.run_id;
    if (!target) return null;
    try {
      const result = await promptForgeApi.editAgentRuntimeGuidance(target, itemId, content);
      await refreshGuidanceQueue(target);
      return result.item;
    } catch (cause) {
      setError(toErrorMessage(cause, "Queued guidance could not be edited."));
      await refreshGuidanceQueue(target);
      return null;
    }
  }, [refreshGuidanceQueue]);

  const removeGuidance = useCallback(async (itemId: string) => {
    const target = runRef.current?.run_id;
    if (!target) return null;
    try {
      const result = await promptForgeApi.removeAgentRuntimeGuidance(target, itemId);
      await refreshGuidanceQueue(target);
      return result.item;
    } catch (cause) {
      setError(toErrorMessage(cause, "Queued guidance could not be removed."));
      await refreshGuidanceQueue(target);
      return null;
    }
  }, [refreshGuidanceQueue]);

  const refreshRun = useCallback(
    async (runId: string) => {
      const sessionIdAtRequest = activeSessionRef.current?.session_id;
      const result = await promptForgeApi.agentRuntimeRun(runId);
      if (
        activeSessionRef.current?.session_id !== sessionIdAtRequest ||
        (selectedSessionRef.current &&
          selectedSessionRef.current !== sessionIdAtRequest)
      )
        return result.run;
      runRef.current = result.run;
      setRun(result.run);
      void refreshGraph(runId);
      void refreshGuidanceQueue(runId);
      setError(null);
      if (stable(result.run.status)) {
        stopStream();
        clearActivity();
        const sessionId = activeSessionRef.current?.session_id;
        if (sessionId) {
          window.setTimeout(
            () => void refreshSession(sessionId).catch(() => null),
            150,
          );
        }
      }
      return result.run;
    },
    [clearActivity, refreshGraph, refreshGuidanceQueue, refreshSession, stopStream],
  );

  const startPolling = useCallback(
    (runId: string) => {
      if (poll.current !== null) return;
      setConnectionState((current) =>
        current === "live" ? current : "polling",
      );
      poll.current = window.setInterval(() => {
        void refreshRun(runId).catch((cause) => {
          setConnectionState("polling");
          setError(
            toErrorMessage(cause, "Agent status could not be refreshed."),
          );
        });
      }, RUN_POLL_INTERVAL_MS);
    },
    [refreshRun],
  );

  const connect = useCallback(
    (runId: string) => {
      stopStream();
      setConnectionState("connecting");
      streamOpenFallback.current = window.setTimeout(() => {
        streamOpenFallback.current = null;
        if (source.current !== null) startPolling(runId);
      }, SSE_OPEN_FALLBACK_MS);
      const stream = new EventSource(
        promptForgeApi.agentRuntimeEventsUrl(runId),
      );
      source.current = stream;
      stream.onopen = () => {
        stopStreamOpenFallback();
        stopPolling();
        setConnectionState("live");
      };
      stream.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as ProductAgentEvent;
          if (source.current !== stream || event.run_id !== runId) return;
          setEvents((current) => mergeActivityEvents(current, event));
          if (event.event_type.startsWith("agent.inbox."))
            void refreshGuidanceQueue(runId);
          showActivity(
            snapshotFromRunEvent(event, activeSessionRef.current?.session_id),
          );
          if (
            event.event_type.startsWith("runtime.") &&
            typeof event.status === "string"
          ) {
            setRun((current) => {
              if (!current || current.run_id !== runId) return current;
              const next = {
                ...current,
                status: event.status as ProductAgentRun["status"],
              };
              runRef.current = next;
              return next;
            });
          }
          if (event.node_id || event.event_type.startsWith("node.")) {
            void refreshGraph(runId).catch(() => null);
          }
          if (
            event.event_type.startsWith("runtime.") &&
            shouldPollAfterSseEvent(event.status)
          ) {
            void refreshRun(runId).catch(() => setConnectionState("polling"));
          }
        } catch {
          setConnectionState("polling");
        }
      };
      stream.onerror = () => {
        if (source.current !== stream) return;
        if (stable(runRef.current?.status)) {
          stream.close();
          source.current = null;
        }
        stopStreamOpenFallback();
        if (shouldStartPollingAfterSseDisconnect(runRef.current?.status)) {
          setConnectionState("polling");
          startPolling(runId);
        }
      };
    },
    [
      refreshGraph,
      refreshGuidanceQueue,
      refreshRun,
      showActivity,
      startPolling,
      stopPolling,
      stopStream,
      stopStreamOpenFallback,
    ],
  );

  const resumeSession = useCallback(
    async (sessionId: string) => {
      selectedSessionRef.current = sessionId;
      stopStream();
      setRestoring(true);
      setError(null);
      setEvents([]);
      stopActivityStream();
      try {
        const session = await refreshSession(sessionId);
        if (
          selectedSessionRef.current !== sessionId ||
          session.project_id !== projectRef.current
        )
          return null;
        if (session.active_run_id) {
          const nextRun = await refreshRun(session.active_run_id);
          if (
            selectedSessionRef.current !== sessionId ||
            session.project_id !== projectRef.current
          )
            return null;
          connect(nextRun.run_id);
        } else {
          setRun(null);
          setEvents([]);
          stopStream();
          clearActivity(true);
        }
        return session;
      } catch (cause) {
        setError(toErrorMessage(cause, "Agent session could not be loaded."));
        return null;
      } finally {
        setRestoring(false);
      }
    },
    [
      clearActivity,
      connect,
      refreshRun,
      refreshSession,
      stopStream,
      stopActivityStream,
    ],
  );

  const ensureSession = useCallback(async () => {
    if (!projectId) return null;
    if (activeSessionRef.current?.project_id === projectId)
      return activeSessionRef.current;
    return createSession();
  }, [createSession, projectId]);

  const sendMessage = useCallback(
    async (
      content: string,
      providerId?: string,
      options: {
        autonomy?:
          | "auto"
          | "read_only"
          | "staged_changes"
          | "plan_only"
          | "build_only_strict"
          | "build_only"
          | "simulate"
          | "build_then_confirm_flash";
        boardPort?: string | null;
        boardType?: string | null;
        environment?: string | null;
        startMonitorAfterFlash?: boolean;
        approvedPlanId?: string | null;
        approvedPlanRevision?: number | null;
        approvedPlanHash?: string | null;
      } = {},
    ) => {
      if (!projectId || submitting) return null;
      const prompt = content.trim();
      if (!prompt) return null;
      setSubmitting(true);
      setError(null);
      try {
        const session = await ensureSession();
        if (!session) return null;
        if (
          projectRef.current !== projectId ||
          selectedSessionRef.current !== session.session_id
        )
          return null;
        connectActivity(session.session_id);
        const result = await promptForgeApi.sendAgentRuntimeSessionMessage(
          session.session_id,
          {
            content: prompt,
            provider_id: providerId ?? activeSessionRef.current?.provider_id ?? null,
            model_id: activeSessionRef.current?.model_id ?? null,
            autonomy: options.autonomy ?? "auto",
            board_port: options.boardPort ?? undefined,
            board_type: options.boardType ?? undefined,
            environment: options.environment ?? undefined,
            start_monitor_after_flash: options.startMonitorAfterFlash ?? false,
            approved_plan_id: options.approvedPlanId ?? undefined,
            approved_plan_revision: options.approvedPlanRevision ?? undefined,
            approved_plan_hash: options.approvedPlanHash ?? undefined,
          },
        );
        if (
          projectRef.current !== projectId ||
          selectedSessionRef.current !== session.session_id
        )
          return result;
        activeSessionRef.current = result.session;
        setActiveSession(result.session);
        for (const event of result.activity_events ?? [])
          handleActivityEvent(event);
        void refreshSessions().catch(() => null);
        if (result.run) {
          runRef.current = result.run;
          setRun(result.run);
          void refreshGraph(result.run.run_id);
          void refreshGuidanceQueue(result.run.run_id);
          connect(result.run.run_id);
        } else {
          runRef.current = null;
          setRun(null);
          setGraph(null);
          stopStream();
          clearActivity();
        }
        return result;
      } catch (cause) {
        emitRuntimeDiagnostic({
          component: "renderer",
          event: "renderer.error",
          level: "ERROR",
          operation: "sendAgentMessage",
          project_id: projectId,
          message: toErrorMessage(cause, "Agent message could not be sent."),
          error: serializeError(cause),
        });
        setError(toErrorMessage(cause, "Agent message could not be sent."));
        return null;
      } finally {
        setSubmitting(false);
      }
    },
    [
      clearActivity,
      connect,
      connectActivity,
      ensureSession,
      handleActivityEvent,
      projectId,
      refreshGraph,
      refreshGuidanceQueue,
      refreshSessions,
      stopStream,
      submitting,
    ],
  );

  const cancel = useCallback(async () => {
    const session = activeSessionRef.current;
    if (!session || cancelling) return null;
    setCancelling(true);
    try {
      const result = await promptForgeApi.cancelAgentRuntimeSession(
        session.session_id,
      );
      if (selectedSessionRef.current !== session.session_id) return result;
      activeSessionRef.current = result.session;
      setActiveSession(result.session);
      for (const event of result.activity_events ?? [])
        handleActivityEvent(event);
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

  const confirmFlash = useCallback(
    async (
      options: {
        port?: string | null;
        boardType?: string | null;
        environment?: string | null;
        startMonitorAfterFlash?: boolean | null;
      } = {},
    ) => {
      const current = runRef.current;
      if (!current || current.status !== "awaiting_flash_confirmation")
        return null;
      setError(null);
      try {
        const result = await promptForgeApi.confirmAgentRuntimeFlash(
          current.run_id,
          {
            port: options.port ?? undefined,
            board_type: options.boardType ?? undefined,
            environment: options.environment ?? undefined,
            start_monitor_after_flash:
              options.startMonitorAfterFlash ?? undefined,
          },
        );
        if (runRef.current?.run_id !== current.run_id) return result.run;
        runRef.current = result.run;
        setRun(result.run);
        connect(result.run.run_id);
        return result.run;
      } catch (cause) {
        setError(toErrorMessage(cause, "Flash could not be started."));
        return null;
      }
    },
    [connect],
  );

  const retry = useCallback(async () => {
    const current = runRef.current;
    if (!current || current.status !== "paused_model_unavailable") return null;
    setError(null);
    try {
      const result = await promptForgeApi.retryAgentRuntimeRun(current.run_id);
      runRef.current = result.run;
      setRun(result.run);
      const sessionId = activeSessionRef.current?.session_id;
      if (sessionId) await refreshSession(sessionId).catch(() => null);
      connect(result.run.run_id);
      return result.run;
    } catch (cause) {
      setError(toErrorMessage(cause, "The selected model could not be retried yet."));
      return null;
    }
  }, [connect, refreshSession]);

  const applyChanges = useCallback(async () => {
    const current = runRef.current;
    if (!current?.change_set_id || applyingChanges) return null;
    setApplyingChanges(true);
    setError(null);
    try {
      const result = await promptForgeApi.applyAgentRuntimeChanges(
        current.run_id,
      );
      if (runRef.current?.run_id !== current.run_id) return result.run;
      runRef.current = result.run;
      setRun(result.run);
      await refreshGraph(result.run.run_id);
      const sessionId = activeSessionRef.current?.session_id;
      if (sessionId) await refreshSession(sessionId).catch(() => null);
      return result.run;
    } catch (cause) {
      setError(
        toErrorMessage(cause, "The staged changes could not be applied."),
      );
      return null;
    } finally {
      setApplyingChanges(false);
    }
  }, [applyingChanges, refreshGraph, refreshSession]);

  useEffect(() => {
    activeSessionRef.current = activeSession;
  }, [activeSession]);

  useEffect(() => {
    void refreshProviders();
  }, [refreshProviders]);

  useEffect(() => {
    stopStream();
    stopActivityStream();
    clearActivity(true);
    setActiveSession(null);
    selectedSessionRef.current = null;
    activeSessionRef.current = null;
    setRun(null);
    runRef.current = null;
    setEvents([]);
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
        const selected =
          loaded.find((session) => session.session_id === saved) ??
          loaded[0] ??
          null;
        if (selected) {
          void resumeSession(selected.session_id);
        }
      })
      .finally(() => {
        if (!disposed) setRestoring(false);
      });
    return () => {
      disposed = true;
      stopStream();
      stopActivityStream();
    };
  }, [
    clearActivity,
    projectId,
    refreshSessions,
    resumeSession,
    stopActivityStream,
    stopStream,
  ]);

  useEffect(
    () => () => {
      stopStream();
      stopActivityStream();
      cancelActivityClear();
    },
    [cancelActivityClear, stopActivityStream, stopStream],
  );

  return {
    enabled,
    providers,
    routeableProviders,
    defaultProviderId,
    sessions,
    activeSession,
    messages: activeSession?.messages ?? [],
    run,
    events,
    graph,
    guidanceQueue,
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
    refreshGuidanceQueue,
    editGuidance,
    removeGuidance,
    confirmFlash,
    retry,
    applyChanges,
    cancel,
  };
}
