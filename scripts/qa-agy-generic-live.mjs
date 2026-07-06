import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import {
  READINESS,
  assertSanitizedPublicPayload,
  baselineDigest,
  compareBaselines,
  detectInstalledVersion,
  fastCompletionClassification,
  flagSupportMissing,
  guardThrowawayWorkspace,
  locateAgy,
  missingLiveFlags,
  orderedEvents,
  selectReadinessStatus,
  snapshotWorkspace,
  terminalEventCount,
} from "./qa-agy-generic-live-core.mjs";
import { guardActiveWorkspace, guardTrustedWorkspace, trustedWorkspacePath } from "./qa-agy-trusted-workspace-core.mjs";

const LIVE_INSTRUCTION = `Inside the current working directory, create a new file named AGY_GENERIC_SMOKE.txt.

The file content must be exactly:
ForgeX generic AGY smoke test completed.

Only create this one file.
Do not modify any existing file.
Do not modify platformio.ini.
Do not modify package files.
Do not modify source files.
Do not run build commands.
Do not install dependencies.
Do not access the network.`;
const TERMINAL = new Set(["completed", "failed", "cancelled", "blocked", "timed_out", "interrupted"]);
const repositoryRoot = path.resolve(import.meta.dirname, "..");
const throwawayRoot = path.resolve(repositoryRoot, "workspace", "forgex-apply-test");
const evidencePath = path.join(repositoryRoot, ".promptforge", "state", "phase-2-5-8-6-live-agy-validation.json");
const checkOnly = process.argv.includes("--check-only");
const confirmed = process.argv.includes("--confirm-real-agy");
const trustedWorkspaceAttested = process.argv.includes("--trusted-workspace-attested");
const cancelSmoke = process.argv.includes("--cancel-smoke");
const timeoutSmoke = process.argv.includes("--timeout-smoke");

if (cancelSmoke && timeoutSmoke) finish("BLOCKED_UNKNOWN_SAFE_REASON", { reason: "conflicting_live_modes", real_execution_count: 0 }, 2);
if (checkOnly && (confirmed || cancelSmoke || timeoutSmoke)) finish("BLOCKED_UNKNOWN_SAFE_REASON", { reason: "check_only_mode_conflict", real_execution_count: 0 }, 2);

const readiness = await readinessProbe();
if (checkOnly) finish(readiness.status, readiness.public, readiness.status === READINESS.READY ? 0 : 2);
if (!confirmed) finish("BLOCKED_OPERATOR_DID_NOT_AUTHORIZE", { ...readiness.public, real_execution_count: 0 }, 2);
if (readiness.status !== READINESS.READY) finish(readiness.status, { ...readiness.public, real_execution_count: 0 }, 2);

console.log("This will execute one real AGY process in a ForgeX-managed sandbox only.");
console.log("The active workspace must remain unchanged.");
console.log("No patch will be applied automatically.");

await runLive(readiness);

