"use client";

import {
  CircleAlert,
  Clipboard,
  ExternalLink,
  FileDiff,
  Loader2,
  RefreshCw,
  Server,
  ShieldCheck,
  PlayCircle,
  Square,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { BridgeReviewPanel } from "@/components/ide/bridge-review-panel";
import { useForgeXDialogs } from "@/components/ide/dialogs/forgex-dialog-provider";
import { PatchApplyDetail } from "@/components/ide/patch-apply-detail";
import { PatchPreflightReport } from "@/components/ide/patch-preflight-report";
import { promptForgeApi } from "@/lib/api";
import { toErrorMessage } from "@/lib/errors";
import { conflictReadableMessage, patchPreflightStatus, preflightLogLines } from "@/lib/patch-preflight-status";
import { applyToneClass, patchApplyDisplayState } from "@/lib/patch-apply-status";
import { CompactPreflightStatus, QaStatus } from "@/components/ide/model-settings/status-components";
import { ProviderCardList } from "@/components/ide/model-settings/ProviderCardList";
import { ProviderDiagnosticsPanel } from "@/components/ide/model-settings/ProviderDiagnosticsPanel";
import { TaskRouteEditor } from "@/components/ide/model-settings/TaskRouteEditor";
import type { BusyKey, DiagnosticsFilter } from "@/components/ide/model-settings/types";
import {
  applyLabel,
  bridgeAuthClass,
  bridgeCommandLabel,
  bridgeConfidenceClass,
  bridgeSetupLabel,
  bridgeStatusClass,
  delay,
  formatBytes,
  formatRelativeTime,
  integrityClass,
  patchLabel,
  safeProviders,
  safeRoutes,
} from "@/components/ide/model-settings/utils";
import type {
  BridgeDetectionResponse,
  CodexOAuthStatusResponse,
  CodexOAuthSmokeResponse,
  CodexStatusDiagnosticsResponse,
  BridgeSafetyStatusResponse,
  BridgeReviewCountsResponse,
  BridgePatchExportResponse,
  BridgeReviewResponse,
  BridgeSandboxRunResponse,
  BridgeSandboxStatusResponse,
  ConsoleEntry,
  PatchApplyResult,
  PatchPreflightResult,
  RollbackRestorePreflightResult,
  RollbackRestoreResult,
  RollbackSnapshotCreateResponse,
  RollbackSnapshot,
  ModelInfoResponse,
  GenerationDiagnosticsRun,
  ModelProviderResponse,
  ModelRouteResponse,
  ProjectResponse,
  ModelUsageRecord,
} from "@/types";

interface ModelSettingsPanelProps {
  providers: ModelProviderResponse[];
  routes: ModelRouteResponse[];
  activeProject?: ProjectResponse | null;
  onRefresh: () => Promise<void>;
  onLog?: (entry: Omit<ConsoleEntry, "id" | "timestamp">) => void;
}

const TASK_TYPES = [
  "code_generation",
  "planning",
  "debugging",
  "documentation",
  "serial_analysis",
  "general_chat",
];

export function ModelSettingsPanel({ providers, routes, activeProject, onRefresh, onLog }: ModelSettingsPanelProps) {
  const dialogs = useForgeXDialogs();
  const providerList = useMemo(() => safeProviders(providers), [providers]);
  const configurableProviders = useMemo(
    () => providerList.filter((provider) => provider.provider_type !== "agent_provider" && provider.provider_id !== "codex"),
    [providerList],
  );
  const routeList = useMemo(() => safeRoutes(routes), [routes]);
  const [providerForms, setProviderForms] = useState<Record<string, { enabled: boolean; baseUrl: string; defaultModel: string }>>({});
  const apiKeyInputs = useRef<Record<string, HTMLInputElement | null>>({});
  const [routeForms, setRouteForms] = useState<Record<string, { providerId: string; modelId: string; fallbackEnabled: boolean; fallbackProviderId: string; localOnly: boolean }>>({});
  const [modelsByProvider, setModelsByProvider] = useState<Record<string, ModelInfoResponse[]>>({});
  const [modelErrors, setModelErrors] = useState<Record<string, string | null>>({});
  const [usage, setUsage] = useState<ModelUsageRecord[]>([]);
  const [generationRuns, setGenerationRuns] = useState<GenerationDiagnosticsRun[]>([]);
  const [bridges, setBridges] = useState<BridgeDetectionResponse[]>([]);
  const [codexOAuthStatus, setCodexOAuthStatus] = useState<CodexOAuthStatusResponse | null>(null);
  const [codexOAuthSmoke, setCodexOAuthSmoke] = useState<CodexOAuthSmokeResponse | null>(null);
  const [codexStatusDiagnostics, setCodexStatusDiagnostics] = useState<CodexStatusDiagnosticsResponse | null>(null);
  const [agySandboxStatus, setAgySandboxStatus] = useState<BridgeSandboxStatusResponse | null>(null);
  const [bridgeSafetyStatus, setBridgeSafetyStatus] = useState<BridgeSafetyStatusResponse | null>(null);
  const [agyRun, setAgyRun] = useState<BridgeSandboxRunResponse | null>(null);
  const [agyReview, setAgyReview] = useState<BridgeReviewResponse | null>(null);
  const [bridgeReviewCounts, setBridgeReviewCounts] = useState<BridgeReviewCountsResponse>({
    pending: 0,
    approved: 0,
    rejected: 0,
    expired: 0,
  });
  const [patchHistory, setPatchHistory] = useState<BridgePatchExportResponse[]>([]);
  const [patchPreflightResults, setPatchPreflightResults] = useState<Record<string, PatchPreflightResult>>({});
  const [rollbackSnapshots, setRollbackSnapshots] = useState<RollbackSnapshot[]>([]);
  const [rollbackSnapshotResults, setRollbackSnapshotResults] = useState<Record<string, RollbackSnapshotCreateResponse>>({});
  const [restorePreflightResults, setRestorePreflightResults] = useState<Record<string, RollbackRestorePreflightResult>>({});
  const [restoreResults, setRestoreResults] = useState<Record<string, RollbackRestoreResult>>({});
  const [patchApplyResults, setPatchApplyResults] = useState<Record<string, PatchApplyResult>>({});
  const [patchApplies, setPatchApplies] = useState<PatchApplyResult[]>([]);
  const [rollbackRestores, setRollbackRestores] = useState<RollbackRestoreResult[]>([]);
  const [patchAppliesLoading, setPatchAppliesLoading] = useState(true);
  const [patchAppliesError, setPatchAppliesError] = useState<string | null>(null);
  const [selectedPatchApplyId, setSelectedPatchApplyId] = useState<string | null>(null);
  const [selectedPatchApplyDetail, setSelectedPatchApplyDetail] = useState<PatchApplyResult | null>(null);
  const [patchApplyDetailLoading, setPatchApplyDetailLoading] = useState(false);
  const [patchApplyDetailError, setPatchApplyDetailError] = useState<string | null>(null);
  const [applyRestorePreflightResults, setApplyRestorePreflightResults] = useState<Record<string, RollbackRestorePreflightResult>>({});
  const [applyRestoreResults, setApplyRestoreResults] = useState<Record<string, RollbackRestoreResult>>({});
  const [expandedPreflightPatchId, setExpandedPreflightPatchId] = useState<string | null>(null);
  const [diagnosticsFilter, setDiagnosticsFilter] = useState<DiagnosticsFilter>("all");
  const [busy, setBusy] = useState<BusyKey>(null);
  const [message, setMessage] = useState<string | null>(null);

  const providerById = useMemo(
    () => Object.fromEntries(providerList.map((provider) => [provider.provider_id, provider])),
    [providerList],
  );
  const activeRoute = routeList.find((route) => route.task_type === "code_generation");
  const activeProvider = activeRoute ? providerById[activeRoute.provider_id] : null;
  const patchStats = useMemo(() => {
    const totalSize = patchHistory.reduce((sum, patch) => sum + patch.patch_size, 0);
    return {
      total: patchHistory.length,
      valid: patchHistory.filter((patch) => patch.integrity_status === "valid").length,
      modified: patchHistory.filter((patch) => patch.integrity_status === "modified").length,
      missing: patchHistory.filter((patch) => patch.integrity_status === "missing").length,
      storage: totalSize,
    };
  }, [patchHistory]);
  const restoreByRollbackId = useMemo(() => Object.fromEntries(
    rollbackRestores.map((restore) => [restore.rollback_id, restore]),
  ), [rollbackRestores]);
  const applyStats = useMemo(() => ({
    total: patchApplies.length,
    applied: patchApplies.filter((item) => item.status === "applied" && !restoreByRollbackId[item.rollback_id ?? ""]).length,
    restored: patchApplies.filter((item) => restoreByRollbackId[item.rollback_id ?? ""]?.status === "restored").length,
    restoreFailed: patchApplies.filter((item) => restoreByRollbackId[item.rollback_id ?? ""]?.status === "failed").length,
    failed: patchApplies.filter((item) => item.status === "failed" || item.status === "failed_rollback_failed").length,
    failedRolledBack: patchApplies.filter((item) => item.status === "failed_rolled_back").length,
    rollbackAvailable: patchApplies.filter((item) => item.rollback_available && item.rollback_id).length,
  }), [patchApplies, restoreByRollbackId]);
  const selectedPatchApply = useMemo(
    () => selectedPatchApplyDetail?.apply_id === selectedPatchApplyId
      ? selectedPatchApplyDetail
      : patchApplies.find((item) => item.apply_id === selectedPatchApplyId) ?? null,
    [patchApplies, selectedPatchApplyDetail, selectedPatchApplyId],
  );

  useEffect(() => {
    setProviderForms((current) => {
      const next = { ...current };
      for (const provider of providerList) {
        const existing = next[provider.provider_id];
        next[provider.provider_id] = {
          enabled: existing?.enabled ?? provider.enabled,
          baseUrl: existing?.baseUrl ?? provider.base_url ?? "",
          defaultModel: existing?.defaultModel ?? provider.default_model ?? "",
        };
      }
      return next;
    });
  }, [providerList]);

  useEffect(() => {
    setRouteForms((current) => {
      const next = { ...current };
      for (const taskType of TASK_TYPES) {
        const route = routeList.find((item) => item.task_type === taskType);
        next[taskType] = {
          providerId: current[taskType]?.providerId ?? route?.provider_id ?? "openrouter",
          modelId: current[taskType]?.modelId ?? route?.model_id ?? providerById.openrouter?.default_model ?? "",
          fallbackEnabled: current[taskType]?.fallbackEnabled ?? route?.fallback_enabled ?? true,
          fallbackProviderId: current[taskType]?.fallbackProviderId ?? route?.fallback_provider_id ?? "",
          localOnly: current[taskType]?.localOnly ?? route?.local_only ?? false,
        };
      }
      return next;
    });
  }, [providerById, routeList]);

  useEffect(() => {
    void refreshBridges();
    void refreshCodexOAuthStatus(false);
    void refreshAgySandboxStatus();
    void refreshBridgeSafetyStatus();
    void refreshBridgeReviews();
    void refreshPatchHistory();
    void refreshPatchApplies();
    void refreshRollbackSnapshots();
    void refreshUsage();
    void refreshDiagnostics();
    // Initial settings load only. Refresh handlers are stable enough for user-driven updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const loadReview = (reviewId: unknown) => {
      if (typeof reviewId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$/.test(reviewId)) return;
      window.sessionStorage.removeItem("forgex.pendingBridgeReviewId");
      void promptForgeApi.bridgeReview(reviewId)
        .then((response) => setAgyReview(response.review))
        .catch((error) => setMessage(toErrorMessage(error, "Bridge review could not be opened")));
    };
    const openReview = (event: Event) => loadReview((event as CustomEvent<{ reviewId?: unknown }>).detail?.reviewId);
    loadReview(window.sessionStorage.getItem("forgex.pendingBridgeReviewId"));
    window.addEventListener("forgex:open-bridge-review", openReview);
    return () => window.removeEventListener("forgex:open-bridge-review", openReview);
  }, []);

  const refreshBridges = async () => {
    setBusy("bridges:refresh");
    try {
      const response = await promptForgeApi.refreshModelBridges();
      setBridges(Array.isArray(response.bridges) ? response.bridges : []);
      void refreshBridgeReviews();
      void refreshPatchHistory();
    } catch (error) {
      setBridges([]);
      setMessage(toErrorMessage(error, "Bridge detection unavailable"));
    } finally {
      setBusy(null);
    }
  };

  const copyCodexLoginCommand = async (bridge: BridgeDetectionResponse) => {
    const command = bridge.login_command ?? "codex login";
    try {
      await navigator.clipboard.writeText(command);
      setMessage("Copied codex login. Run it in your terminal; the official Codex CLI owns the sign-in session.");
      onLog?.({ channel: "system", message: "[Codex CLI OAuth Bridge] Official login command copied." });
    } catch (error) {
      setMessage(toErrorMessage(error, "Codex login command could not be copied"));
    }
  };

  const refreshCodexOAuthStatus = async (notify = true) => {
    if (notify) setBusy("codex:status");
    try {
      const response = await promptForgeApi.codexOAuthStatus();
      setCodexOAuthStatus(response);
      if (notify) {
        setMessage(`Codex auth status: ${response.auth_status.replace("_", " ")}.`);
        onLog?.({ channel: "system", message: `[Codex CLI OAuth Bridge] Status: ${response.auth_status}.` });
      }
    } catch (error) {
      setCodexOAuthStatus(null);
      if (notify) setMessage(toErrorMessage(error, "Codex status check failed safely"));
    } finally {
      if (notify) setBusy(null);
    }
  };

  const runCodexStatusDiagnostics = async () => {
    setBusy("codex:diagnostics");
    try {
      const response = await promptForgeApi.codexStatusDiagnostics();
      setCodexStatusDiagnostics(response);
      setMessage(`Codex status diagnostics: ${response.classification}.`);
    } catch (error) {
      setCodexStatusDiagnostics(null);
      setMessage(toErrorMessage(error, "Codex status diagnostics failed safely"));
    } finally {
      setBusy(null);
    }
  };

  const launchCodexLogin = async () => {
    const confirmed = await dialogs.confirmAction({
      title: "Launch Codex Login",
      description: "ForgeX will launch the official Codex CLI login. ForgeX will not see or store your OAuth tokens. Continue?",
      confirmText: "Launch Codex Login",
      cancelText: "Cancel",
    });
    if (!confirmed) return;
    setBusy("codex:login");
    try {
      const result = await promptForgeApi.launchCodexOAuthLogin();
      if (result.launched && result.classification === "CODEX_LOGIN_LAUNCHED") {
        setMessage("Official Codex CLI login launched. Complete login, then click Check Status.");
        onLog?.({ channel: "system", message: "[Codex CLI OAuth Bridge] Official login launched; authentication is not yet assumed." });
      } else {
        setMessage(`Open a terminal and run: codex login (${result.classification})`);
      }
    } catch (error) {
      setMessage(`${toErrorMessage(error, "Codex login launch failed safely")}. Open a terminal and run: codex login`);
    } finally {
      setBusy(null);
    }
  };

  const runCodexOAuthSmoke = async () => {
    const confirmed = await dialogs.confirmAction({
      title: "Run Sandboxed Smoke",
      description: "ForgeX will run one Codex CLI smoke test inside an external disposable sandbox. It will not edit your active workspace. Continue?",
      confirmText: "Run Sandboxed Smoke",
      cancelText: "Cancel",
    });
    if (!confirmed) return;
    setBusy("codex:smoke");
    try {
      const result = await promptForgeApi.runCodexOAuthSmoke();
      setCodexOAuthSmoke(result);
      setMessage(`Codex smoke: ${result.classification}. Review created: ${result.review_created ? "yes" : "no"}.`);
      onLog?.({ channel: "system", message: `[Codex CLI OAuth Bridge] ${result.classification}; production routing remains disabled.` });
      if (result.review_created) void refreshBridgeReviews();
    } catch (error) {
      setCodexOAuthSmoke(null);
      setMessage(toErrorMessage(error, "Codex smoke failed safely"));
    } finally {
      setBusy(null);
    }
  };

  const openCodexOAuthReview = async () => {
    const reviewId = codexOAuthSmoke?.review_id;
    if (!reviewId) return;
    try {
      const response = await promptForgeApi.bridgeReview(reviewId);
      setAgyReview(response.review);
    } catch (error) {
      setMessage(toErrorMessage(error, "Codex Bridge Review could not be opened"));
    }
  };

  const refreshBridgeReviews = async () => {
    try {
      const response = await promptForgeApi.bridgeReviews();
      setBridgeReviewCounts(response.counts);
    } catch {
      setBridgeReviewCounts({ pending: 0, approved: 0, rejected: 0, expired: 0 });
    }
  };

  const refreshPatchHistory = async () => {
    try {
      const response = await promptForgeApi.bridgePatches();
      setPatchHistory(Array.isArray(response.patches) ? response.patches : []);
    } catch {
      setPatchHistory([]);
    }
  };

  const refreshPatchApplies = async () => {
    setPatchAppliesLoading(true);
    setPatchAppliesError(null);
    try {
      const [response, restoresResponse] = await Promise.all([
        promptForgeApi.listPatchApplies(),
        promptForgeApi.rollbackRestores(),
      ]);
      const applies = Array.isArray(response.applies) ? response.applies : [];
      setPatchApplies(applies);
      setRollbackRestores(Array.isArray(restoresResponse.restores) ? restoresResponse.restores : []);
      setSelectedPatchApplyDetail(null);
      setPatchApplyDetailError(null);
      setSelectedPatchApplyId((current) => current && applies.some((item) => item.apply_id === current) ? current : applies[0]?.apply_id ?? null);
    } catch (error) {
      setPatchApplies([]);
      setRollbackRestores([]);
      setSelectedPatchApplyId(null);
      setPatchAppliesError(toErrorMessage(error, "Patch apply history is unavailable."));
    } finally {
      setPatchAppliesLoading(false);
    }
  };

  const openPatchApplyDetail = async (applyId: string) => {
    setSelectedPatchApplyId(applyId);
    setSelectedPatchApplyDetail(null);
    setPatchApplyDetailError(null);
    setPatchApplyDetailLoading(true);
    try {
      const response = await promptForgeApi.getPatchApply(applyId);
      setSelectedPatchApplyDetail(response.apply);
    } catch (error) {
      setPatchApplyDetailError(toErrorMessage(error, "Patch apply detail is unavailable."));
    } finally {
      setPatchApplyDetailLoading(false);
    }
  };

  const refreshRollbackSnapshots = async () => {
    try {
      const response = await promptForgeApi.rollbackSnapshots();
      setRollbackSnapshots(Array.isArray(response.snapshots) ? response.snapshots : []);
    } catch {
      setRollbackSnapshots([]);
    }
  };

  const refreshAgySandboxStatus = async () => {
    try {
      setAgySandboxStatus(await promptForgeApi.antigravitySandboxStatus());
    } catch {
      setAgySandboxStatus(null);
    }
  };

  const refreshBridgeSafetyStatus = async () => {
    try {
      setBridgeSafetyStatus(await promptForgeApi.bridgeSafetyStatus());
    } catch {
      setBridgeSafetyStatus(null);
    }
  };

  const pollAgyRun = async (runId: string) => {
    let current = agyRun;
    for (let attempt = 0; attempt < 300; attempt += 1) {
      const response = await promptForgeApi.bridgeRun(runId);
      current = response.run;
      setAgyRun(current);
      if (!["pending", "running"].includes(current.status)) {
        if (current.review_id) {
          const reviewResponse = await promptForgeApi.bridgeReview(current.review_id);
          setAgyReview(reviewResponse.review);
          await refreshBridgeReviews();
          await refreshPatchHistory();
        }
        return current;
      }
      await delay(1000);
    }
    return current;
  };

  const runAgySandboxTest = async (bridge: BridgeDetectionResponse) => {
    if (!activeProject) {
      await dialogs.message({
        title: "Open a workspace first",
        description: "Open or import a workspace before running an AGY sandbox test.",
      });
      return;
    }
    if (!agySandboxStatus?.enabled) {
      await dialogs.message({
        title: "AGY sandbox disabled",
        description: "AGY sandbox execution is disabled. Enable FORGEX_ENABLE_AGY_BRIDGE=1 for development testing.",
      });
      return;
    }
    if (!bridge.installed) {
      await dialogs.message({
        title: "AGY not installed",
        description: "Install Google Antigravity / AGY CLI and complete official sign-in before running a sandbox test.",
      });
      return;
    }
    const confirmed = await dialogs.confirmAction({
      title: "Run AGY in a sandbox?",
      description: `ForgeX will copy ${activeProject.project_name} to a temporary sandbox and run AGY only inside that copy. The active workspace will not be modified.`,
      confirmText: "Continue",
      variant: "hardware",
    });
    if (!confirmed) return;
    const prompt = await dialogs.input({
      title: "AGY sandbox prompt",
      description: "This prompt is sent only to the local AGY CLI running inside the temporary sandbox copy.",
      label: "Prompt",
      placeholder: "Add a comment to README.md saying this is an AGY sandbox test.",
      confirmText: "Run Sandbox Test",
      validate: (value) => (value.trim() ? null : "Enter a prompt."),
    });
    if (!prompt) return;
    setBusy("agy:sandbox");
    setMessage(null);
    setAgyReview(null);
    try {
      const response = await promptForgeApi.startAntigravitySandboxRun({
        workspace_root: activeProject.project_path,
        prompt,
        timeout_seconds: 300,
      });
      setAgyRun(response.run);
      const completed = await pollAgyRun(response.run.run_id);
      if (completed?.status === "review_ready") {
        setMessage(completed.changed_file_count > 0 ? "AGY sandbox review is ready" : "AGY completed but produced no file changes.");
      } else if (completed?.error_message) {
        setMessage(completed.error_message);
      }
    } catch (error) {
      setMessage(toErrorMessage(error, "AGY sandbox run failed"));
    } finally {
      setBusy(null);
    }
  };

  const cancelAgyRun = async () => {
    if (!agyRun || !["pending", "running"].includes(agyRun.status)) return;
    try {
      const response = await promptForgeApi.cancelBridgeRun(agyRun.run_id);
      setAgyRun(response.run);
      setMessage("AGY sandbox run cancelled");
    } catch (error) {
      setMessage(toErrorMessage(error, "AGY sandbox run could not be cancelled"));
    }
  };

  const approveAgyReview = async (reviewId: string) => {
    setBusy("agy:review");
    try {
      const response = await promptForgeApi.approveBridgeReview(reviewId);
      setAgyReview(response.review);
      await refreshBridgeReviews();
    } catch (error) {
      setMessage(toErrorMessage(error, "Bridge review could not be approved"));
    } finally {
      setBusy(null);
    }
  };

  const rejectAgyReview = async (reviewId: string) => {
    setBusy("agy:review");
    try {
      const response = await promptForgeApi.rejectBridgeReview(reviewId);
      setAgyReview(response.review);
      await refreshBridgeReviews();
    } catch (error) {
      setMessage(toErrorMessage(error, "Bridge review could not be rejected"));
    } finally {
      setBusy(null);
    }
  };

  const refreshUsage = async () => {
    try {
      const response = await promptForgeApi.modelUsage(25);
      setUsage(Array.isArray(response.usage) ? response.usage : []);
    } catch {
      setUsage([]);
    }
  };

  const refreshDiagnostics = async () => {
    try {
      const response = await promptForgeApi.chunkedGenerationRuns(25);
      setGenerationRuns(Array.isArray(response.runs) ? response.runs : []);
    } catch {
      setGenerationRuns([]);
    }
  };

  const clearDiagnostics = async () => {
    const approved = await dialogs.confirmAction({
      title: "Clear generation diagnostics?",
      description: "Clear persisted generation attempt and chunked run diagnostics from local app data.",
      confirmText: "Clear Diagnostics",
      variant: "danger",
    });
    if (!approved) return;
    setBusy("diagnostics:clear");
    setMessage(null);
    try {
      await promptForgeApi.clearGenerationDiagnostics();
      setGenerationRuns([]);
      setMessage("Generation diagnostics cleared");
    } catch (error) {
      setMessage(toErrorMessage(error, "Generation diagnostics could not be cleared"));
    } finally {
      setBusy(null);
    }
  };

  const viewPatchReview = async (patch: BridgePatchExportResponse) => {
    setBusy(`patch:view:${patch.patch_id}`);
    setMessage(null);
    try {
      const response = await promptForgeApi.bridgeReview(patch.review_id);
      setAgyReview(response.review);
      setMessage("Patch review loaded.");
    } catch (error) {
      setMessage(toErrorMessage(error, "Patch review could not be loaded"));
    } finally {
      setBusy(null);
    }
  };

  const verifyPatchHistoryItem = async (patch: BridgePatchExportResponse) => {
    setBusy(`patch:verify:${patch.patch_id}`);
    setMessage(null);
    try {
      await promptForgeApi.verifyBridgeReviewPatch(patch.review_id);
      await refreshPatchHistory();
      setMessage("Patch integrity verified.");
    } catch (error) {
      setMessage(toErrorMessage(error, "Patch integrity could not be verified"));
    } finally {
      setBusy(null);
    }
  };

  const copyPatchHistoryItem = async (patch: BridgePatchExportResponse) => {
    setBusy(`patch:copy:${patch.patch_id}`);
    setMessage(null);
    try {
      const text = await promptForgeApi.bridgeReviewPatch(patch.review_id);
      await navigator.clipboard.writeText(text);
      await promptForgeApi.markBridgeReviewPatchCopied(patch.review_id);
      await refreshPatchHistory();
      setMessage("Patch copied to clipboard. Review it before applying manually.");
    } catch (error) {
      setMessage(toErrorMessage(error, "Patch could not be copied"));
    } finally {
      setBusy(null);
    }
  };

  const openPatchHistoryFolder = async (patch: BridgePatchExportResponse) => {
    setBusy(`patch:open:${patch.patch_id}`);
    setMessage(null);
    try {
      await promptForgeApi.openBridgeReviewPatchFolder(patch.review_id);
      await refreshPatchHistory();
      setMessage("Patch folder opened.");
    } catch (error) {
      setMessage(toErrorMessage(error, "Patch folder could not be opened"));
    } finally {
      setBusy(null);
    }
  };

  const preflightPatchHistoryItem = async (patch: BridgePatchExportResponse) => {
    if (!activeProject?.project_path) {
      setMessage("Open the target workspace before running preflight.");
      return;
    }
    setBusy(`patch:preflight:${patch.patch_id}`);
    setMessage("Running preflight...");
    onLog?.({ channel: "system", message: "[preflight] Started patch preflight" });
    try {
      const result = await promptForgeApi.preflightPatch(patch.patch_id, activeProject.project_path);
      setPatchPreflightResults((current) => ({ ...current, [patch.patch_id]: result }));
      setRollbackSnapshotResults((current) => {
        const next = { ...current };
        delete next[patch.patch_id];
        return next;
      });
      setRestorePreflightResults((current) => {
        const next = { ...current };
        delete next[patch.patch_id];
        return next;
      });
      setRestoreResults((current) => {
        const next = { ...current };
        delete next[patch.patch_id];
        return next;
      });
      setPatchApplyResults((current) => {
        const next = { ...current };
        delete next[patch.patch_id];
        return next;
      });
      setExpandedPreflightPatchId(patch.patch_id);
      for (const line of preflightLogLines(result)) {
        onLog?.({ channel: result.can_apply ? "system" : "error", message: line });
      }
      const status = patchPreflightStatus(result);
      const firstIssue = result.conflicts[0] ?? result.warnings[0];
      setMessage(
        result.can_apply
          ? bridgeSafetyStatus?.patch_apply_enabled
            ? "Preflight passed. Patch can be applied after exact confirmation."
            : "Preflight passed. Patch appears safe to apply, but Apply is disabled."
          : `Preflight failed. ${firstIssue ? conflictReadableMessage(firstIssue) : status.summary}`,
      );
    } catch (error) {
      const message = toErrorMessage(error, "Patch preflight failed");
      onLog?.({ channel: "error", message: `[preflight] ${message}` });
      setMessage(message);
    } finally {
      setBusy(null);
    }
  };

  const createRollbackSnapshotForPatch = async (patch: BridgePatchExportResponse) => {
    const result = patchPreflightResults[patch.patch_id];
    if (!activeProject?.project_path || !result?.can_apply) return;
    setBusy(`patch:rollback:${patch.patch_id}`);
    setMessage(null);
    try {
      const snapshot = await promptForgeApi.createRollbackSnapshot(patch.patch_id, activeProject.project_path);
      setRollbackSnapshotResults((current) => ({ ...current, [patch.patch_id]: snapshot }));
      setRestorePreflightResults((current) => {
        const next = { ...current };
        delete next[patch.patch_id];
        return next;
      });
      setRestoreResults((current) => {
        const next = { ...current };
        delete next[patch.patch_id];
        return next;
      });
      await refreshRollbackSnapshots();
      setMessage(bridgeSafetyStatus?.patch_apply_enabled ? "Rollback snapshot created. Apply will still create a fresh snapshot before writing." : "Rollback snapshot created. Apply is disabled.");
      onLog?.({ channel: "system", message: `[preflight] Rollback snapshot created, ${snapshot.files_backed_up} file${snapshot.files_backed_up === 1 ? "" : "s"} backed up` });
    } catch (error) {
      const message = toErrorMessage(error, "Rollback snapshot failed");
      onLog?.({ channel: "error", message: `[preflight] ${message}` });
      setMessage(message);
    } finally {
      setBusy(null);
    }
  };

  const preflightRestoreForPatch = async (patch: BridgePatchExportResponse) => {
    const snapshot = rollbackSnapshotResults[patch.patch_id];
    if (!activeProject?.project_path || !snapshot) return;
    setBusy(`patch:restore-preflight:${patch.patch_id}`);
    setMessage("Running restore preflight...");
    try {
      const result = await promptForgeApi.restorePreflightRollbackSnapshot(snapshot.rollback_id, activeProject.project_path);
      setRestorePreflightResults((current) => ({ ...current, [patch.patch_id]: result }));
      setRestoreResults((current) => {
        const next = { ...current };
        delete next[patch.patch_id];
        return next;
      });
      const firstIssue = result.conflicts[0] ?? result.warnings[0];
      setMessage(
        result.can_restore
          ? "Restore preflight passed. Snapshot appears restorable, but Restore is disabled in this build."
          : `Restore preflight blocked. ${firstIssue ? `${firstIssue.path ? `${firstIssue.path}: ` : ""}${firstIssue.message}` : "Review restore conflicts."}`,
      );
      onLog?.({ channel: result.can_restore ? "system" : "error", message: `[preflight] Restore preflight ${result.can_restore ? "passed" : "blocked"}, ${result.conflicts.length} conflict${result.conflicts.length === 1 ? "" : "s"}` });
    } catch (error) {
      const message = toErrorMessage(error, "Restore preflight failed");
      onLog?.({ channel: "error", message: `[preflight] ${message}` });
      setMessage(message);
    } finally {
      setBusy(null);
    }
  };

  const restoreSnapshotForPatch = async (patch: BridgePatchExportResponse) => {
    const snapshot = rollbackSnapshotResults[patch.patch_id];
    const restorePreflight = restorePreflightResults[patch.patch_id];
    if (!activeProject?.project_path || !snapshot || !restorePreflight?.can_restore || !bridgeSafetyStatus?.restore_enabled) return;
    const confirmation = await dialogs.input({
      title: "Restore files from rollback snapshot?",
      description: "This will modify files in your active workspace.\nOnly files recorded in the rollback snapshot will be restored.",
      label: "Type RESTORE to continue",
      placeholder: "RESTORE",
      confirmText: "Restore Snapshot",
      validate: (value) => (value.trim() === "RESTORE" ? null : "Type RESTORE exactly to continue."),
    });
    if (confirmation !== "RESTORE") return;
    setBusy(`patch:restore:${patch.patch_id}`);
    setMessage("Restoring rollback snapshot...");
    try {
      const result = await promptForgeApi.restoreRollbackSnapshot(snapshot.rollback_id, activeProject.project_path, confirmation);
      setRestoreResults((current) => ({ ...current, [patch.patch_id]: result }));
      await refreshBridgeSafetyStatus();
      setMessage(`Restore completed. Files restored: ${result.files_restored}. Files removed: ${result.files_removed}. Files failed: ${result.files_failed}.`);
      onLog?.({ channel: result.files_failed ? "error" : "system", message: `[restore] Restore ${result.status}, restored=${result.files_restored}, removed=${result.files_removed}, failed=${result.files_failed}` });
    } catch (error) {
      const message = toErrorMessage(error, "Rollback restore failed");
      onLog?.({ channel: "error", message: `[restore] ${message}` });
      setMessage(message);
    } finally {
      setBusy(null);
    }
  };

  const applyPatchHistoryItem = async (patch: BridgePatchExportResponse) => {
    const preflight = patchPreflightResults[patch.patch_id];
    const canApply = Boolean(
      activeProject?.project_path &&
      bridgeSafetyStatus?.patch_apply_enabled &&
      bridgeSafetyStatus?.rollback_restore_enabled &&
      preflight?.can_apply &&
      preflight.apply_enabled &&
      patch.review_status_at_export === "approved" &&
      patch.integrity_status === "valid",
    );
    if (!activeProject?.project_path || !canApply) return;
    const confirmation = await dialogs.input({
      title: "Apply patch to active workspace?",
      description: "This will modify files in your active workspace.\nForgeX will create a rollback snapshot before applying.\nOnly files listed in the preflight report will be touched.\nYou can use rollback restore if something goes wrong.",
      label: "Type APPLY to continue",
      placeholder: "APPLY",
      confirmText: "Apply Patch",
      validate: (value) => (value.trim() === "APPLY" ? null : "Type APPLY exactly to continue."),
    });
    if (confirmation !== "APPLY") return;
    setBusy(`patch:apply:${patch.patch_id}`);
    setMessage("Applying patch...");
    onLog?.({ channel: "system", message: "[apply] Patch apply started" });
    try {
      const result = await promptForgeApi.applyPatch(patch.patch_id, activeProject.project_path, confirmation);
      setPatchApplyResults((current) => ({ ...current, [patch.patch_id]: result }));
      await refreshPatchHistory();
      await refreshRollbackSnapshots();
      await refreshPatchApplies();
      setMessage(
        result.status === "applied"
          ? `Patch applied. Files created: ${result.files_created}. Files modified: ${result.files_modified}. Files deleted: ${result.files_deleted}. Rollback snapshot: ${result.rollback_id ?? "unknown"}. Rollback restore is available if needed.`
          : `Patch apply ${result.status}. Files failed: ${result.files_failed}. Rollback snapshot: ${result.rollback_id ?? "unknown"}.`,
      );
      onLog?.({ channel: result.status === "applied" ? "system" : "error", message: "[apply] Fresh preflight passed" });
      onLog?.({ channel: result.status === "applied" ? "system" : "error", message: `[apply] Rollback snapshot created: ${result.rollback_id ?? "unknown"}` });
      onLog?.({ channel: result.status === "applied" ? "system" : "error", message: `[apply] Files created: ${result.files_created}, modified: ${result.files_modified}, deleted: ${result.files_deleted}` });
      onLog?.({ channel: result.status === "applied" ? "system" : "error", message: `[apply] Patch apply ${result.status === "applied" ? "completed" : result.status}` });
    } catch (error) {
      const message = toErrorMessage(error, "Patch apply failed");
      onLog?.({ channel: "error", message: `[apply] ${message}` });
      setMessage(message);
    } finally {
      setBusy(null);
    }
  };

  const openApplyRollbackSnapshot = async (rollbackId: string) => {
    setBusy(`apply:rollback:${rollbackId}`);
    setMessage(null);
    try {
      const response = await promptForgeApi.rollbackSnapshot(rollbackId);
      setMessage(`Rollback snapshot available. Files tracked: ${response.snapshot.files.length}.`);
    } catch (error) {
      setMessage(toErrorMessage(error, "Rollback snapshot could not be loaded"));
    } finally {
      setBusy(null);
    }
  };

  const preflightRestoreForApply = async (apply: PatchApplyResult) => {
    if (!activeProject?.project_path || !apply.rollback_id) return;
    setBusy(`apply:restore-preflight:${apply.apply_id}`);
    setMessage("Running restore preflight...");
    try {
      const result = await promptForgeApi.restorePreflightRollbackSnapshot(apply.rollback_id, activeProject.project_path);
      setApplyRestorePreflightResults((current) => ({ ...current, [apply.apply_id]: result }));
      setApplyRestoreResults((current) => {
        const next = { ...current };
        delete next[apply.apply_id];
        return next;
      });
      const firstIssue = result.conflicts[0] ?? result.warnings[0];
      setMessage(
        result.can_restore
          ? "Restore preflight passed. Rollback snapshot can be restored with RESTORE confirmation."
          : `Restore preflight blocked. ${firstIssue ? `${firstIssue.path ? `${firstIssue.path}: ` : ""}${firstIssue.message}` : "Review restore conflicts."}`,
      );
      onLog?.({ channel: result.can_restore ? "system" : "error", message: `[preflight] Restore preflight ${result.can_restore ? "passed" : "blocked"}, ${result.conflicts.length} conflict${result.conflicts.length === 1 ? "" : "s"}` });
    } catch (error) {
      const message = toErrorMessage(error, "Restore preflight failed");
      onLog?.({ channel: "error", message: `[preflight] ${message}` });
      setMessage(message);
    } finally {
      setBusy(null);
    }
  };

  const restoreSnapshotForApply = async (apply: PatchApplyResult) => {
    const restorePreflight = applyRestorePreflightResults[apply.apply_id];
    if (!activeProject?.project_path || !apply.rollback_id || !restorePreflight?.can_restore || !bridgeSafetyStatus?.restore_enabled) return;
    const confirmation = await dialogs.input({
      title: "Restore files from rollback snapshot?",
      description: "This will modify files in your active workspace.\nOnly files recorded in the rollback snapshot will be restored.",
      label: "Type RESTORE to continue",
      placeholder: "RESTORE",
      confirmText: "Restore Snapshot",
      validate: (value) => (value.trim() === "RESTORE" ? null : "Type RESTORE exactly to continue."),
    });
    if (confirmation !== "RESTORE") return;
    setBusy(`apply:restore:${apply.apply_id}`);
    setMessage("Restoring rollback snapshot...");
    onLog?.({ channel: "system", message: "[restore] Rollback restore started" });
    try {
      const result = await promptForgeApi.restoreRollbackSnapshot(apply.rollback_id, activeProject.project_path, confirmation);
      setApplyRestoreResults((current) => ({ ...current, [apply.apply_id]: result }));
      await refreshRollbackSnapshots();
      setMessage(`Restore completed. Files restored: ${result.files_restored}. Files removed: ${result.files_removed}. Files failed: ${result.files_failed}.`);
      onLog?.({ channel: result.files_failed ? "error" : "system", message: `[restore] Files restored: ${result.files_restored}, removed: ${result.files_removed}, failed: ${result.files_failed}` });
    } catch (error) {
      const message = toErrorMessage(error, "Rollback restore failed");
      onLog?.({ channel: "error", message: `[restore] ${message}` });
      setMessage(message);
    } finally {
      setBusy(null);
    }
  };

  const deletePatchHistoryItem = async (patch: BridgePatchExportResponse) => {
    const approved = await dialogs.confirmAction({
      title: "Delete exported patch?",
      description: "This removes the exported patch file and metadata. It does not modify your workspace or review history.",
      confirmText: "Delete Patch",
      variant: "danger",
    });
    if (!approved) return;
    setBusy(`patch:delete:${patch.patch_id}`);
    setMessage(null);
    try {
      await promptForgeApi.deleteBridgePatch(patch.patch_id);
      await refreshPatchHistory();
      setMessage("Exported patch deleted. Review history was preserved.");
    } catch (error) {
      setMessage(toErrorMessage(error, "Patch could not be deleted"));
    } finally {
      setBusy(null);
    }
  };

  const cleanupPatchHistory = async () => {
    const approved = await dialogs.confirmAction({
      title: "Clean up exported patches?",
      description: "Remove exported patches older than 30 days and missing patch records. This does not modify your workspace or review history.",
      confirmText: "Clean Up Patches",
      variant: "danger",
    });
    if (!approved) return;
    setBusy("patch:cleanup");
    setMessage(null);
    try {
      const response = await promptForgeApi.cleanupBridgePatches({ older_than_days: 30, include_missing: true });
      await refreshPatchHistory();
      setMessage(`Patch cleanup removed ${response.cleanup.removed} exported patch record(s).`);
    } catch (error) {
      setMessage(toErrorMessage(error, "Patch cleanup failed"));
    } finally {
      setBusy(null);
    }
  };

  const updateProviderForm = (providerId: string, patch: Partial<{ enabled: boolean; baseUrl: string; defaultModel: string }>) => {
    setProviderForms((current) => ({
      ...current,
      [providerId]: {
        ...(current[providerId] ?? { enabled: true, baseUrl: "", defaultModel: "" }),
        ...patch,
      },
    }));
  };

  const updateRouteForm = (taskType: string, patch: Partial<{ providerId: string; modelId: string; fallbackEnabled: boolean; fallbackProviderId: string; localOnly: boolean }>) => {
    setRouteForms((current) => {
      const existing = current[taskType] ?? {
        providerId: "openrouter",
        modelId: "",
        fallbackEnabled: true,
        localOnly: false,
      };
      const next = { ...existing, ...patch };
      if (patch.providerId) {
        next.modelId = providerById[patch.providerId]?.default_model ?? next.modelId;
      }
      return { ...current, [taskType]: next };
    });
  };

  const saveProvider = async (provider: ModelProviderResponse) => {
    const form = providerForms[provider.provider_id];
    if (!form) return;
    const apiKey = apiKeyInputs.current[provider.provider_id]?.value.trim() ?? "";
    if (provider.auth_type === "api_key" && form.enabled && !provider.configured && !apiKey) {
      setMessage(`${provider.display_name} requires an API key.`);
      return;
    }
    setBusy(`save:${provider.provider_id}`);
    setMessage(null);
    try {
      await promptForgeApi.configureModelProvider(provider.provider_id, {
        api_key: apiKey || undefined,
        base_url: form.baseUrl.trim() || undefined,
        default_model: form.defaultModel.trim() || undefined,
        enabled: form.enabled,
      });
      if (apiKeyInputs.current[provider.provider_id]) apiKeyInputs.current[provider.provider_id]!.value = "";
      await onRefresh();
      setMessage(`${provider.display_name} saved`);
    } catch (error) {
      setMessage(toErrorMessage(error, "Provider save failed"));
    } finally {
      setBusy(null);
    }
  };

  const clearProviderKey = async (provider: ModelProviderResponse) => {
    const approved = await dialogs.confirmAction({
      title: "Clear API key?",
      description: `Clear the saved API key for ${provider.display_name}?`,
      confirmText: "Clear Key",
      variant: "danger",
    });
    if (!approved) return;
    setBusy(`clear:${provider.provider_id}`);
    setMessage(null);
    try {
      await promptForgeApi.configureModelProvider(provider.provider_id, { api_key: null });
      await onRefresh();
      setMessage(`${provider.display_name} API key cleared`);
    } catch (error) {
      setMessage(toErrorMessage(error, "API key could not be cleared"));
    } finally {
      setBusy(null);
    }
  };

  const testProvider = async (provider: ModelProviderResponse) => {
    setBusy(`test:${provider.provider_id}`);
    setMessage(null);
    try {
      const result = await promptForgeApi.testModelProvider(provider.provider_id);
      await onRefresh();
      setMessage(result.health.ok ? `${provider.display_name} is healthy` : result.health.message ?? `${provider.display_name} health check failed`);
    } catch (error) {
      setMessage(toErrorMessage(error, "Health check failed"));
    } finally {
      setBusy(null);
    }
  };

  const refreshModels = async (provider: ModelProviderResponse) => {
    setBusy(`models:${provider.provider_id}`);
    setMessage(null);
    try {
      const response = await promptForgeApi.providerModels(provider.provider_id);
      setModelsByProvider((current) => ({
        ...current,
        [provider.provider_id]: Array.isArray(response.models) ? response.models : [],
      }));
      setModelErrors((current) => ({ ...current, [provider.provider_id]: response.error ?? null }));
      await onRefresh();
      setMessage(response.error ?? `${provider.display_name} models refreshed`);
    } catch (error) {
      const text = toErrorMessage(error, "Could not fetch model list. Enter model ID manually.");
      setModelErrors((current) => ({ ...current, [provider.provider_id]: text }));
      setMessage(text);
    } finally {
      setBusy(null);
    }
  };

  const saveRoute = async (taskType: string) => {
    const form = routeForms[taskType];
    if (!form) return;
    setBusy(`route:${taskType}`);
    setMessage(null);
    try {
      await promptForgeApi.saveModelRoute({
        task_type: taskType,
        provider_id: form.providerId,
        model_id: form.modelId.trim() || providerById[form.providerId]?.default_model || "local-model",
        fallback_enabled: form.fallbackEnabled,
        fallback_provider_id: form.fallbackEnabled ? form.fallbackProviderId || null : null,
        local_only: form.localOnly,
      });
      await onRefresh();
      setMessage(`${taskType} route saved`);
    } catch (error) {
      setMessage(toErrorMessage(error, "Route could not be saved"));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-[var(--fx-panel)]">
      <div className="min-h-0 min-w-0 flex-1 space-y-3 overflow-y-auto overflow-x-hidden p-3">
        <section className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
          <div className="flex min-w-0 items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2 text-sm font-semibold text-[var(--fx-text)]">
              <Server className="h-4 w-4 shrink-0 text-[var(--fx-info)]" />
              Model Router
            </div>
            {message ? <span className="min-w-0 truncate text-[11px] text-[var(--fx-text-muted)]">{message}</span> : null}
          </div>
          <div className="mt-3 min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2 text-xs">
            <div className="font-semibold uppercase text-[var(--fx-text-muted)]">Active code generation route</div>
            {activeRoute ? (
              <div className="mt-1 break-words text-[var(--fx-code-text)]">
                {activeProvider?.display_name ?? activeRoute.provider_id} / {activeRoute.model_id || "No model"}
                <span className="ml-2 text-[var(--fx-text-muted)]">Fallback: {activeRoute.fallback_enabled ? (providerById[activeRoute.fallback_provider_id ?? ""]?.display_name ?? activeRoute.fallback_provider_id ?? "not selected") : "disabled"}</span>
              </div>
            ) : (
              <div className="mt-1 text-[var(--fx-error)]">
                No active model route configured. Configure OpenRouter or a local provider to generate firmware.
              </div>
            )}
          </div>
        </section>

        <section className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
          <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="text-xs font-semibold uppercase text-[var(--fx-text-muted)]">Verified Templates</div>
              <div className="mt-1 text-sm font-medium text-[var(--fx-text)]">Built-in embedded project templates</div>
              <div className="mt-1 max-w-2xl text-[11px] leading-4 text-[var(--fx-text-muted)]">Deterministic templates require no account or API key. Supported routes include ESP32 and Arduino blink, minimal PlatformIO, serial output, WiFi scan, OLED test, and sensor read.</div>
            </div>
            <span className="shrink-0 rounded border border-[var(--fx-success)] bg-[var(--fx-success-soft)] px-2 py-1 text-[11px] text-[var(--fx-success)]">Ready</span>
          </div>
        </section>

        <ProviderCardList
          providers={configurableProviders}
          providerForms={providerForms}
          modelsByProvider={modelsByProvider}
          modelErrors={modelErrors}
          apiKeyInputs={apiKeyInputs}
          busy={busy}
          updateProviderForm={updateProviderForm}
          clearProviderKey={clearProviderKey}
          saveProvider={saveProvider}
          testProvider={testProvider}
          refreshModels={refreshModels}
        />
        <section className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="text-xs font-semibold uppercase text-[var(--fx-text-muted)]">Local Agent Providers</div>
            <button
              className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
              onClick={() => void refreshBridges()}
              disabled={Boolean(busy)}
              title="Refresh bridge detection"
            >
              {busy === "bridges:refresh" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              Refresh
            </button>
          </div>
          <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2 text-[11px] text-[var(--fx-text-muted)]">
            Gemini CLI has moved to Antigravity / AGY CLI for individual users. ForgeX detects AGY instead of the legacy Gemini CLI.
          </div>
          <div className="grid min-w-0 grid-cols-1 gap-2 2xl:grid-cols-2">
            {bridges.map((bridge) => (
              <div key={bridge.provider_id} className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
                <div className="flex min-w-0 items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-[var(--fx-text)]">{bridge.display_name}</div>
                    <div className="mt-1 flex flex-wrap gap-1 text-[11px]">
                      <span className={`rounded border px-1.5 py-0.5 ${bridgeStatusClass(bridge)}`}>
                        {bridge.installed ? "Installed" : "Not installed"}
                      </span>
                      <span className={`rounded border px-1.5 py-0.5 ${bridgeAuthClass(bridge.auth_status)}`}>
                        Auth {bridge.auth_status.replace("_", " ")}
                      </span>
                      <span className={`rounded border px-1.5 py-0.5 ${bridgeConfidenceClass(bridge.status_confidence)}`}>
                        {bridge.status_confidence} confidence
                      </span>
                    </div>
                  </div>
                  <span className="shrink-0 rounded border border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] px-1.5 py-0.5 text-[11px] text-[var(--fx-warning)]">
                    Disabled
                  </span>
                </div>
                <div className="mt-2 space-y-1 break-words text-[11px] text-[var(--fx-text-muted)]">
                  <div>Installed: <span className="text-[var(--fx-code-text)]">{bridge.installed ? "Yes" : "No"}</span></div>
                  <div>Command: <span className="text-[var(--fx-code-text)]">{bridgeCommandLabel(bridge)}</span></div>
                  <div>Version: <span className="text-[var(--fx-code-text)]">{bridge.version ?? "Unknown"}</span></div>
                  <div>Executable found: <span className="text-[var(--fx-code-text)]">{bridge.executable_found ? "Yes" : "No"}</span></div>
                  <div>Detection: <span className="text-[var(--fx-code-text)]">{bridge.detection_classification}</span></div>
                  <div>Auth: <span className="text-[var(--fx-code-text)]">{bridge.auth_status.replace("_", " ")}</span></div>
                  <div>Auth classification: <span className="text-[var(--fx-code-text)]">{bridge.auth_classification}</span></div>
                  <div>Confidence: <span className="text-[var(--fx-code-text)]">{bridge.status_confidence}</span></div>
                  <div>Setup: <span className="text-[var(--fx-code-text)]">{bridgeSetupLabel(bridge.setup_action)}</span></div>
                  <div>{bridge.auth_message}</div>
                  {bridge.setup_hint ? <div>{bridge.setup_hint}</div> : null}
                  <div>ForgeX does not store or inspect subscription credentials.</div>
                  <div>Authentication must be handled by the official local tool.</div>
                  <div className="text-[var(--fx-warning)]">{bridge.reason}</div>
                  <div>Setup docs: <span className="break-all text-[var(--fx-code-text)]">docs/phase-2-5-2-safe-bridge-auth-status-refinement.md</span></div>
                  <div>Checked: <span className="text-[var(--fx-code-text)]">{bridge.safe_status_checked ? bridge.checked_commands.join(", ") || "safe policy" : "not checked"}</span></div>
                </div>
                {bridge.provider_id === "codex_cli_oauth_bridge" ? (
                  <div className="mt-3 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-2 text-xs text-[var(--fx-text-muted)]">
                    <div className="font-medium text-[var(--fx-text)]">Codex CLI OAuth Bridge</div>
                    <div className="mt-2 grid gap-1">
                      <div>Codex CLI installed: <span className="text-[var(--fx-code-text)]">{(codexOAuthStatus?.codex_installed ?? bridge.installed) ? "yes" : "no"}</span></div>
                      <div>Version: <span className="text-[var(--fx-code-text)]">{codexOAuthStatus?.codex_version ?? bridge.version ?? "unknown"}</span></div>
                      <div>Auth status: <span className="text-[var(--fx-code-text)]">{codexOAuthStatus?.auth_status ?? "unknown"}</span></div>
                      <div>Status runner: <span className="text-[var(--fx-code-text)]">{codexOAuthStatus?.status_runner ?? "resolved_executable_runner"}</span></div>
                      <div>OAuth bridge ready: <span className="text-[var(--fx-code-text)]">{codexOAuthStatus?.oauth_bridge_ready ? "yes" : "no"}</span></div>
                      <div>Env profile: <span className="text-[var(--fx-code-text)]">{codexOAuthStatus?.env_profile ?? codexOAuthStatus?.safe_env_profile ?? "codex_safe_user_env"}</span></div>
                      <div>Production routing: <span className="text-[var(--fx-warning)]">disabled</span></div>
                      <div>QA-only: <span className="text-[var(--fx-code-text)]">yes</span></div>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <code className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 py-1 text-[var(--fx-code-text)]">
                        {bridge.login_command ?? "codex login"}
                      </code>
                      <button
                        className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)]"
                        onClick={() => void copyCodexLoginCommand(bridge)}
                        title="Copy the official Codex CLI login command"
                      >
                        <Clipboard className="h-3.5 w-3.5" />
                        Copy Login Command
                      </button>
                      <button
                        className="flex h-7 items-center gap-1 rounded bg-[var(--fx-accent)] px-2 text-xs font-medium text-white disabled:opacity-50"
                        onClick={() => void launchCodexLogin()}
                        disabled={Boolean(busy) || !(codexOAuthStatus?.codex_installed ?? bridge.installed)}
                      >
                        {busy === "codex:login" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ExternalLink className="h-3.5 w-3.5" />}
                        Launch Codex Login
                      </button>
                      <button
                        className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                        onClick={() => void refreshCodexOAuthStatus()}
                        disabled={Boolean(busy)}
                      >
                        {busy === "codex:status" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                        Check Status Again
                      </button>
                      {process.env.NODE_ENV !== "production" ? (
                        <button
                          className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                          onClick={() => void runCodexStatusDiagnostics()}
                          disabled={Boolean(busy)}
                        >
                          {busy === "codex:diagnostics" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                          Run Status Diagnostics
                        </button>
                      ) : null}
                      <button
                        className="flex h-7 items-center gap-1 rounded border border-[var(--fx-accent)] bg-[var(--fx-panel-elevated)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                        onClick={() => void runCodexOAuthSmoke()}
                        disabled={Boolean(busy) || !codexOAuthStatus?.codex_installed || codexOAuthStatus.auth_status !== "signed_in" || !codexOAuthStatus.oauth_bridge_ready || !codexOAuthStatus.sandbox_smoke_enabled}
                      >
                        {busy === "codex:smoke" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
                        Run Sandboxed Smoke
                      </button>
                    </div>
                    {codexOAuthSmoke ? (
                      <div className="mt-2 grid gap-1 rounded border border-[var(--fx-border)] p-2">
                        <div>Smoke classification: <span className="text-[var(--fx-code-text)]">{codexOAuthSmoke.classification}</span></div>
                        {codexOAuthSmoke.classification === "CODEX_OAUTH_SMOKE_PASS" ? <div className="text-[var(--fx-success)]">Smoke passed</div> : null}
                        {codexOAuthSmoke.classification.includes("CONTENT_INVALID") ? <div className="text-[var(--fx-warning)]">Codex created the expected file, but the content did not pass validation.</div> : null}
                        <div>Expected file created: <span className="text-[var(--fx-code-text)]">{codexOAuthSmoke.expected_file_created ? "yes" : "no"}</span></div>
                        <div>Content validation valid: <span className="text-[var(--fx-code-text)]">{codexOAuthSmoke.expected_content_valid ? "yes" : "no"}</span></div>
                        <div>Mismatch kind: <span className="text-[var(--fx-code-text)]">{codexOAuthSmoke.first_difference_kind ?? "unknown"}</span></div>
                        <div>Normalization would pass: <span className="text-[var(--fx-code-text)]">{codexOAuthSmoke.normalized_content_matches ? "yes" : "no"}</span></div>
                        <div>Review created: <span className="text-[var(--fx-code-text)]">{codexOAuthSmoke.review_created ? "yes" : "no"}</span></div>
                        {codexOAuthSmoke.review_id ? <div>Review ID: <span className="text-[var(--fx-code-text)]">{codexOAuthSmoke.review_id}</span></div> : null}
                        <div>Active workspace unchanged: <span className="text-[var(--fx-code-text)]">{codexOAuthSmoke.active_workspace_unchanged ? "yes" : "no"}</span></div>
                        <div>Production routing: <span className="text-[var(--fx-warning)]">disabled</span></div>
                        {codexOAuthSmoke.classification === "CODEX_OAUTH_SMOKE_PASS" && codexOAuthSmoke.review_created && codexOAuthSmoke.review_id ? (
                          <button className="mt-1 h-7 w-fit rounded bg-[var(--fx-accent)] px-2 text-xs font-medium text-white" onClick={() => void openCodexOAuthReview()}>Open Review</button>
                        ) : null}
                      </div>
                    ) : null}
                    {codexStatusDiagnostics ? <div className="mt-2">Status diagnostics: <span className="text-[var(--fx-code-text)]">{codexStatusDiagnostics.classification}</span></div> : null}
                    {codexStatusDiagnostics?.classification === "CODEX_STATUS_DIAGNOSTICS_MISMATCH" ? <div className="mt-1 text-[var(--fx-warning)]">Normal CMD and ForgeX Codex status may be using different CLI/session context. Smoke is blocked until ForgeX status reports signed_in.</div> : null}
                    {codexOAuthStatus?.auth_status === "signed_out" ? <div className="mt-1 text-[var(--fx-warning)]">Run official Codex login again or use device-code login: codex login --device-auth</div> : null}
                    <div className="mt-2">ForgeX does not handle your Codex OAuth login. The official Codex CLI owns authentication. ForgeX only checks whether the CLI appears usable.</div>
                    <div className="mt-1">ForgeX never reads Codex tokens.</div>
                    <div className="mt-1 text-[var(--fx-warning)]">Codex project execution remains sandbox-only and disabled for production routing.</div>
                  </div>
                ) : null}
                <div className="mt-2 flex flex-wrap gap-1 text-[11px]">
                  <span className="rounded border border-[var(--fx-success)] bg-[var(--fx-success-soft)] px-1.5 py-0.5 text-[var(--fx-success)]">detect</span>
                  <span className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 py-0.5 text-[var(--fx-text-muted)]">run_prompt off</span>
                  <span className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 py-0.5 text-[var(--fx-text-muted)]">edit_files off</span>
                </div>
                {bridge.provider_id === "antigravity_cli_bridge" ? (
                  <div className="mt-3 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-2 text-xs">
                    <div className="mb-2 text-[var(--fx-text-muted)]">
                      Sandbox test:{" "}
                      <span className={agySandboxStatus?.enabled ? "text-[var(--fx-success)]" : "text-[var(--fx-warning)]"}>
                        {agySandboxStatus?.enabled ? "Available for development" : "Disabled"}
                      </span>
                    </div>
                    {!agySandboxStatus?.enabled ? (
                      <div className="mb-2 text-[var(--fx-text-muted)]">
                        AGY sandbox execution is disabled. Enable <span className="text-[var(--fx-code-text)]">FORGEX_ENABLE_AGY_BRIDGE=1</span> for development testing.
                      </div>
                    ) : null}
                    <div className="flex flex-wrap gap-2">
                      {agySandboxStatus?.enabled && bridge.installed ? (
                        <button
                          className="flex h-8 items-center gap-1 rounded bg-[var(--fx-accent)] px-2 text-xs font-medium text-white disabled:opacity-50"
                          onClick={() => void runAgySandboxTest(bridge)}
                          disabled={Boolean(busy) || !activeProject}
                        >
                          {busy === "agy:sandbox" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
                          Run Sandbox Test
                        </button>
                      ) : null}
                      {agyRun && ["pending", "running"].includes(agyRun.status) ? (
                        <button
                          className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)]"
                          onClick={() => void cancelAgyRun()}
                        >
                          <Square className="h-3.5 w-3.5" />
                          Cancel
                        </button>
                      ) : null}
                    </div>
                    {activeProject ? null : (
                      <div className="mt-2 text-[11px] text-[var(--fx-text-muted)]">Open a workspace to run a sandbox test.</div>
                    )}
                    {agyRun ? (
                      <div className="mt-2 grid gap-1 text-[11px] text-[var(--fx-text-muted)]">
                        <div>Run: <span className="text-[var(--fx-code-text)]">{agyRun.status}</span></div>
                        <div>Changed files: <span className="text-[var(--fx-code-text)]">{agyRun.changed_file_count}</span></div>
                        {agyRun.error_message ? <div className="text-[var(--fx-warning)]">{agyRun.error_message}</div> : null}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {bridge.warnings.length > 0 ? (
                  <div className="mt-2 space-y-1 text-[11px] text-[var(--fx-warning)]">
                    {bridge.warnings.map((warning) => (
                      <div key={warning}>{warning}</div>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
            {bridges.length === 0 ? (
              <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3 text-sm text-[var(--fx-text-muted)]">
                Bridge detection unavailable.
              </div>
            ) : null}
          </div>
          {agyReview ? (
            <div className="mt-3">
              <BridgeReviewPanel
                review={agyReview}
                providerName="Google Antigravity / AGY CLI"
                onApprove={(reviewId) => void approveAgyReview(reviewId)}
                onReject={(reviewId) => void rejectAgyReview(reviewId)}
                onPatchUpdated={() => void refreshPatchHistory()}
                workspaceRoot={activeProject?.project_path}
                onLog={onLog}
                busy={busy === "agy:review"}
              />
              <div className="mt-2 text-[11px] text-[var(--fx-text-muted)]">
                Approval records review state only. ForgeX does not apply AGY sandbox changes to the active workspace yet.
              </div>
            </div>
          ) : null}
        </section>

        <section className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
          <div className="mb-2 text-xs font-semibold uppercase text-[var(--fx-text-muted)]">Bridge Safety</div>
          <div className="grid gap-2 text-xs sm:grid-cols-2">
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Execution</div>
              <div className="text-[var(--fx-warning)]">Disabled</div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Review system</div>
              <div className="text-[var(--fx-success)]">Ready</div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Pending reviews</div>
              <div className="text-[var(--fx-code-text)]">{bridgeReviewCounts.pending}</div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Expired reviews</div>
              <div className={bridgeReviewCounts.expired > 0 ? "text-[var(--fx-warning)]" : "text-[var(--fx-code-text)]"}>
                {bridgeReviewCounts.expired}
              </div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Diff approval</div>
              <div className="text-[var(--fx-code-text)]">Required</div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Workspace containment</div>
              <div className="text-[var(--fx-success)]">Enabled</div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Audit logging</div>
              <div className="text-[var(--fx-success)]">Enabled</div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Persistence</div>
              <div className="text-[var(--fx-success)]">Enabled</div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Patch export</div>
              <div className="text-[var(--fx-success)]">Enabled</div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Patch integrity</div>
              <div className="text-[var(--fx-success)]">Enabled</div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Rollback snapshots</div>
              <div className="text-[var(--fx-success)]">Enabled</div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Restore preflight</div>
              <div className="text-[var(--fx-success)]">Enabled</div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Restore</div>
              <div className={bridgeSafetyStatus?.restore_enabled ? "text-[var(--fx-success)]" : "text-[var(--fx-warning)]"}>
                {bridgeSafetyStatus?.restore_enabled ? "Enabled" : "Disabled"}
              </div>
            </div>
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
              <div className="text-[var(--fx-text-muted)]">Apply changes</div>
              <div className={bridgeSafetyStatus?.patch_apply_enabled ? "text-[var(--fx-success)]" : "text-[var(--fx-warning)]"}>
                {bridgeSafetyStatus?.patch_apply_enabled ? "Enabled" : "Disabled"}
              </div>
            </div>
          </div>
          {bridgeSafetyStatus?.qa_mode_enabled ? (
            <div className="mt-2 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2 text-[11px]">
              <div className="mb-1 font-semibold uppercase text-[var(--fx-text-muted)]">QA Diagnostics</div>
              <div className="grid min-w-0 grid-cols-1 gap-1 xl:grid-cols-2 2xl:grid-cols-3">
                <QaStatus label="QA Mode" enabled={bridgeSafetyStatus.qa_mode_enabled} />
                <QaStatus label="AGY sandbox" enabled={bridgeSafetyStatus.agy_bridge_enabled} />
                <QaStatus label="Rollback restore" enabled={bridgeSafetyStatus.rollback_restore_enabled} />
                <QaStatus label="Patch apply" enabled={bridgeSafetyStatus.patch_apply_enabled} />
                <QaStatus label="Bridge routing" enabled={bridgeSafetyStatus.bridge_routing_enabled} inverted />
                <QaStatus label="Codex execution" enabled={bridgeSafetyStatus.codex_execution_enabled} inverted />
                <QaStatus label="Claude execution" enabled={bridgeSafetyStatus.claude_execution_enabled} inverted />
                <QaStatus label="OpenCode execution" enabled={bridgeSafetyStatus.opencode_execution_enabled} inverted />
                <QaStatus label="Auto-build after apply" enabled={bridgeSafetyStatus.auto_build_after_apply} inverted />
                <QaStatus label="Auto-flash after apply" enabled={bridgeSafetyStatus.auto_flash_after_apply} inverted />
              </div>
            </div>
          ) : null}
          <div className="mt-2 text-[11px] text-[var(--fx-text-muted)]">
            Recent rollback snapshots: <span className="text-[var(--fx-code-text)]">{rollbackSnapshots.length}</span>. Apply {bridgeSafetyStatus?.patch_apply_enabled ? "requires APPLY confirmation and creates a fresh rollback snapshot" : "is disabled"}.
          </div>
          <div className="mt-3 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-xs font-semibold uppercase text-[var(--fx-text-muted)]">Patch History</div>
                <div className="mt-1 text-[11px] text-[var(--fx-text-muted)]">
                  Total {patchStats.total} - Valid {patchStats.valid} - Modified {patchStats.modified} - Missing {patchStats.missing} - {formatBytes(patchStats.storage)}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                  onClick={() => void refreshPatchHistory()}
                  disabled={Boolean(busy)}
                  title="Refresh patch history"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  Refresh
                </button>
                <button
                  className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-warning)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                  onClick={() => void cleanupPatchHistory()}
                  disabled={Boolean(busy) || patchHistory.length === 0}
                  title="Clean up old or missing exported patches"
                >
                  {busy === "patch:cleanup" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                  Cleanup
                </button>
              </div>
            </div>
            {patchHistory.length === 0 ? (
              <div className="text-xs text-[var(--fx-text-muted)]">No exported bridge patches yet.</div>
            ) : (
              <div className="space-y-1">
                {patchHistory.slice(0, 6).map((patch) => (
                  <div key={patch.patch_id} className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 py-1.5 text-[11px]">
                    <div className="grid min-w-0 grid-cols-1 gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-[var(--fx-code-text)]">{patchLabel(patch)}</div>
                        <div className="truncate text-[var(--fx-text-muted)]">
                          {patch.created_at} - {formatBytes(patch.patch_size)} - SHA {patch.patch_sha256.slice(0, 12) || "unknown"}
                        </div>
                        <div className="truncate text-[var(--fx-text-muted)]">
                          Created {patch.created_files.length} - Modified {patch.modified_files.length} - Deleted {patch.deleted_files.length}
                        </div>
                      {patchPreflightResults[patch.patch_id] ? (
                        <CompactPreflightStatus result={patchPreflightResults[patch.patch_id]} />
                      ) : !activeProject ? (
                        <div className="text-[var(--fx-warning)]">Open the target workspace before running preflight.</div>
                      ) : null}
                      {rollbackSnapshotResults[patch.patch_id] ? (
                        <div className="mt-1 text-[var(--fx-text-muted)]">
                          Rollback snapshot: <span className="text-[var(--fx-code-text)]">{rollbackSnapshotResults[patch.patch_id].files_backed_up}</span> file(s) backed up - restore <span className={bridgeSafetyStatus?.restore_enabled ? "text-[var(--fx-success)]" : "text-[var(--fx-warning)]"}>{bridgeSafetyStatus?.restore_enabled ? "available after preflight" : "disabled"}</span>
                        </div>
                      ) : null}
                      {restorePreflightResults[patch.patch_id] ? (
                        <div className={restorePreflightResults[patch.patch_id].can_restore ? "mt-1 text-[var(--fx-success)]" : "mt-1 text-[var(--fx-warning)]"}>
                          Restore preflight {restorePreflightResults[patch.patch_id].can_restore ? "passed" : "blocked"} - {restorePreflightResults[patch.patch_id].conflicts.length} conflict(s)
                        </div>
                      ) : null}
                      {restoreResults[patch.patch_id] ? (
                        <div className={restoreResults[patch.patch_id].files_failed ? "mt-1 text-[var(--fx-warning)]" : "mt-1 text-[var(--fx-success)]"}>
                          Restore {restoreResults[patch.patch_id].status} - restored {restoreResults[patch.patch_id].files_restored}, removed {restoreResults[patch.patch_id].files_removed}, failed {restoreResults[patch.patch_id].files_failed}
                        </div>
                      ) : null}
                      {patchApplyResults[patch.patch_id] ? (
                        <div className={patchApplyResults[patch.patch_id].status === "applied" ? "mt-1 text-[var(--fx-success)]" : "mt-1 text-[var(--fx-warning)]"}>
                          Patch {patchApplyResults[patch.patch_id].status} - created {patchApplyResults[patch.patch_id].files_created}, modified {patchApplyResults[patch.patch_id].files_modified}, deleted {patchApplyResults[patch.patch_id].files_deleted}, rollback {patchApplyResults[patch.patch_id].rollback_id ?? "unknown"}
                        </div>
                      ) : null}
                      </div>
                      <div className="flex min-w-0 flex-wrap items-center gap-1">
                      <span className={`rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 py-0.5 ${integrityClass(patch.integrity_status)}`}>
                        {patch.integrity_status}
                      </span>
                      <button
                        className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                        onClick={() => void viewPatchReview(patch)}
                        disabled={Boolean(busy)}
                        title="View review"
                      >
                        {busy === `patch:view:${patch.patch_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileDiff className="h-3.5 w-3.5" />}
                        View
                      </button>
                      <button
                        className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                        onClick={() => void verifyPatchHistoryItem(patch)}
                        disabled={Boolean(busy)}
                        title="Verify patch integrity"
                      >
                        {busy === `patch:verify:${patch.patch_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                        Verify
                      </button>
                      <button
                        className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                        onClick={() => void copyPatchHistoryItem(patch)}
                        disabled={Boolean(busy)}
                        title="Copy patch"
                      >
                        {busy === `patch:copy:${patch.patch_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Clipboard className="h-3.5 w-3.5" />}
                        Copy
                      </button>
                      <button
                        className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                        onClick={() => void openPatchHistoryFolder(patch)}
                        disabled={Boolean(busy)}
                        title="Open patch folder"
                      >
                        {busy === `patch:open:${patch.patch_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ExternalLink className="h-3.5 w-3.5" />}
                        Folder
                      </button>
                      <button
                        className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                        onClick={() => void preflightPatchHistoryItem(patch)}
                        disabled={Boolean(busy) || !activeProject?.project_path}
                        title={activeProject?.project_path ? "Run read-only patch preflight" : "Open the target workspace before running preflight"}
                      >
                        {busy === `patch:preflight:${patch.patch_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                        Preflight
                      </button>
                      <button
                        className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                        onClick={() => void createRollbackSnapshotForPatch(patch)}
                        disabled={Boolean(busy) || !patchPreflightResults[patch.patch_id]?.can_apply || !activeProject?.project_path}
                        title="Create rollback snapshot in ForgeX state"
                      >
                        {busy === `patch:rollback:${patch.patch_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                        Create Rollback Snapshot
                      </button>
                      <button
                        className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                        onClick={() => void preflightRestoreForPatch(patch)}
                        disabled={Boolean(busy) || !rollbackSnapshotResults[patch.patch_id] || !activeProject?.project_path}
                        title="Run read-only restore preflight"
                      >
                        {busy === `patch:restore-preflight:${patch.patch_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                        Restore Preflight
                      </button>
                      {bridgeSafetyStatus?.restore_enabled && restorePreflightResults[patch.patch_id]?.can_restore ? (
                        <button
                          className="flex h-7 items-center gap-1 rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] px-1.5 text-[var(--fx-error)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                          onClick={() => void restoreSnapshotForPatch(patch)}
                          disabled={Boolean(busy) || !activeProject?.project_path}
                          title="Restore files from rollback snapshot"
                        >
                          {busy === `patch:restore:${patch.patch_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                          Restore Snapshot
                        </button>
                      ) : (
                        <button
                          className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-warning)] opacity-70"
                          disabled
                          title={bridgeSafetyStatus?.restore_enabled ? "Run passing restore preflight first" : "Rollback restore remains disabled"}
                        >
                          Restore disabled
                        </button>
                      )}
                      {bridgeSafetyStatus?.patch_apply_enabled && patchPreflightResults[patch.patch_id]?.can_apply && patchPreflightResults[patch.patch_id]?.apply_enabled && patch.review_status_at_export === "approved" && patch.integrity_status === "valid" && activeProject?.project_path ? (
                        <button
                          className="flex h-7 items-center gap-1 rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] px-1.5 text-[var(--fx-error)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                          onClick={() => void applyPatchHistoryItem(patch)}
                          disabled={Boolean(busy)}
                          title="Apply patch to active workspace"
                        >
                          {busy === `patch:apply:${patch.patch_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                          Apply Patch
                        </button>
                      ) : (
                        <button
                          className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-warning)] opacity-70"
                          disabled
                          title={bridgeSafetyStatus?.patch_apply_enabled ? "Run passing preflight first" : "Patch apply is disabled"}
                        >
                          Apply disabled
                        </button>
                      )}
                      <button
                        className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-warning)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                        onClick={() => void deletePatchHistoryItem(patch)}
                        disabled={Boolean(busy)}
                        title="Delete exported patch"
                      >
                        {busy === `patch:delete:${patch.patch_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                        Delete
                      </button>
                      </div>
                    </div>
                    {patchPreflightResults[patch.patch_id] && expandedPreflightPatchId === patch.patch_id ? (
                      <div className="mt-2">
                        <PatchPreflightReport result={patchPreflightResults[patch.patch_id]} compact defaultExpanded />
                      </div>
                    ) : null}
                    </div>
                ))}
              </div>
            )}
            <div className="mt-2 text-[11px] text-[var(--fx-text-muted)]">
              Patch deletion removes exported patch files and metadata only. Apply never triggers build or flash automatically.
            </div>
          </div>

          <div className="mt-3 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-xs font-semibold uppercase text-[var(--fx-text-muted)]">Patch Apply History</div>
                <div className="mt-1 text-[11px] text-[var(--fx-text-muted)]">
                  Apply records are local and feature-flagged. No build or flash is triggered after apply.
                </div>
                <div className="mt-1 flex flex-wrap gap-1 text-[11px] text-[var(--fx-text-muted)]">
                  <span>Total applies: <span className="text-[var(--fx-code-text)]">{applyStats.total}</span></span>
                  <span>Applied: <span className="text-[var(--fx-success)]">{applyStats.applied}</span></span>
                  <span>Restored: <span className="text-[var(--fx-success)]">{applyStats.restored}</span></span>
                  <span>Restore failed: <span className={applyStats.restoreFailed ? "text-[var(--fx-error)]" : "text-[var(--fx-code-text)]"}>{applyStats.restoreFailed}</span></span>
                  <span>Failed: <span className={applyStats.failed ? "text-[var(--fx-warning)]" : "text-[var(--fx-code-text)]"}>{applyStats.failed}</span></span>
                  <span>Failed rolled back: <span className="text-[var(--fx-code-text)]">{applyStats.failedRolledBack}</span></span>
                  <span>Rollback available: <span className="text-[var(--fx-code-text)]">{applyStats.rollbackAvailable}</span></span>
                </div>
              </div>
              <button
                className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                onClick={() => void refreshPatchApplies()}
                disabled={Boolean(busy) || patchAppliesLoading}
                title="Refresh apply history"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${patchAppliesLoading ? "animate-spin" : ""}`} />
                Refresh
              </button>
            </div>
            {patchAppliesError ? (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] p-2 text-xs text-[var(--fx-error)]">
                <span>Apply history unavailable. {patchAppliesError}</span>
                <button className="h-7 rounded border border-[var(--fx-error)] px-2" onClick={() => void refreshPatchApplies()}>
                  Retry
                </button>
              </div>
            ) : patchAppliesLoading ? (
              <div className="flex items-center gap-2 text-xs text-[var(--fx-text-muted)]">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Loading patch apply history...
              </div>
            ) : patchApplies.length === 0 ? (
              <div className="text-xs text-[var(--fx-text-muted)]">
                No patch applies yet. Apply is available only after preflight passes and feature flags are enabled.
              </div>
            ) : (
              <div className="space-y-1">
                {patchApplies.slice(0, 7).map((apply) => {
                  const persistedRestore = apply.rollback_id ? restoreByRollbackId[apply.rollback_id] : null;
                  const displayState = patchApplyDisplayState(apply, persistedRestore);
                  const alreadyRestored = persistedRestore?.status === "restored";
                  return (
                  <div key={apply.apply_id} className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 py-1.5 text-[11px]">
                    <div className="grid min-w-0 grid-cols-1 gap-2 lg:grid-cols-[minmax(0,1fr)_auto]">
                      <div className="min-w-0">
                        <div className="flex min-w-0 flex-wrap items-center gap-1 text-[var(--fx-code-text)]">
                          <span className="min-w-0 truncate">{applyLabel(apply)}</span>
                          <span className={`shrink-0 rounded border px-1.5 py-0.5 ${applyToneClass(displayState.tone)}`}>{displayState.label}</span>
                          <span className="shrink-0 text-[var(--fx-text-muted)]">{formatRelativeTime(apply.completed_at)}</span>
                        </div>
                        <div className="truncate text-[var(--fx-text-muted)]">
                          Patch {apply.patch_id} - rollback {apply.rollback_id ?? "none"}
                        </div>
                        {displayState.reason ? <div className="truncate text-[var(--fx-error)]" title={displayState.reason}>{displayState.reason}</div> : null}
                      </div>
                      <div className="flex flex-wrap items-center gap-1 lg:justify-end">
                        <button
                          className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                          onClick={() => void openPatchApplyDetail(apply.apply_id)}
                          disabled={Boolean(busy)}
                        >
                          Details
                        </button>
                        <button
                          className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                          onClick={() => apply.rollback_id ? void openApplyRollbackSnapshot(apply.rollback_id) : undefined}
                          disabled={Boolean(busy) || !apply.rollback_id}
                        >
                          Open rollback snapshot
                        </button>
                        <button
                          className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                          onClick={() => void preflightRestoreForApply(apply)}
                          disabled={Boolean(busy) || !apply.rollback_id || !activeProject?.project_path}
                        >
                          Restore Preflight
                        </button>
                        <button
                          className="flex h-7 items-center gap-1 rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] px-1.5 text-[var(--fx-error)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
                          onClick={() => void restoreSnapshotForApply(apply)}
                          disabled={Boolean(busy) || alreadyRestored || !apply.rollback_id || !applyRestorePreflightResults[apply.apply_id]?.can_restore || !bridgeSafetyStatus?.restore_enabled || !activeProject?.project_path}
                          title={alreadyRestored ? "This rollback snapshot has already been restored." : bridgeSafetyStatus?.restore_enabled ? "Requires passing restore preflight and RESTORE confirmation" : "Rollback restore is disabled"}
                        >
                          Restore Snapshot
                        </button>
                      </div>
                    </div>
                  </div>
                  );
                })}
              </div>
            )}
            {patchApplyDetailLoading ? (
              <div className="mt-2 flex items-center gap-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-2 text-xs text-[var(--fx-text-muted)]">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Loading apply detail...
              </div>
            ) : patchApplyDetailError ? (
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] p-2 text-xs text-[var(--fx-error)]">
                <span>Apply detail unavailable. {patchApplyDetailError}</span>
                {selectedPatchApplyId ? (
                  <button className="h-7 rounded border border-[var(--fx-error)] px-2" onClick={() => void openPatchApplyDetail(selectedPatchApplyId)}>
                    Retry
                  </button>
                ) : null}
              </div>
            ) : selectedPatchApply ? (
              <div className="mt-2">
                <PatchApplyDetail
                  apply={selectedPatchApply}
                  safetyStatus={bridgeSafetyStatus}
                  restorePreflight={applyRestorePreflightResults[selectedPatchApply.apply_id] ?? null}
                  restoreResult={applyRestoreResults[selectedPatchApply.apply_id] ?? (selectedPatchApply.rollback_id ? restoreByRollbackId[selectedPatchApply.rollback_id] : null) ?? null}
                  busy={Boolean(busy)}
                  onOpenRollback={(rollbackId) => void openApplyRollbackSnapshot(rollbackId)}
                  onRestorePreflight={() => void preflightRestoreForApply(selectedPatchApply)}
                  onRestoreSnapshot={() => void restoreSnapshotForApply(selectedPatchApply)}
                />
              </div>
            ) : null}
          </div>
        </section>

        <TaskRouteEditor
          routeForms={routeForms}
          providers={configurableProviders}
          providerById={providerById}
          modelsByProvider={modelsByProvider}
          busy={busy}
          updateRouteForm={updateRouteForm}
          saveRoute={saveRoute}
        />

        <section className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <div className="text-xs font-semibold uppercase text-[var(--fx-text-muted)]">Unified Coding Workflow</div>
              <div className="mt-1 text-xs text-[var(--fx-text-muted)]">
                Experimental fake-provider workflow controls are available from the main IDE Forge panel under Coding Agent.
              </div>
            </div>
            <span className="rounded border border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] px-2 py-1 text-[11px] text-[var(--fx-warning)]">
              Experimental
            </span>
          </div>
        </section>

        <ProviderDiagnosticsPanel
          usage={usage}
          generationRuns={generationRuns}
          diagnosticsFilter={diagnosticsFilter}
          busy={busy}
          refreshUsage={refreshUsage}
          refreshDiagnostics={refreshDiagnostics}
          clearDiagnostics={clearDiagnostics}
          setDiagnosticsFilter={setDiagnosticsFilter}
        />
        {message?.toLowerCase().includes("failed") || message?.toLowerCase().includes("could not") ? (
          <div className="flex gap-2 rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] p-2 text-xs text-[var(--fx-error)]">
            <CircleAlert className="h-3.5 w-3.5 shrink-0" />
            <span>{message}</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
