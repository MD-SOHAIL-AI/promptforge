"use client";

import { CheckCircle2, Code2, FileDiff, Loader2, PlayCircle, RefreshCw, Settings2, ShieldCheck, Sparkles, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  CodingWorkflowApiError,
  codingWorkflowApi,
  codingWorkflowErrorMessage,
} from "@/lib/coding-workflow-api";
import type {
  CodingWorkflowActionResult,
  CodingWorkflowApiStatus,
  CodingWorkflowContextFileItem,
  CodingWorkflowContextPreview,
  CodingWorkflowEvent,
  CodingWorkflowProviderMode,
  CodingWorkflowRun,
  CodingWorkflowStaleLock,
  CodingWorkflowStaleRun,
  CodingWorkflowStatus,
  ModelProviderResponse,
  ModelRouteResponse,
} from "@/types";

export interface UnifiedCodingWorkflowPanelProps {
  workspacePath?: string | null;
  projectName?: string | null;
  defaultPort?: string | null;
  defaultBoardId?: string | null;
  modelProviders?: ModelProviderResponse[];
  modelRoutes?: ModelRouteResponse[];
  onOpenModels?: () => void;
  onLog?: (entry: { channel: "system" | "workflow" | "build" | "serial" | "error"; message: string }) => void;
}

type WorkflowBusy =
  | "runs"
  | "events"
  | "apiStatus"
  | "generate"
  | "files"
  | "preview"
  | "apply"
  | "build"
  | "flash"
  | "monitor"
  | "repair"
  | "cancel"
  | "recovery"
  | null;

const STAGE_LABELS: Record<string, string> = {
  awaiting_apply: "awaiting apply",
  awaiting_build: "awaiting build",
  awaiting_flash: "awaiting flash",
  awaiting_monitor: "awaiting monitor",
  applying: "Applying...",
  building: "Building...",
  flashing: "Flashing...",
  monitoring: "Monitoring...",
  repairing: "Repairing...",
  cancelling: "Cancelling...",
  cancelled: "cancelled",
};
const CANCELLABLE_STATUSES = new Set(["awaiting_apply", "awaiting_build", "awaiting_flash", "awaiting_monitor", "failed"]);