async function readinessProbe() {
  const supportSource = [
    ".env.example",
    "backend/bridges/generic/api_policy.py",
    "backend/bridges/agy_execution_router.py",
    "backend/api/routes/models.py",
  ].map((name) => fs.readFileSync(path.join(repositoryRoot, ...name.split("/")), "utf8")).join("\n");
  const unsupportedFlags = flagSupportMissing(supportSource);
  const disabledFlags = missingLiveFlags(process.env);

  let workspaceGuard;
  let trustedWorkspaceGuard;
  try {
    const activeWorkspace = guardActiveWorkspace(repositoryRoot, throwawayRoot);
    workspaceGuard = { ok: true, workspaceRoot: activeWorkspace };
  } catch {
    workspaceGuard = { ok: false, reason: "workspace_guard_failed" };
  }
  try {
    trustedWorkspaceGuard = guardTrustedWorkspace(
      repositoryRoot,
      throwawayRoot,
      trustedWorkspacePath(repositoryRoot, throwawayRoot),
    );
  } catch (error) {
    trustedWorkspaceGuard = {
      ok: false,
      reason: error instanceof Error && /^[a-z0-9_]+$/i.test(error.message) ? error.message : "trusted_workspace_guard_failed",
    };
  }

  const installation = locateAgy(spawnSync);
  const version = installation.installed ? detectInstalledVersion(installation.resolvedPath, spawnSync) : null;
  const authentication = process.env.FORGEX_AGY_AUTHENTICATED === "1" ? "attested" : "not_attested";
  const apiBase = loopbackBase(process.env.PROMPTFORGE_API_URL || "http://127.0.0.1:8000");
  const backend = apiBase ? await backendReadiness(apiBase) : { ready: false, reason: "non_loopback_backend_rejected" };
  const agentUi = await optionalAgentUiReadiness();

  const classification = selectReadinessStatus({
    unsupportedFlags,
    disabledFlags,
    workspaceOk: workspaceGuard.ok,
    workspaceReason: workspaceGuard.reason,
    trustedWorkspacePrepared: trustedWorkspaceGuard.ok,
    trustedWorkspaceReason: trustedWorkspaceGuard.reason,
    trustAttested: trustedWorkspaceAttested,
    installed: installation.installed,
    authenticated: authentication === "attested",
    version,
    backendReady: backend.ready,
    backendReason: backend.reason,
    agentUiRequired: agentUi.required,
    agentUiReady: agentUi.ready,
  });
  const { status, reason } = classification;

  return {
    status,
    apiBase,
    workspaceRoot: workspaceGuard.ok ? workspaceGuard.workspaceRoot : null,
    trustedWorkspaceRoot: trustedWorkspaceGuard.ok ? trustedWorkspaceGuard.workspaceRoot : null,
    public: {
      reason,
      qa_mode: process.env.FORGEX_QA_MODE === "1",
      live_flags_enabled: disabledFlags.length === 0,
      flag_support_available: unsupportedFlags.length === 0,
      workspace_guard: workspaceGuard.ok ? "pass" : "blocked",
      trusted_workspace_guard: trustedWorkspaceGuard.ok ? "pass" : "blocked",
      trusted_workspace_trust: trustedWorkspaceAttested ? "operator_attested_for_run" : "operator_attestation_required",
      agy_installation: installation.installed ? "installed" : "not_installed",
      agy_version: version || "not_detected",
      agy_authentication: authentication,
      backend: backend.ready ? "ready" : "unready",
      agent_ui: agentUi.ready ? "ready" : agentUi.required ? "required_unready" : "not_required_unavailable",
      real_execution_count: 0,
    },
  };
}

async function backendReadiness(apiBase) {
  try {
    const health = await fetchJson(apiBase, "/health", undefined, 4_000);
    const safety = await fetchJson(apiBase, "/models/bridges/safety-status", undefined, 4_000);
    const ready = health.status === "healthy"
      && health.service === "forgex-backend"
      && safety.qa_mode_enabled === true
      && safety.agy_bridge_enabled === true
      && safety.public_generic_run_api_enabled === true
      && safety.generic_bridge_routing_enabled === true
      && safety.agy_generic_provider_enabled === true
      && safety.agy_effective_execution_mode === "generic"
      && safety.agy_trusted_workspace_enabled === true;
    return { ready, reason: ready ? "ready" : "backend_live_gates_unready" };
  } catch {
    return { ready: false, reason: "backend_unreachable" };
  }
}

async function optionalAgentUiReadiness() {
  const required = process.env.FORGEX_REQUIRE_AGENT_UI === "1";
  const port = Number.parseInt(process.env.FORGEX_FRONTEND_PORT || "3127", 10);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return { required, ready: false };
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2_000);
    const response = await fetch(`http://127.0.0.1:${port}/`, { signal: controller.signal });
    const text = await response.text();
    clearTimeout(timer);
    return { required, ready: response.ok && text.includes("ForgeX") };
  } catch {
    return { required, ready: false };
  }
}

async function runLive(readiness) {
  const before = snapshotWorkspace(readiness.workspaceRoot);
  if (before.some((item) => item.relative_path === "AGY_GENERIC_SMOKE.txt")) {
    finish("FAIL", { reason: "active_workspace_already_contains_smoke_artifact", real_execution_count: 0 }, 1);
  }
  const managed = managedQaPaths(repositoryRoot);
  const sandboxesBefore = directoryNames(managed.sandboxes);
  const auditBefore = jsonLineCount(managed.audit);
  let realExecutionCount = 0;

  try {
    const imported = await fetchJson(readiness.apiBase, "/projects/import", {
      method: "POST",
      body: JSON.stringify({ path: readiness.workspaceRoot }),
    });
    const genericBefore = await fetchJson(readiness.apiBase, `/models/bridges/generic/runs?project_id=${encodeURIComponent(imported.project_id)}&limit=100`);
    const compatibilityBefore = await fetchJson(readiness.apiBase, "/models/bridges/runs");
    const timeoutSeconds = timeoutSmoke ? 10 : 300;
    const started = await fetchJson(readiness.apiBase, "/models/bridges/runs", {
      method: "POST",
      body: JSON.stringify({
        provider_id: "agy",
        project_id: imported.project_id,
        instruction: LIVE_INSTRUCTION,
        timeout_seconds: timeoutSeconds,
        idempotency_key: `agy-live-${crypto.randomUUID()}`,
      }),
    });
    realExecutionCount = 1;
    assertSanitizedPublicPayload(started);
    const eventPromise = readSseEvents(readiness.apiBase, started.run_id, (timeoutSeconds + 30) * 1_000);

    let cancellation = "NOT_RUN";
    if (cancelSmoke) {
      const cancellable = await waitForCancellable(readiness.apiBase, started.run_id, 15_000);
      if (!cancellable) cancellation = "BLOCKED_FAST_COMPLETION";
      else {
        const cancelled = await fetchJson(readiness.apiBase, `/models/bridges/generic/runs/${encodeURIComponent(started.run_id)}/cancel`, { method: "POST", body: "{}" });
        assertSanitizedPublicPayload(cancelled);
        cancellation = cancelled.disposition === "accepted" || cancelled.disposition === "already_terminal" ? "REQUESTED" : "FAIL";
      }
    }

    const detail = await waitForTerminal(readiness.apiBase, started.run_id, (timeoutSeconds + 30) * 1_000);
    assertSanitizedPublicPayload(detail);
    const stream = await eventPromise;
    assertSanitizedPublicPayload(stream.events);
    const after = snapshotWorkspace(readiness.workspaceRoot);
    const workspaceUnchanged = compareBaselines(before, after) && !fs.existsSync(path.join(readiness.workspaceRoot, "AGY_GENERIC_SMOKE.txt"));
    const genericAfter = await fetchJson(readiness.apiBase, `/models/bridges/generic/runs?project_id=${encodeURIComponent(imported.project_id)}&limit=100`);
    const compatibilityAfter = await fetchJson(readiness.apiBase, "/models/bridges/runs");
    const matching = genericAfter.runs.filter((item) => item.run_id === started.run_id);
    const sandboxesAfter = directoryNames(managed.sandboxes);
    const sandboxDelta = sandboxesAfter.filter((item) => !sandboxesBefore.has(item));
    const compatibilityDelta = compatibilityAfter.runs.length - compatibilityBefore.runs.length;
    const genericDelta = genericAfter.count - genericBefore.count;
    const eventSafety = orderedEvents(stream.events) && terminalEventCount(stream.events) === 1;

    let pipeline = { review: "not_created", patch_export: "not_run", integrity: "not_run", preflight: "not_run" };
    if (detail.status === "completed" && detail.review_id) {
      pipeline = await validateReviewPipeline(readiness.apiBase, detail.review_id, readiness.workspaceRoot);
    }

    if (cancelSmoke) cancellation = cancellation === "BLOCKED_FAST_COMPLETION" ? cancellation : fastCompletionClassification(detail.status, "cancelled");
    const timeout = timeoutSmoke ? fastCompletionClassification(detail.status, "timed_out") : "NOT_RUN";
    const auditAfter = jsonLineCount(managed.audit);
    const commonPass = matching.length === 1
      && genericDelta === 1
      && compatibilityDelta === 1
      && sandboxDelta.length === 0
      && workspaceUnchanged
      && eventSafety;
    const smokePass = !cancelSmoke && !timeoutSmoke && detail.status === "completed" && pipeline.review === "pass" && pipeline.integrity === "pass";
    const optionalPass = cancelSmoke ? cancellation !== "FAIL" : timeoutSmoke ? timeout !== "FAIL" : smokePass;
    const status = commonPass && optionalPass ? "PASS" : "FAIL";

    finish(status, {
      reason: status === "PASS" ? "live_validation_complete" : "live_validation_invariant_failed",
      mode: cancelSmoke ? "cancellation" : timeoutSmoke ? "timeout" : "smoke",
      real_execution_count: realExecutionCount,
      run_id: started.run_id,
      review_id: detail.review_id || null,
      provider_id: detail.provider_id,
      terminal_status: detail.status,
      generic_run_delta: genericDelta,
      compatibility_run_delta: compatibilityDelta,
      sandbox_delta: sandboxDelta.length,
      trusted_workspace_reused: true,
      process_count_inferred_from_compatibility_run: compatibilityDelta,
      baseline_before_digest: baselineDigest(before),
      baseline_after_digest: baselineDigest(after),
      active_workspace_unchanged: workspaceUnchanged,
      events_ordered: orderedEvents(stream.events),
      heartbeat_observed: stream.heartbeats > 0,
      terminal_event_count: terminalEventCount(stream.events),
      review_pipeline: pipeline,
      cancellation,
      timeout,
      audit_records_added: auditAfter - auditBefore,
      audit_log_safety: true,
      automatic_apply: false,
      automatic_build: false,
      automatic_flash: false,
      legacy_fallback: false,
    }, status === "PASS" ? 0 : 1);
  } catch (error) {
    const after = snapshotWorkspace(readiness.workspaceRoot);
    finish("FAIL", {
      reason: safeFailure(error),
      real_execution_count: realExecutionCount,
      active_workspace_unchanged: compareBaselines(before, after),
      automatic_apply: false,
      automatic_build: false,
      automatic_flash: false,
      legacy_fallback: false,
    }, 1);
  }
}