export function UnifiedCodingWorkflowPanel({
  workspacePath,
  projectName,
  defaultPort,
  defaultBoardId,
  modelProviders = [],
  modelRoutes = [],
  onOpenModels,
  onLog,
}: UnifiedCodingWorkflowPanelProps) {
  const [providerMode, setProviderMode] = useState<CodingWorkflowProviderMode>("api");
  const [prompt, setPrompt] = useState("");
  const [contextMode, setContextMode] = useState("project_summary");
  const [apiProviderId, setApiProviderId] = useState("");
  const [apiModel, setApiModel] = useState("");
  const [contextFiles, setContextFiles] = useState<CodingWorkflowContextFileItem[]>([]);
  const [fileSearch, setFileSearch] = useState("");
  const [selectedFilePaths, setSelectedFilePaths] = useState<string[]>(["platformio.ini", "src/main.cpp"]);
  const [liveApiConfirmed, setLiveApiConfirmed] = useState(false);
  const [port, setPort] = useState(defaultPort ?? "COM7");
  const [boardId, setBoardId] = useState(defaultBoardId ?? "esp32dev");
  const [baudRate, setBaudRate] = useState(115200);
  const [durationSeconds, setDurationSeconds] = useState(5);
  const [maxOutputBytes, setMaxOutputBytes] = useState(16384);
  const [runs, setRuns] = useState<CodingWorkflowRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<CodingWorkflowRun | null>(null);
  const [events, setEvents] = useState<CodingWorkflowEvent[]>([]);
  const [apiStatus, setApiStatus] = useState<CodingWorkflowApiStatus | null>(null);
  const [contextPreview, setContextPreview] = useState<CodingWorkflowContextPreview | null>(null);
  const [lastResult, setLastResult] = useState<CodingWorkflowActionResult | null>(null);
  const [staleRuns, setStaleRuns] = useState<CodingWorkflowStaleRun[]>([]);
  const [staleLocks, setStaleLocks] = useState<CodingWorkflowStaleLock[]>([]);
  const [busy, setBusy] = useState<WorkflowBusy>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (defaultPort && (!port || port === "COM7")) setPort(defaultPort);
  }, [defaultPort, port]);

  useEffect(() => {
    if (defaultBoardId && (!boardId || boardId === "esp32dev")) setBoardId(defaultBoardId);
  }, [boardId, defaultBoardId]);

  useEffect(() => {
    if (providerMode === "api" && contextMode === "file_tree_only") setContextMode("project_summary");
    if (providerMode === "fake" && contextMode === "project_summary") setContextMode("selected_files");
  }, [contextMode, providerMode]);

  useEffect(() => {
    void refreshRuns(false);
    // Initial dev panel hydration only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const status = selectedRun?.status ?? lastResult?.status ?? "idle";
  const selectedRunId = selectedRun?.run_id ?? lastResult?.run_id ?? null;
  const filesChanged = selectedRun?.files_changed ?? lastResult?.files_changed ?? [];
  const monitorPreview = lastResult?.output_preview || stringMetadata(selectedRun, "monitor_output_preview");
  const actionBusy = busy === "apply" || busy === "build" || busy === "flash" || busy === "monitor" || busy === "repair" || busy === "cancel" || busy === "recovery";
  const canApply = !actionBusy && selectedRun?.status === "awaiting_apply";
  const canBuild = !actionBusy && selectedRun?.status === "awaiting_build";
  const canFlash = !actionBusy && selectedRun?.status === "awaiting_flash";
  const canMonitor = !actionBusy && selectedRun?.status === "awaiting_monitor";
  const selectedRunHasBuildFailure = selectedRun?.status === "failed" && Boolean(selectedRun.failure_code?.includes("BUILD"));
  const repairChildren = useMemo(
    () => selectedRun ? runs.filter((run) => stringMetadata(run, "repair_of_run_id") === selectedRun.run_id) : [],
    [runs, selectedRun],
  );
  const repairAttemptNumber = numberMetadata(selectedRun, "repair_attempt_number");
  const repairAttemptLimit = 2;
  const repairDisabledReason = repairReason(selectedRun, apiStatus);
  const canRepairBuild = !actionBusy && selectedRunHasBuildFailure && repairDisabledReason === null;
  const selectedRunCancellable = Boolean(selectedRun && CANCELLABLE_STATUSES.has(String(selectedRun.status)) && selectedRun.status !== "completed" && selectedRun.status !== "cancelled");
  const canCancel = !actionBusy && selectedRunCancellable;
  const apiSelectedFilesMissing = providerMode === "api" && contextMode === "selected_files" && selectedFilePaths.length === 0;
  const apiStatusBlocksGenerate = providerMode === "api" && (apiStatus?.ready !== true || !liveApiConfirmed || apiSelectedFilesMissing);
  const selectedRunIsMidStage = Boolean(
    selectedRun && !["completed", "failed", "cancelled", "rejected"].includes(String(selectedRun.status)),
  );
  const hasWorkspace = Boolean(workspacePath?.trim());
  const workspaceLabel = hasWorkspace ? projectName || "Active workspace selected" : "No workspace selected";
  const workspaceBody = useMemo(
    () => workspacePath?.trim() ? { workspace_path: workspacePath.trim() } : {},
    [workspacePath],
  );
  const selectedFiles = useMemo(() => selectedFilePaths.filter(Boolean), [selectedFilePaths]);
  const filteredContextFiles = useMemo(() => {
    const query = fileSearch.trim().toLowerCase();
    const files = query
      ? contextFiles.filter((file) => `${file.path} ${file.kind} ${file.reason ?? ""}`.toLowerCase().includes(query))
      : contextFiles;
    return files.slice(0, 80);
  }, [contextFiles, fileSearch]);
  const readyProviders = useMemo(
    () => modelProviders.filter((provider) =>
      !provider.local
      && provider.configured
      && provider.enabled
      && !["offline", "error", "not_configured", "disabled", "unavailable"].includes(String(provider.health_status ?? "").toLowerCase()),
    ),
    [modelProviders],
  );
  const codingRoute = useMemo(
    () => modelRoutes.find((route) => route.task_type === "code_generation"),
    [modelRoutes],
  );

  useEffect(() => {
    if (providerMode !== "api") return;
    const preferred = readyProviders.find((provider) => provider.provider_id === apiProviderId)
      ?? readyProviders.find((provider) => provider.provider_id === codingRoute?.provider_id)
      ?? readyProviders[0];
    if (!preferred) return;
    if (apiProviderId !== preferred.provider_id) setApiProviderId(preferred.provider_id);
    if (!apiModel.trim() || apiProviderId !== preferred.provider_id) {
      setApiModel(codingRoute?.provider_id === preferred.provider_id ? codingRoute.model_id : preferred.default_model);
    }
  }, [apiModel, apiProviderId, codingRoute, providerMode, readyProviders]);

  useEffect(() => {
    if (providerMode === "api") {
      void refreshApiStatus(false);
    }
    // Fetch when the user switches modes; provider/model can be refreshed explicitly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerMode]);

  useEffect(() => {
    setLiveApiConfirmed(false);
  }, [providerMode, apiProviderId, apiModel, contextMode, selectedFilePaths]);

  const refreshRuns = async (notify = true, showBusy = true) => {
    if (showBusy) setBusy("runs");
    setError(null);
    try {
      const response = await codingWorkflowApi.listCodingWorkflowRuns(25);
      setRuns(response.runs);
      if (!selectedRun && response.runs[0]) {
        setSelectedRun(response.runs[0]);
        void loadEvents(response.runs[0].run_id, false);
      }
      if (notify) setMessage(`Loaded ${response.count} coding workflow run${response.count === 1 ? "" : "s"}.`);
    } catch (err) {
      setError(codingWorkflowErrorMessage(err));
      setRuns([]);
    } finally {
      if (showBusy) setBusy(null);
    }
  };

  const loadRun = async (runId: string, notify = true, showBusy = true) => {
    if (showBusy) setBusy("runs");
    setError(null);
    try {
      const response = await codingWorkflowApi.getCodingWorkflowRun(runId);
      setSelectedRun(response.run);
      setLastResult(null);
      await loadEvents(runId, false);
      if (notify) setMessage(`Loaded run ${shortId(runId)}.`);
    } catch (err) {
      setError(codingWorkflowErrorMessage(err));
    } finally {
      if (showBusy) setBusy(null);
    }
  };

  const loadEvents = async (runId: string, notify = true, showBusy = true) => {
    if (showBusy) setBusy("events");
    try {
      const response = await codingWorkflowApi.getCodingWorkflowEvents(runId);
      setEvents(response.events);
      if (notify) setMessage(`Loaded ${response.count} event${response.count === 1 ? "" : "s"}.`);
    } catch (err) {
      setError(codingWorkflowErrorMessage(err));
      setEvents([]);
    } finally {
      if (showBusy) setBusy(null);
    }
  };

  const syncRunState = async (runId: string) => {
    const [runResponse, eventsResponse, runsResponse] = await Promise.all([
      codingWorkflowApi.getCodingWorkflowRun(runId),
      codingWorkflowApi.getCodingWorkflowEvents(runId),
      codingWorkflowApi.listCodingWorkflowRuns(25),
    ]);
    setSelectedRun(runResponse.run);
    setLastResult(null);
    setEvents(eventsResponse.events);
    setRuns(runsResponse.runs);
    return runResponse.run;
  };

  const refreshAfterOutcome = async (runId: string) => {
    try {
      await syncRunState(runId);
    } catch (err) {
      setError(codingWorkflowErrorMessage(err));
    }
  };

  const latestRunForAction = async (
    runId: string,
    expectedStatus: CodingWorkflowStatus,
  ) => {
    const run = await syncRunState(runId);
    if (run.status !== expectedStatus) {
      setError(`This run is no longer ${STAGE_LABELS[expectedStatus] ?? String(expectedStatus)}. Refreshed latest status: ${run.status}.`);
      setMessage(null);
      return null;
    }
    setError(null);
    setMessage(null);
    return run;
  };

  const handleActionError = async (err: unknown, runId: string) => {
    if (isOperationInProgressError(err)) {
      setError("A workflow operation is already in progress. Refreshing run state...");
    } else if (isWrongStageError(err)) {
      setError("This action is not valid for the current workflow stage. Refreshing run state...");
    } else {
      setError(codingWorkflowErrorMessage(err));
    }
    await refreshAfterOutcome(runId);
  };

  const refreshRecovery = async (notify = true) => {
    setBusy("recovery");
    try {
      const response = await codingWorkflowApi.listStaleCodingWorkflowRuns();
      setStaleRuns(response.runs);
      setStaleLocks(response.stale_locks ?? []);
      setError(null);
      if (notify) setMessage(`Loaded ${response.count} stale workflow candidate${response.count === 1 ? "" : "s"} and ${response.stale_lock_count ?? 0} stale lock${(response.stale_lock_count ?? 0) === 1 ? "" : "s"}.`);
    } catch (err) {
      setError(codingWorkflowErrorMessage(err));
      setStaleRuns([]);
      setStaleLocks([]);
    } finally {
      setBusy(null);
    }
  };

  const loadContextFiles = async () => {
    setBusy("files");
    try {
      const response = await codingWorkflowApi.listCodingWorkflowContextFiles({
        workspace_path: workspacePath?.trim() || undefined,
        max_files: 200,
      });
      setContextFiles(response.files);
      const selectable = new Set(response.files.filter((file) => file.selectable).map((file) => file.path));
      setSelectedFilePaths((current) => current.filter((path) => selectable.has(path)));
      setError(null);
      setMessage(`Loaded ${response.files.length} context file${response.files.length === 1 ? "" : "s"}.`);
    } catch (err) {
      setError(codingWorkflowErrorMessage(err));
      setContextFiles([]);
    } finally {
      setBusy(null);
    }
  };

  const afterAction = async (result: CodingWorkflowActionResult, successMessage: string) => {
    setLastResult(result);
    setMessage(successMessage);
    setError(null);
    onLog?.({ channel: "workflow", message: `[Unified coding workflow] ${successMessage}` });
    await refreshAfterOutcome(result.run_id);
  };

  const refreshApiStatus = async (notify = true) => {
    setBusy("apiStatus");
    try {
      const status = await codingWorkflowApi.getRealApiCodingWorkflowStatus({
        provider_id: apiProviderId.trim() || undefined,
        model: apiModel.trim() || undefined,
      });
      setApiStatus(status);
      setError(null);
      if (notify) setMessage(status.safe_message || (status.ready ? "Real API coding agent is ready." : "Real API coding agent is not ready."));
    } catch (err) {
      setApiStatus(null);
      setError(codingWorkflowErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const runGenerate = async () => {
    if (providerMode === "api" && apiStatus?.ready !== true) {
      setError("Real API coding agent is not ready. Refresh API Status and configure a healthy provider in Model Settings.");
      return;
    }
    if (providerMode === "api" && contextMode === "selected_files" && selectedFiles.length === 0) {
      setError("Select at least one context file or switch to project_summary context.");
      return;
    }
    if (providerMode === "api" && !liveApiConfirmed) {
      setError("Confirm live Real API usage before generating a review.");
      return;
    }
    setBusy("generate");
    try {
      const result = providerMode === "api"
        ? await codingWorkflowApi.generateRealApiCodingWorkflowReview({
            prompt,
            context_mode: contextMode,
            workspace_path: workspacePath?.trim() || undefined,
            selected_files: contextMode === "selected_files" && selectedFiles.length ? selectedFiles : undefined,
            provider_id: apiProviderId.trim() || undefined,
            model: apiModel.trim() || undefined,
            live_api_confirmed: true,
          })
        : await codingWorkflowApi.generateFakeCodingWorkflowReview({
            prompt,
            context_mode: contextMode,
            workspace_path: workspacePath?.trim() || undefined,
          });
      await afterAction(result, `${providerMode === "api" ? "Real API" : "Fake provider"} review generated and paused at apply approval.`);
    } catch (err) {
      setError(codingWorkflowErrorMessage(err));
      if (selectedRunId) await refreshAfterOutcome(selectedRunId);
    } finally {
      setBusy(null);
    }
  };

  const runContextPreview = async () => {
    setBusy("preview");
    try {
      const preview = await codingWorkflowApi.previewCodingWorkflowContext({
        workspace_path: workspacePath?.trim() || undefined,
        prompt,
        context_mode: contextMode,
        selected_files: contextMode === "selected_files" && selectedFiles.length ? selectedFiles : undefined,
      });
      setContextPreview(preview);
      setError(null);
      setMessage(`Previewed ${preview.included_files.length} included context file${preview.included_files.length === 1 ? "" : "s"}.`);
    } catch (err) {
      setError(codingWorkflowErrorMessage(err));
      setContextPreview(null);
    } finally {
      setBusy(null);
    }
  };

  const runApply = async () => {
    if (!selectedRunId) return;
    setBusy("apply");
    try {
      const latest = await latestRunForAction(selectedRunId, "awaiting_apply");
      if (!latest) return;
      const result = await codingWorkflowApi.approveCodingWorkflowApply(selectedRunId, {
        approval_confirmed: true,
        approved_by: "user",
        ...workspaceBody,
      });
      await afterAction(result, "Apply completed and paused before build.");
    } catch (err) {
      await handleActionError(err, selectedRunId);
    } finally {
      setBusy(null);
    }
  };

  const runBuild = async () => {
    if (!selectedRunId) return;
    setBusy("build");
    try {
      const latest = await latestRunForAction(selectedRunId, "awaiting_build");
      if (!latest) return;
      const result = await codingWorkflowApi.runCodingWorkflowBuild(selectedRunId, {
        build_confirmed: true,
        environment: boardId || defaultBoardId || "esp32dev",
        ...workspaceBody,
      });
      await afterAction(result, "Build completed and paused before flash.");
    } catch (err) {
      await handleActionError(err, selectedRunId);
    } finally {
      setBusy(null);
    }
  };

  const runFlash = async () => {
    if (!selectedRunId) return;
    setBusy("flash");
    try {
      const latest = await latestRunForAction(selectedRunId, "awaiting_flash");
      if (!latest) return;
      const result = await codingWorkflowApi.runCodingWorkflowFlash(selectedRunId, {
        flash_confirmed: true,
        port,
        board_id: boardId,
        ...workspaceBody,
      });
      await afterAction(result, "Flash completed and paused before monitor.");
    } catch (err) {
      await handleActionError(err, selectedRunId);
    } finally {
      setBusy(null);
    }
  };

  const runMonitor = async () => {
    if (!selectedRunId) return;
    setBusy("monitor");
    try {
      const latest = await latestRunForAction(selectedRunId, "awaiting_monitor");
      if (!latest) return;
      const result = await codingWorkflowApi.runCodingWorkflowMonitor(selectedRunId, {
        monitor_confirmed: true,
        port: port.trim() || undefined,
        baud_rate: baudRate,
        duration_seconds: durationSeconds,
        max_output_bytes: maxOutputBytes,
      });
      await afterAction(result, "Monitor completed the workflow.");
    } catch (err) {
      await handleActionError(err, selectedRunId);
    } finally {
      setBusy(null);
    }
  };

  const runRepairBuild = async () => {
    if (!selectedRunId) return;
    setBusy("repair");
    try {
      const latest = await syncRunState(selectedRunId);
      if (latest.status !== "failed" || !latest.failure_code?.includes("BUILD")) {
        setError(`Repair is only available after build failure. Refreshed latest status: ${latest.status}.`);
        setMessage(null);
        return;
      }
      const result = await codingWorkflowApi.generateBuildRepairReview(selectedRunId, {
        workspace_path: workspacePath?.trim() || undefined,
        selected_files: contextMode === "selected_files" && selectedFiles.length ? selectedFiles : undefined,
        provider_id: apiProviderId.trim() || undefined,
        model: apiModel.trim() || undefined,
      });
      await afterAction(result, `Build repair review generated for ${shortId(selectedRunId)} and paused at apply approval.`);
    } catch (err) {
      await handleActionError(err, selectedRunId);
    } finally {
      setBusy(null);
    }
  };

  const runCancel = async () => {
    if (!selectedRunId) return;
    if (!window.confirm("Cancel this coding workflow?")) return;
    setBusy("cancel");
    try {
      const result = await codingWorkflowApi.cancelCodingWorkflow(selectedRunId, {
        cancel_confirmed: true,
        reason: "User cancelled from ForgeX UI.",
      });
      await afterAction(result, "Workflow cancelled.");
    } catch (err) {
      await handleActionError(err, selectedRunId);
    } finally {
      setBusy(null);
    }
  };

  const runMarkRecoveredFailed = async (runId: string) => {
    if (!window.confirm("Mark this stale workflow failed?")) return;
    setBusy("recovery");
    try {
      const result = await codingWorkflowApi.markStaleCodingWorkflowFailed(runId, {
        recovery_confirmed: true,
        reason: "Manual stale workflow recovery from ForgeX UI.",
      });
      await afterAction(result, "Stale workflow marked failed.");
      await refreshRecovery(false);
    } catch (err) {
      setError(codingWorkflowErrorMessage(err));
      await refreshAfterOutcome(runId);
    } finally {
      setBusy(null);
    }
  };

  const runClearStaleLock = async (runId: string) => {
    if (!window.confirm("Clear this stale workflow operation lock?")) return;
    setBusy("recovery");
    try {
      const result = await codingWorkflowApi.clearStaleCodingWorkflowLock(runId, {
        clear_lock_confirmed: true,
        reason: "Manual stale workflow lock recovery from ForgeX UI.",
      });
      setMessage(result.safe_message || "Stale lock cleared.");
      setError(null);
      await refreshRecovery(false);
      await refreshAfterOutcome(runId);
    } catch (err) {
      setError(codingWorkflowErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const toggleSelectedFile = (path: string, selected: boolean) => {
    setSelectedFilePaths((current) => {
      if (selected) return current.includes(path) ? current : [...current, path].sort();
      return current.filter((item) => item !== path);
    });
  };

  return (
    <section className="min-w-0 overflow-hidden rounded-2xl border border-[var(--fx-border)] bg-[var(--fx-panel)] shadow-[0_18px_50px_rgba(0,0,0,.18)]">
      <header className="border-b border-[var(--fx-border)] bg-[linear-gradient(135deg,var(--fx-panel-elevated),var(--fx-panel))] px-4 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[var(--fx-accent)]/30 bg-[var(--fx-accent-faint)] text-[var(--fx-accent)]"><Code2 className="h-5 w-5" /></div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-sm font-semibold text-[var(--fx-text)]">Coding Agent <span className="rounded-full border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 py-0.5 text-[9px] font-medium uppercase tracking-wider text-[var(--fx-text-muted)]">Review first</span></div>
              <div className="mt-1 truncate text-[11px] text-[var(--fx-text-muted)]">{workspaceLabel}</div>
            </div>
          </div>
          <button type="button" className="flex h-8 items-center gap-1.5 rounded-lg border border-[var(--fx-border)] bg-[var(--fx-input)] px-2.5 text-[10px] text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)]" onClick={onOpenModels}><Settings2 className="h-3.5 w-3.5" /> Models</button>
        </div>
        <div className="mt-3 grid grid-cols-4 gap-1.5" aria-label="Coding workflow stages">
          {["Review", "Apply", "Build", "Device"].map((step, index) => {
            const activeIndex = ["awaiting_apply", "applying"].includes(String(status)) ? 1 : ["awaiting_build", "building"].includes(String(status)) ? 2 : ["awaiting_flash", "flashing", "awaiting_monitor", "monitoring", "completed"].includes(String(status)) ? 3 : 0;
            return <div key={step} className="min-w-0"><div className={`h-1 rounded-full ${index < activeIndex ? "bg-[var(--fx-success)]" : index === activeIndex ? "bg-[var(--fx-accent)]" : "bg-[var(--fx-border)]"}`} /><div className={`mt-1 text-center text-[9px] ${index === activeIndex ? "text-[var(--fx-text)]" : "text-[var(--fx-text-muted)]"}`}>{step}</div></div>;
          })}
        </div>
      </header>
      <div className="space-y-4 p-3">
          {!hasWorkspace ? (
            <div className="rounded-xl border border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] p-3 text-xs text-[var(--fx-warning)]">
              Open or select a workspace before running the coding agent.
            </div>
          ) : null}

          <div className="space-y-3">
            <div className="space-y-2">
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs font-semibold text-[var(--fx-text)]"><Sparkles className="h-3.5 w-3.5 text-[var(--fx-accent)]" /> Agent mode</div>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { id: "api" as const, label: "Configured model", title: "Use a configured model provider to create a sandboxed review." },
                    { id: "fake" as const, label: "Local test", title: "Use the deterministic development provider without an API call." },
                  ].map((mode) => (
                    <button
                      key={mode.id}
                      type="button"
                      className={`min-h-10 rounded-xl border px-2 py-2 text-center text-[11px] font-medium leading-tight ${
                        providerMode === mode.id
                          ? "border-[var(--fx-accent)] bg-[var(--fx-accent-faint)] text-[var(--fx-accent)]"
                          : "border-[var(--fx-border)] bg-[var(--fx-input)] text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
                      }`}
                      onClick={() => setProviderMode(mode.id)}
                      title={mode.title}
                    >
                      {mode.label}
                    </button>
                  ))}
                </div>
                <div className="rounded-xl border border-[var(--fx-border)] bg-[var(--fx-input)] p-2.5 text-[11px] leading-4 text-[var(--fx-text-muted)]">
                  {providerMode === "api"
                    ? "The model can propose file changes only. ForgeX validates them in a sandbox and pauses for your approval before applying anything."
                    : "Deterministic test mode. No remote model request is made."}
                </div>
                {providerMode === "api" ? (
                  <div className="rounded-xl border border-[var(--fx-border)] bg-[var(--fx-input)] p-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <div className="text-[11px] font-semibold uppercase text-[var(--fx-text-muted)]">Real API Status</div>
                      <button
                        className="flex min-h-8 items-center justify-center gap-1.5 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 py-1 text-[11px] font-medium text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                        onClick={() => void refreshApiStatus()}
                        disabled={Boolean(busy)}
                        title="Refresh API status"
                      >
                        <RefreshCw className={`h-3.5 w-3.5 ${busy === "apiStatus" ? "animate-spin" : ""}`} />
                        <span>Check connection</span>
                      </button>
                    </div>
                    <ApiStatusView status={apiStatus} loading={busy === "apiStatus"} />
                  </div>
                ) : null}
              </div>

              <label className="block text-xs font-semibold text-[var(--fx-text)]" htmlFor="unified-workflow-prompt">
                What should the agent change?
              </label>
              <textarea
                id="unified-workflow-prompt"
                className="min-h-28 w-full resize-y rounded-xl border border-[var(--fx-border)] bg-[var(--fx-input)] px-3 py-2.5 text-xs leading-5 text-[var(--fx-text)] outline-none placeholder:text-[var(--fx-text-muted)] focus:border-[var(--fx-accent)] focus:shadow-[0_0_0_3px_var(--fx-accent-faint)]"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="Describe a firmware change, bug fix, or refactor. The agent will prepare a review before touching your workspace."
              />
              {selectedRunIsMidStage ? (
                <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2 text-[11px] text-[var(--fx-text-muted)]">
                  Generating a new review will create a separate workflow run.
                </div>
              ) : null}
            </div>

            <div className="space-y-2">
              <label className="block text-xs font-semibold text-[var(--fx-text)]" htmlFor="unified-workflow-context">
                Project context
              </label>
              <select
                id="unified-workflow-context"
                className="h-9 w-full rounded-xl border border-[var(--fx-border)] bg-[var(--fx-input)] px-2.5 text-xs text-[var(--fx-text)] outline-none focus:border-[var(--fx-accent)]"
                value={contextMode}
                onChange={(event) => setContextMode(event.target.value)}
              >
                <option value="selected_files">Selected files</option>
                {providerMode === "api" ? (
                  <option value="project_summary">Safe project summary</option>
                ) : (
                  <option value="file_tree_only">File tree only</option>
                )}
              </select>
              {providerMode === "api" && contextMode === "project_summary" ? (
                <div className="text-[11px] text-[var(--fx-text-muted)]">
                  Sends a bounded project summary and key config files.
                </div>
              ) : null}
              {providerMode === "api" ? (
                <div className="space-y-3 rounded-xl border border-[var(--fx-border)] bg-[var(--fx-input)] p-3">
                  <div className="text-xs font-semibold text-[var(--fx-text)]">Model</div>
                  {readyProviders.length > 0 ? (
                    <label className="block text-[10px] font-medium text-[var(--fx-text-muted)]">Provider
                      <select className="mt-1 h-9 w-full rounded-lg border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2.5 text-xs text-[var(--fx-text)] outline-none focus:border-[var(--fx-accent)]" value={apiProviderId} onChange={(event) => setApiProviderId(event.target.value)}>
                        {readyProviders.map((item) => <option key={item.provider_id} value={item.provider_id}>{item.display_name}</option>)}
                      </select>
                    </label>
                  ) : (
                    <button type="button" className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-[var(--fx-warning)]/50 bg-[var(--fx-warning-soft)] px-3 py-2 text-[11px] font-medium text-[var(--fx-warning)]" onClick={onOpenModels}><Settings2 className="h-3.5 w-3.5" /> Configure a model provider</button>
                  )}
                  <TextInput label="Model" value={apiModel} onChange={setApiModel} placeholder="Provider default model" />
                  {contextMode === "selected_files" ? (
                    <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-2">
                      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                        <div className="text-[11px] font-semibold uppercase text-[var(--fx-text-muted)]">
                          Context Files ({selectedFiles.length} selected)
                        </div>
                        <div className="flex flex-wrap gap-1">
                          <button
                            className="flex min-h-8 items-center justify-center gap-1.5 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 py-1 text-[11px] font-medium text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                            onClick={() => void loadContextFiles()}
                            disabled={Boolean(busy) || !hasWorkspace}
                            title="Load selectable context files"
                          >
                            <RefreshCw className={`h-3.5 w-3.5 ${busy === "files" ? "animate-spin" : ""}`} />
                            <span>Load Files</span>
                          </button>
                          <button
                            className="min-h-8 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 py-1 text-[11px] font-medium text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                            onClick={() => setSelectedFilePaths([])}
                            disabled={Boolean(busy) || selectedFiles.length === 0}
                            title="Clear selected files"
                          >
                            Clear
                          </button>
                        </div>
                      </div>
                      <input
                        className="mb-2 h-8 w-full rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-xs text-[var(--fx-text)] outline-none focus:border-[var(--fx-accent)]"
                        value={fileSearch}
                        placeholder="Search files"
                        onChange={(event) => setFileSearch(event.target.value)}
                      />
                      {selectedFiles.length === 0 ? (
                        <div className="mb-2 rounded border border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] p-2 text-[11px] text-[var(--fx-warning)]">
                          Select at least one safe context file or switch to project_summary.
                        </div>
                      ) : null}
                      <div className="max-h-44 space-y-1 overflow-auto">
                        {filteredContextFiles.length === 0 ? (
                          <div className="text-[11px] text-[var(--fx-text-muted)]">Load files to choose context without showing file bodies.</div>
                        ) : (
                          filteredContextFiles.map((file) => (
                            <label
                              key={file.path}
                              className={`flex min-w-0 items-start gap-2 rounded border px-2 py-1 text-[11px] ${
                                file.selectable
                                  ? "border-[var(--fx-border)] bg-[var(--fx-input)] text-[var(--fx-text)]"
                                  : "border-[var(--fx-border)] bg-[var(--fx-panel)] text-[var(--fx-text-muted)] opacity-75"
                              }`}
                            >
                              <input
                                className="mt-0.5"
                                type="checkbox"
                                checked={selectedFilePaths.includes(file.path)}
                                disabled={!file.selectable}
                                onChange={(event) => toggleSelectedFile(file.path, event.target.checked)}
                              />
                              <span className="min-w-0 flex-1">
                                <span className="block break-all text-[var(--fx-code-text)]">{file.path}</span>
                                <span className="text-[var(--fx-text-muted)]">{file.kind} - {file.size_bytes} bytes{file.reason ? ` - ${file.reason}` : ""}</span>
                              </span>
                            </label>
                          ))
                        )}
                      </div>
                    </div>
                  ) : null}
                  <div className="text-[11px] text-[var(--fx-text-muted)]">
                    Only bounded, safe context is sent. Secrets, generated dependencies, hidden control files, and unsafe paths are excluded server-side.
                  </div>
                  <label className="flex items-start gap-2 rounded-xl border border-[var(--fx-warning)]/60 bg-[var(--fx-warning-soft)] p-2.5 text-[11px] text-[var(--fx-warning)]">
                    <input
                      className="mt-0.5"
                      type="checkbox"
                      checked={liveApiConfirmed}
                      onChange={(event) => setLiveApiConfirmed(event.target.checked)}
                    />
                    <span>
                      I understand this may call the selected remote provider. Generate a review only; do not apply changes automatically.
                    </span>
                  </label>
                  {!liveApiConfirmed ? (
                    <div className="text-[11px] text-[var(--fx-text-muted)]">
                      Confirmation resets when provider, model, context mode, or selected files change.
                    </div>
                  ) : null}
                  {apiStatusBlocksGenerate ? (
                    <div className="rounded border border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] p-2 text-[11px] text-[var(--fx-warning)]">
                      {apiStatus?.ready !== true
                        ? "Real API provider readiness is required before generation."
                        : apiSelectedFilesMissing
                          ? "Selected-files context requires at least one selected file."
                          : "Live API confirmation is required before generation."}
                    </div>
                  ) : null}
                  {contextMode !== "selected_files" ? (
                    <div className="text-[11px] text-[var(--fx-text-muted)]">
                      project_summary mode sends bounded summary context and does not require selected files.
                    </div>
                  ) : null}
                  {contextPreview ? null : (
                    <div className="text-[11px] text-[var(--fx-text-muted)]">
                      Preview Context does not call a model provider.
                    </div>
                  )}
                  <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-2">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <div className="text-[11px] font-semibold uppercase text-[var(--fx-text-muted)]">Context Preview</div>
                      <button
                        className="flex min-h-8 items-center justify-center gap-1.5 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 py-1 text-[11px] font-medium text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                        onClick={() => void runContextPreview()}
                        disabled={Boolean(busy) || !hasWorkspace}
                        title="Preview context"
                      >
                        <RefreshCw className={`h-3.5 w-3.5 ${busy === "preview" ? "animate-spin" : ""}`} />
                        <span>{contextPreview ? "Refresh Preview" : "Preview Context"}</span>
                      </button>
                    </div>
                    {contextMode === "project_summary" ? (
                      <div className="mb-2 text-[11px] text-[var(--fx-text-muted)]">
                        ForgeX will send a bounded project summary and safe key config files.
                      </div>
                    ) : null}
                    <ContextPreviewView preview={contextPreview} />
                  </div>
                </div>
              ) : null}
              <div className="grid gap-2">
                <button
                  className="flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-[var(--fx-accent)] bg-[var(--fx-accent)] px-3 py-2.5 text-xs font-semibold text-white shadow-[0_10px_28px_var(--fx-glow)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
                  onClick={() => void runGenerate()}
                  disabled={Boolean(busy) || !prompt.trim() || !hasWorkspace || apiStatusBlocksGenerate}
                  title="Generate review"
                >
                  {busy === "generate" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileDiff className="h-3.5 w-3.5" />}
                  <span>{busy === "generate" ? "Creating safe review…" : "Generate safe review"}</span>
                </button>
                <button
                  className="flex min-h-9 w-full items-center justify-center gap-1.5 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-3 py-2 text-xs font-medium text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                  onClick={() => void refreshRuns()}
                  disabled={Boolean(busy)}
                  title="Refresh recent runs"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${busy === "runs" ? "animate-spin" : ""}`} />
                  <span>Refresh Runs</span>
                </button>
              </div>
            </div>
          </div>

          <div className="space-y-3">
            <div className="min-w-0 space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <StatusField label="Status" value={statusLabel(status)} />
                <StatusField label="Next" value={selectedRun?.next_action ?? lastResult?.next_action ?? "-"} />
                <StatusField label="Run" value={selectedRunId ? shortId(selectedRunId) : "-"} />
                <StatusField label="Review" value={selectedRun?.review_id ?? lastResult?.review_id ?? "-"} />
              </div>
              {selectedRun?.in_progress_stage || selectedRun?.locked || selectedRun?.stale_candidate || selectedRun?.status === "cancelled" ? (
                <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2 text-[11px] text-[var(--fx-text-muted)]">
                  {selectedRun.status === "cancelled" ? "This workflow was cancelled and cannot continue." : null}
                  {selectedRun.in_progress_stage ? ` ${statusLabel(selectedRun.status)} Stage: ${selectedRun.in_progress_stage}.` : null}
                  {selectedRun.locked ? ` Operation lock is active${selectedRun.lock_operation ? ` for ${selectedRun.lock_operation}` : ""}.` : null}
                  {selectedRun.lock_stale ? " Lock is stale and can be reviewed in recovery." : null}
                  {selectedRun.stale_candidate ? " Stale recovery candidate." : null}
                </div>
              ) : null}

              {stringMetadata(selectedRun, "repair_of_run_id") ? (
                <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2 text-[11px] text-[var(--fx-text-muted)]">
                  <div className="font-semibold uppercase">Repair run</div>
                  <div>Repair of: {shortId(stringMetadata(selectedRun, "repair_of_run_id"))}</div>
                  <div>Attempt: {repairAttemptNumber || 1}</div>
                  <div>Reason: {stringMetadata(selectedRun, "repair_reason") || "build_failed"}</div>
                </div>
              ) : null}

              {filesChanged.length > 0 ? (
                <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
                  <div className="mb-1 text-[11px] font-semibold uppercase text-[var(--fx-text-muted)]">Files Changed</div>
                  <div className="flex flex-wrap gap-1">
                    {filesChanged.map((file) => (
                      <span key={file} className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-1.5 py-0.5 text-[11px] text-[var(--fx-code-text)]">
                        {file}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}

              <div className="grid grid-cols-2 gap-2">
                <button className={actionClass(canApply)} onClick={() => void runApply()} disabled={Boolean(busy) || !canApply || !hasWorkspace} title="Approve apply">
                  {busy === "apply" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                  <span>{busy === "apply" ? "Applying..." : "Approve Apply"}</span>
                </button>
                <button className={actionClass(canBuild)} onClick={() => void runBuild()} disabled={Boolean(busy) || !canBuild || !hasWorkspace} title="Run build">
                  {busy === "build" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
                  <span>{busy === "build" ? "Building..." : "Build"}</span>
                </button>
                <button className={actionClass(canFlash)} onClick={() => void runFlash()} disabled={Boolean(busy) || !canFlash || !hasWorkspace || !port.trim() || !boardId.trim()} title="Run flash">
                  {busy === "flash" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
                  <span>{busy === "flash" ? "Flashing..." : "Flash"}</span>
                </button>
                <button className={actionClass(canMonitor)} onClick={() => void runMonitor()} disabled={Boolean(busy) || !canMonitor || !hasWorkspace} title="Run monitor">
                  {busy === "monitor" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                  <span>{busy === "monitor" ? "Monitoring..." : "Monitor"}</span>
                </button>
              </div>
              {selectedRunCancellable || busy === "cancel" ? (
                <button
                  className="flex min-h-9 w-full items-center justify-center gap-1.5 rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] px-3 py-2 text-xs font-medium text-[var(--fx-error)] disabled:opacity-50"
                  onClick={() => void runCancel()}
                  disabled={Boolean(busy) || !canCancel}
                  title="Cancel workflow"
                >
                  {busy === "cancel" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <XCircle className="h-3.5 w-3.5" />}
                  <span>{busy === "cancel" ? "Cancelling..." : "Cancel Workflow"}</span>
                </button>
              ) : null}
              {selectedRunHasBuildFailure ? (
                <div className="rounded border border-[var(--fx-warning)] bg-[var(--fx-input)] p-2">
                  <div className="mb-2 space-y-1 text-[11px] text-[var(--fx-text-muted)]">
                    <div className="font-semibold uppercase text-[var(--fx-warning)]">Build failed</div>
                    <div>Repair available</div>
                    <div>Attempt {repairAttemptNumber || repairChildren.length + 1} of {repairAttemptLimit}</div>
                    {selectedRun?.failure_code ? <div>Code: {selectedRun.failure_code}</div> : null}
                    {selectedRun?.safe_message ? <div className="break-words">{selectedRun.safe_message}</div> : null}
                    {repairDisabledReason ? <div>Disabled: {repairDisabledReason}</div> : null}
                  </div>
                  <button
                    className="flex min-h-9 w-full items-center justify-center gap-1.5 rounded border border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] px-3 py-2 text-xs font-medium text-[var(--fx-warning)] disabled:opacity-50"
                    onClick={() => void runRepairBuild()}
                    disabled={Boolean(busy) || !canRepairBuild || !hasWorkspace}
                    title="Generate build repair review"
                  >
                    {busy === "repair" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileDiff className="h-3.5 w-3.5" />}
                    <span>{busy === "repair" ? "Generating Repair..." : "Generate Build Repair Review"}</span>
                  </button>
                  {repairChildren.length > 0 ? (
                    <div className="mt-2 space-y-1 text-[11px] text-[var(--fx-text-muted)]">
                      <div className="font-semibold uppercase">Recent repair runs</div>
                      {repairChildren.slice(0, 3).map((run) => (
                        <button
                          key={run.run_id}
                          className="block w-full truncate rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 py-1 text-left text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)]"
                          onClick={() => void loadRun(run.run_id)}
                          title={`Open repair run ${run.run_id}`}
                        >
                          {shortId(run.run_id)} - {run.status}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}

              <div className="grid grid-cols-2 gap-2">
                <TextInput label="Port" value={port} onChange={setPort} />
                <TextInput label="Board ID" value={boardId} onChange={setBoardId} />
                <NumberInput label="Baud" value={baudRate} onChange={setBaudRate} min={1} max={4000000} />
                <NumberInput label="Output Bytes" value={maxOutputBytes} onChange={setMaxOutputBytes} min={1} max={16384} />
              </div>
              <div className="max-w-[12rem]">
                <NumberInput label="Monitor Seconds" value={durationSeconds} onChange={setDurationSeconds} min={1} max={10} />
              </div>

              {monitorPreview ? (
                <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
                  <div className="mb-1 text-[11px] font-semibold uppercase text-[var(--fx-text-muted)]">Monitor Output Preview</div>
                  <pre className="max-h-36 overflow-auto whitespace-pre-wrap break-words text-[11px] text-[var(--fx-code-text)]">{monitorPreview}</pre>
                </div>
              ) : null}
            </div>

            <div className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="mb-2 text-[11px] font-semibold uppercase text-[var(--fx-text-muted)]">Recent Runs</div>
              {runs.length === 0 ? (
                <div className="text-xs text-[var(--fx-text-muted)]">No workflow runs recorded.</div>
              ) : (
                <div className="space-y-1">
                  {runs.map((run) => (
                    <button
                      key={run.run_id}
                      className={`w-full rounded border px-2 py-1 text-left text-[11px] ${selectedRun?.run_id === run.run_id ? "border-[var(--fx-accent)] bg-[var(--fx-panel)]" : "border-[var(--fx-border)] bg-[var(--fx-input)] hover:bg-[var(--fx-hover)]"}`}
                      onClick={() => void loadRun(run.run_id)}
                    >
                      <div className="truncate text-[var(--fx-code-text)]">{shortId(run.run_id)} - {run.status}</div>
                      <div className="truncate text-[var(--fx-text-muted)]">{run.provider_id} / {run.review_id ?? "no review"}</div>
                      {stringMetadata(run, "repair_of_run_id") ? (
                        <div className="truncate text-[var(--fx-text-muted)]">repair of {shortId(stringMetadata(run, "repair_of_run_id"))}</div>
                      ) : null}
                      <div className="truncate text-[var(--fx-text-muted)]">{run.next_action ?? "no next action"} - {run.updated_at ?? "unknown time"}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="text-[11px] font-semibold uppercase text-[var(--fx-text-muted)]">Stale Workflow Recovery</div>
                <button
                  className="flex min-h-8 items-center justify-center gap-1.5 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 py-1 text-[11px] font-medium text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                  onClick={() => void refreshRecovery()}
                  disabled={Boolean(busy)}
                  title="Refresh stale recovery candidates"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${busy === "recovery" ? "animate-spin" : ""}`} />
                  <span>Refresh Recovery</span>
                </button>
              </div>
              {staleRuns.length === 0 && staleLocks.length === 0 ? (
                <div className="text-xs text-[var(--fx-text-muted)]">No stale in-progress workflows or locks loaded.</div>
              ) : (
                <div className="space-y-2">
                  {staleRuns.map((run) => (
                    <div key={run.run_id} className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 py-1 text-[11px]">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate text-[var(--fx-code-text)]">{shortId(run.run_id)} - {run.status}</div>
                          <div className="truncate text-[var(--fx-text-muted)]">{run.stage} - {Math.floor(run.age_seconds / 60)} min old</div>
                        </div>
                        <button
                          className="rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] px-2 py-1 text-[11px] font-medium text-[var(--fx-error)] disabled:opacity-50"
                          onClick={() => void runMarkRecoveredFailed(run.run_id)}
                          disabled={Boolean(busy)}
                          title="Mark stale workflow failed"
                        >
                          Mark Failed
                        </button>
                      </div>
                    </div>
                  ))}
                  {staleLocks.map((lock) => (
                    <div key={`${lock.run_id}-${lock.operation}`} className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 py-1 text-[11px]">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate text-[var(--fx-code-text)]">{shortId(lock.run_id)} - stale lock</div>
                          <div className="truncate text-[var(--fx-text-muted)]">{lock.operation} - expires {lock.expires_at}</div>
                        </div>
                        <button
                          className="rounded border border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] px-2 py-1 text-[11px] font-medium text-[var(--fx-warning)] disabled:opacity-50"
                          onClick={() => void runClearStaleLock(lock.run_id)}
                          disabled={Boolean(busy)}
                          title="Clear stale workflow operation lock"
                        >
                          Clear Lock
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="text-[11px] font-semibold uppercase text-[var(--fx-text-muted)]">Event Timeline</div>
              <button
                className="text-[var(--fx-text-muted)] hover:text-[var(--fx-text)] disabled:opacity-50"
                onClick={() => selectedRunId ? void loadEvents(selectedRunId) : undefined}
                disabled={Boolean(busy) || !selectedRunId}
                title="Refresh events"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${busy === "events" ? "animate-spin" : ""}`} />
              </button>
            </div>
            {events.length === 0 ? (
              <div className="text-xs text-[var(--fx-text-muted)]">No events loaded.</div>
            ) : (
              <div className="space-y-1">
                {events.map((event) => (
                  <div key={event.event_id} className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 py-1 text-[11px]">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="font-medium text-[var(--fx-code-text)]">{event.event_type}</span>
                      <span className="text-[var(--fx-text-muted)]">{event.stage}</span>
                      <span className="text-[var(--fx-text-muted)]">{event.status}</span>
                    </div>
                    {event.safe_message ? <div className="mt-0.5 break-words text-[var(--fx-text-muted)]">{event.safe_message}</div> : null}
                    <div className="mt-0.5 truncate text-[var(--fx-text-muted)]">{event.created_at ?? event.timestamp ?? ""}</div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {message ? (
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2 text-xs text-[var(--fx-text-muted)]">{message}</div>
          ) : null}
          {error ? (
            <div className="rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] p-2 text-xs text-[var(--fx-error)]">{error}</div>
          ) : null}
        </div>
    </section>
  );
}

function StatusField({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 py-1.5">
      <div className="text-[10px] uppercase text-[var(--fx-text-muted)]">{label}</div>
      <div className="truncate text-xs text-[var(--fx-code-text)]">{value || "-"}</div>
    </div>
  );
}

function TextInput({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (value: string) => void; placeholder?: string }) {
  return (
    <label className="block min-w-0 text-[11px] uppercase text-[var(--fx-text-muted)]">
      {label}
      <input
        className="mt-1 h-9 w-full min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-xs text-[var(--fx-text)] outline-none focus:border-[var(--fx-accent)]"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function NumberInput({ label, value, onChange, min, max }: { label: string; value: number; onChange: (value: number) => void; min: number; max: number }) {
  return (
    <label className="block min-w-0 text-[11px] uppercase text-[var(--fx-text-muted)]">
      {label}
      <input
        className="mt-1 h-9 w-full min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-xs text-[var(--fx-text)] outline-none focus:border-[var(--fx-accent)]"
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function ContextPreviewView({ preview }: { preview: CodingWorkflowContextPreview | null }) {
  if (!preview) {
    return (
      <div className="text-[11px] text-[var(--fx-text-muted)]">
        Preview context before generating a real API review.
      </div>
    );
  }
  return (
    <div className="space-y-2 text-[11px]">
      <div className="grid grid-cols-2 gap-2">
        <StatusField label="Mode" value={preview.context_mode} />
        <StatusField label="Bytes" value={`${preview.total_bytes}/${preview.limits.max_total_bytes}`} />
        <StatusField label="Files" value={`${preview.included_files.length}/${preview.limits.max_files}`} />
        <StatusField label="Truncated" value={preview.truncated ? "yes" : "no"} />
      </div>
      {preview.included_files.length === 0 ? (
        <div className="rounded border border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] p-2 text-[var(--fx-warning)]">
          No safe files selected for context. Use project_summary or select files.
        </div>
      ) : (
        <div>
          <div className="mb-1 font-semibold uppercase text-[var(--fx-text-muted)]">Included Files</div>
          <div className="space-y-1">
            {preview.included_files.map((file) => (
              <div key={file.path} className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 py-1">
                <span className="break-all text-[var(--fx-code-text)]">{file.path}</span>
                <span className="text-[var(--fx-text-muted)]">{file.kind}</span>
                <span className="text-[var(--fx-text-muted)]">{file.size_bytes} bytes</span>
                {file.truncated ? <span className="text-[var(--fx-warning)]">truncated</span> : null}
              </div>
            ))}
          </div>
        </div>
      )}
      {preview.excluded_files.length > 0 ? (
        <div>
          <div className="mb-1 font-semibold uppercase text-[var(--fx-text-muted)]">Excluded Files</div>
          <div className="max-h-24 space-y-1 overflow-auto">
            {preview.excluded_files.slice(0, 12).map((file, index) => (
              <div key={`${file.path}-${index}`} className="flex flex-wrap gap-x-2 gap-y-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 py-1">
                <span className="break-all text-[var(--fx-code-text)]">{file.path}</span>
                <span className="text-[var(--fx-text-muted)]">{file.reason}</span>
              </div>
            ))}
            {preview.excluded_files.length > 12 ? (
              <div className="text-[var(--fx-text-muted)]">{preview.excluded_files.length - 12} more excluded.</div>
            ) : null}
          </div>
        </div>
      ) : null}
      {preview.tree_summary.length > 0 ? (
        <div>
          <div className="mb-1 font-semibold uppercase text-[var(--fx-text-muted)]">Tree Summary</div>
          <pre className="max-h-24 overflow-auto whitespace-pre-wrap break-words rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2 text-[11px] text-[var(--fx-code-text)]">
            {preview.tree_summary.slice(0, 80).join("\n")}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

function ApiStatusView({ status, loading }: { status: CodingWorkflowApiStatus | null; loading: boolean }) {
  if (loading && !status) {
    return <div className="text-[11px] text-[var(--fx-text-muted)]">Checking provider readiness...</div>;
  }
  if (!status) {
    return (
      <div className="text-[11px] text-[var(--fx-text-muted)]">
        Refresh API Status before generating with the Real API Provider.
      </div>
    );
  }
  return (
    <div className="space-y-2 text-[11px]">
      <div className={`rounded border px-2 py-1.5 ${status.ready ? "border-[var(--fx-success)] bg-[var(--fx-input)] text-[var(--fx-success)]" : "border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] text-[var(--fx-warning)]"}`}>
        {status.ready ? "Ready" : "Not Ready"}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <StatusField label="Provider" value={status.provider_id ?? "-"} />
        <StatusField label="Model" value={status.model ?? "-"} />
        <StatusField label="Router" value={status.model_router_available ? "available" : "unavailable"} />
        <StatusField label="Reason" value={status.reason ?? "-"} />
      </div>
      <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-2 text-[var(--fx-text-muted)]">
        {status.safe_message || (status.ready ? "Real API coding agent is ready." : "Configure a healthy provider in Model Settings.")}
      </div>
      {!status.ready ? (
        <div className="text-[var(--fx-text-muted)]">
          Configure a healthy provider in Model Settings, then refresh API status.
        </div>
      ) : null}
    </div>
  );
}

function actionClass(enabled: boolean) {
  return `flex min-h-9 min-w-0 items-center justify-center gap-1.5 rounded border px-2 py-2 text-center text-xs font-medium leading-tight disabled:opacity-50 ${
    enabled
      ? "border-[var(--fx-accent)] bg-[var(--fx-accent)] text-white hover:opacity-90"
      : "border-[var(--fx-border)] bg-[var(--fx-input)] text-[var(--fx-text-muted)]"
  }`;
}

function shortId(value: string) {
  return value.length > 18 ? `${value.slice(0, 10)}...${value.slice(-6)}` : value;
}

function statusLabel(value: string | null | undefined) {
  if (!value) return "-";
  return STAGE_LABELS[value] ?? value;
}

function stringMetadata(run: CodingWorkflowRun | null, key: string) {
  const value = run?.metadata?.[key];
  return typeof value === "string" ? value : "";
}

function numberMetadata(run: CodingWorkflowRun | null, key: string) {
  const value = run?.metadata?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function repairReason(run: CodingWorkflowRun | null, apiStatus: CodingWorkflowApiStatus | null) {
  if (!run) return "no workflow selected";
  if (apiStatus?.unified_workflow_enabled === false) return "workflow disabled";
  if (apiStatus?.coding_agent_repair_loop_enabled === false) return "repair loop disabled";
  if (apiStatus?.ready === false) return "real API provider not ready";
  if (run.status !== "failed" || !run.failure_code?.includes("BUILD")) return "not a build failure";
  const attempt = numberMetadata(run, "repair_attempt_number");
  if (attempt >= 2) return "repair attempt limit exceeded";
  return null;
}

function isOperationInProgressError(error: unknown) {
  return error instanceof CodingWorkflowApiError && error.code === "CODING_WORKFLOW_OPERATION_IN_PROGRESS";
}

function isWrongStageError(error: unknown) {
  return error instanceof CodingWorkflowApiError && (
    Boolean(error.code?.includes("NOT_AWAITING")) ||
    Boolean(error.code?.includes("ALREADY_"))
  );
}