async function validateReviewPipeline(apiBase, reviewId, workspaceRoot) {
  const reviewResponse = await fetchJson(apiBase, `/models/bridges/reviews/${encodeURIComponent(reviewId)}`);
  const changed = Array.isArray(reviewResponse.review?.changed_files) ? reviewResponse.review.changed_files : [];
  const safeNames = changed.map((item) => item.path).filter((item) => typeof item === "string" && !path.isAbsolute(item));
  const reviewPass = safeNames.length === 1 && safeNames[0] === "AGY_GENERIC_SMOKE.txt";
  const exported = await fetchJson(apiBase, `/models/bridges/reviews/${encodeURIComponent(reviewId)}/export-patch`, { method: "POST", body: "{}" });
  const verified = await fetchJson(apiBase, `/models/bridges/reviews/${encodeURIComponent(reviewId)}/verify-patch`, { method: "POST", body: "{}" });
  const patchId = exported.patch?.patch_id;
  if (typeof patchId !== "string") throw new Error("patch_export_failed");
  const preflight = await fetchJson(apiBase, `/models/bridges/patches/${encodeURIComponent(patchId)}/preflight`, {
    method: "POST",
    body: JSON.stringify({ workspace_root: workspaceRoot }),
  });
  const safety = await fetchJson(apiBase, "/models/bridges/safety-status");
  return {
    review: reviewPass ? "pass" : "fail",
    changed_file_count: safeNames.length,
    changed_files: safeNames,
    patch_export: "pass",
    integrity: verified.patch?.integrity_valid === true || verified.patch?.integrity_status === "valid" ? "pass" : "fail",
    preflight: preflight.safe_to_apply === true ? "safe" : "safely_blocked",
    apply_enabled: safety.patch_apply_enabled === true,
  };
}

async function waitForCancellable(apiBase, runId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const detail = await fetchJson(apiBase, `/models/bridges/generic/runs/${encodeURIComponent(runId)}`);
    if (TERMINAL.has(detail.status)) return false;
    if (detail.cancellable) return true;
    await delay(100);
  }
  return false;
}

async function waitForTerminal(apiBase, runId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const detail = await fetchJson(apiBase, `/models/bridges/generic/runs/${encodeURIComponent(runId)}`);
    if (TERMINAL.has(detail.status)) return detail;
    await delay(500);
  }
  throw new Error("run_terminal_timeout");
}

async function readSseEvents(apiBase, runId, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const events = [];
  let heartbeats = 0;
  try {
    const response = await fetch(`${apiBase}/models/bridges/runs/${encodeURIComponent(runId)}/events`, { signal: controller.signal });
    if (!response.ok || !response.body) throw new Error("sse_unavailable");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() || "";
      for (const frame of frames) {
        if (frame.trim().startsWith(":")) {
          heartbeats += 1;
          continue;
        }
        const data = frame.split(/\r?\n/).find((line) => line.startsWith("data:"));
        if (data) events.push(JSON.parse(data.slice(5).trim()));
      }
    }
    return { events, heartbeats };
  } finally {
    clearTimeout(timeout);
  }
}

async function fetchJson(apiBase, route, init, timeoutMs = 10_000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${apiBase}${route}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(`api_${response.status}_${safeCode(body.code)}`);
    return body;
  } finally {
    clearTimeout(timeout);
  }
}

function managedQaPaths(repoRoot) {
  const configured = process.env.PROMPTFORGE_ROOT ? path.resolve(process.env.PROMPTFORGE_ROOT) : repoRoot;
  const relative = path.relative(repoRoot, configured).replaceAll("\\", "/");
  if (configured !== repoRoot && !relative.startsWith(".promptforge/qa/phase-2-5-8-6")) throw new Error("managed_state_root_not_allowlisted");
  const state = path.join(configured, ".promptforge", "state");
  return { sandboxes: path.join(state, "bridge-sandboxes"), audit: path.join(state, "bridge-review-audit.jsonl") };
}

function directoryNames(root) {
  if (!fs.existsSync(root)) return new Set();
  return new Set(fs.readdirSync(root, { withFileTypes: true }).filter((item) => item.isDirectory() && !item.isSymbolicLink()).map((item) => item.name));
}

function jsonLineCount(file) {
  if (!fs.existsSync(file)) return 0;
  return fs.readFileSync(file, "utf8").split(/\r?\n/).filter((line) => line.trim()).length;
}

function loopbackBase(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) return null;
    return url.origin;
  } catch {
    return null;
  }
}

function safeCode(value) {
  return typeof value === "string" && /^[A-Za-z0-9_.:-]{1,64}$/.test(value) ? value : "unknown";
}

function safeFailure(error) {
  const message = error instanceof Error ? error.message : "unknown";
  return /^[A-Za-z0-9_.:-]{1,128}$/.test(message) ? message : "unknown_safe_failure";
}

function finish(status, details, exitCode) {
  const evidence = { phase: "2.5.8.6", status, ...details, recorded_at: new Date().toISOString() };
  const serialized = JSON.stringify(evidence);
  if (/[A-Za-z]:\\/.test(serialized) || serialized.includes(repositoryRoot)) {
    console.error("BLOCKED_UNKNOWN_SAFE_REASON");
    process.exit(2);
  }
  fs.mkdirSync(path.dirname(evidencePath), { recursive: true });
  fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
  console.log(status);
  console.log(`AGY installation: ${details.agy_installation || "not_checked"}`);
  console.log(`AGY authentication: ${details.agy_authentication || "not_checked"}`);
  console.log(`Backend: ${details.backend || "not_checked"}`);
  console.log(`Real execution count: ${details.real_execution_count ?? 0}`);
  process.exit(exitCode);
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
