import { spawn, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import path from "node:path";

import { assertContained, packagedFlagMatrix, qaFixturesAllowed } from "./qa-packaged-helpers.mjs";

const repoRoot = process.cwd();
const qaRoot = path.resolve(repoRoot, ".promptforge", "qa", "phase-2-5-8-5-1");
const packageRoot = path.join(qaRoot, "package", "win-unpacked");
const isolatedRoot = path.join(qaRoot, "app-root");
const stateDirectory = path.join(isolatedRoot, ".promptforge", "state");
const logsDirectory = path.join(qaRoot, "logs");
const userDataDirectory = path.join(qaRoot, "electron-user-data");
const launchDirectory = path.join(qaRoot, "launch-cwd");
const evidencePath = path.join(repoRoot, ".promptforge", "state", "phase-2-5-8-5-1-packaged-readiness.json");
const screenshotsDirectory = path.join(repoRoot, "docs", "images", "phase-2-5-8-5");
const backendPort = numberEnv("FORGEX_PACKAGED_QA_BACKEND_PORT", 8127);
const frontendPort = numberEnv("FORGEX_PACKAGED_QA_FRONTEND_PORT", 3127);
const debuggingPort = numberEnv("FORGEX_PACKAGED_QA_DEBUG_PORT", 9227);
const smokeMode = process.argv.includes("--smoke");
const resetMode = process.argv.includes("--reset");
const noFixtures = process.argv.includes("--no-fixtures");
const reusePackage = process.argv.includes("--reuse-package");
const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
process.env.FORGEX_QA_MODE = "1";
let activeEvidence = null;
let activeSession = null;
let startupPhase = "initializing";

main().catch(async (error) => {
  if (activeSession) await stopPackaged(activeSession).catch(() => {});
  recordFailureEvidence(error);
  console.error(`[packaged-qa] FAIL ${safeMessage(error)}`);
  process.exitCode = 1;
});

async function main() {
  assertContained(qaRoot, path.resolve(repoRoot, ".promptforge", "qa"));
  if (resetMode && fs.existsSync(qaRoot)) {
    assertContained(qaRoot, path.resolve(repoRoot, ".promptforge", "qa"));
    fs.rmSync(qaRoot, { recursive: true, force: true });
    console.log("[packaged-qa] Isolated QA state reset");
  }
  for (const directory of [qaRoot, isolatedRoot, stateDirectory, logsDirectory, userDataDirectory, launchDirectory, screenshotsDirectory, path.dirname(evidencePath)]) {
    fs.mkdirSync(directory, { recursive: true });
  }

  startupPhase = "checking_ports";
  await assertPortAvailable(backendPort, "backend");
  await assertPortAvailable(frontendPort, "frontend");
  await assertPortAvailable(debuggingPort, "debugging");
  startupPhase = "building_package";
  if (reusePackage) {
    if (!fs.existsSync(path.join(packageRoot, "ForgeX-QA.exe"))) throw new Error("Reusable packaged QA executable is unavailable.");
  } else {
    await buildAndStagePackage();
  }
  if (!noFixtures) seedQaFixtures();

  const executable = packagedExecutable();
  const executableIdentity = {
    name: path.basename(executable),
    sha256: sha256File(executable),
  };
  const evidence = {
    started_at: new Date().toISOString(),
    mode: "packaged",
    platform: process.platform,
    executable: executableIdentity,
    electron_started: false,
    renderer_ready: false,
    backend_ready: false,
    frontend_ready: false,
    restart_completed: false,
    history_persisted: false,
    detail_persisted: false,
    restore_state_persisted: false,
    feature_flags: { patch_apply: false, rollback_restore: false },
    flag_matrix: [],
    fixture_count: noFixtures ? 0 : fixtureApplies().length,
    malformed_record_ignored: false,
    missing_detail_normalized: false,
    log_safety: false,
    smoke_mode: smokeMode,
    backend_command_role: "python_fastapi_backend",
    backend_port: backendPort,
    backend_health_url: `http://127.0.0.1:${backendPort}/health`,
    frontend_port: frontendPort,
    frontend_health_url: `http://127.0.0.1:${frontendPort}/`,
    electron_pid: null,
    packaged_executable_path: `<qa-package>/${path.basename(executable)}`,
    packaged_mode_flag: true,
    resource_root: "<qa-package>",
    startup_phase: "package_staged",
    health_failure_reason: null,
    last_backend_stdout_preview: [],
    last_backend_stderr_preview: [],
    sessions: [],
    generic_api: {
      available: false,
      disabled_by_default: false,
      provider_scope: null,
      providers_reachable: false,
      runs_list_reachable: false,
      run_detail_reachable: false,
      start_disabled_safely: false,
    },
    generic_sse: {
      available: false,
      replay: false,
      heartbeat: false,
      terminal_close: false,
      invalid_run_safe: false,
      resync_required: false,
      fallback_polling_no_duplicates: false,
    },
    bridge_safety_reachable: false,
    agent_ui: { rendered: false, dom_states: [], screenshots: [] },
    live_agy_smoke: { status: "BLOCKED", reason: "operator did not authorize", real_provider_executions: 0 },
  };
  activeEvidence = evidence;

  const configurations = smokeMode ? [packagedFlagMatrix()[0]] : packagedFlagMatrix();

  let baselineIds = [];
  for (const [index, configuration] of configurations.entries()) {
    startupPhase = `launching_${configuration.name}`;
    const session = await launchPackaged(executable, configuration, index + 1);
    try {
      evidence.electron_started = true;
      evidence.backend_ready = session.backendReady;
      evidence.frontend_ready = session.frontendReady;
      evidence.renderer_ready = session.rendererReady;
      const safety = await getJson(`${session.backendUrl}/models/bridges/safety-status`);
      evidence.bridge_safety_reachable = true;
      evidence.flag_matrix.push({
        name: configuration.name,
        patch_apply_feature_flag: safety.patch_apply_feature_flag,
        rollback_restore_feature_flag: safety.rollback_restore_feature_flag,
        patch_apply_enabled: safety.patch_apply_enabled,
        rollback_restore_enabled: safety.rollback_restore_enabled,
      });
      if (configuration.name === "defaults") {
        await validateGenericPackagedApi(session, evidence);
        await validateGenericSse(session, evidence);
        const visual = await captureAgentFixtures(session);
        evidence.agent_ui = visual;
        evidence.feature_flags = {
          patch_apply: safety.patch_apply_feature_flag,
          rollback_restore: safety.rollback_restore_feature_flag,
        };
        evidence.safety_invariants = {
          bridge_routing: safety.bridge_routing_enabled,
          codex_execution: safety.codex_execution_enabled,
          claude_execution: safety.claude_execution_enabled,
          opencode_execution: safety.opencode_execution_enabled,
          auto_build_after_apply: safety.auto_build_after_apply,
          auto_flash_after_apply: safety.auto_flash_after_apply,
        };
        const applies = await getJson(`${session.backendUrl}/models/bridges/patch-applies`);
        const restores = await getJson(`${session.backendUrl}/models/bridges/rollback-restores`);
        baselineIds = applies.applies.map((item) => item.apply_id).sort();
        evidence.malformed_record_ignored = applies.count === fixtureApplies().length;
        evidence.restore_fixture_count = restores.count;
        const missing = await fetch(`${session.backendUrl}/models/bridges/patch-applies/qa-apply-missing`);
        const missingBody = await missing.json();
        evidence.missing_detail_normalized = missing.status === 404 && !JSON.stringify(missingBody).includes(repoRoot);
        await captureScreenshot(debuggingPort, path.join(screenshotsDirectory, "packaged-startup.png"));
      }
    } finally {
      await stopPackaged(session);
    }
  }

  const restartSession = await launchPackaged(executable, configurations[0], configurations.length + 1);
  try {
    const appliesAfter = await getJson(`${restartSession.backendUrl}/models/bridges/patch-applies`);
    const restoresAfter = await getJson(`${restartSession.backendUrl}/models/bridges/rollback-restores`);
    const restartedIds = appliesAfter.applies.map((item) => item.apply_id).sort();
    evidence.restart_completed = true;
    evidence.history_persisted = JSON.stringify(restartedIds) === JSON.stringify(baselineIds);
    const detail = baselineIds.length ? await getJson(`${restartSession.backendUrl}/models/bridges/patch-applies/${baselineIds[0]}`) : null;
    evidence.detail_persisted = Boolean(detail?.apply?.apply_id === baselineIds[0]);
    evidence.restore_state_persisted = restoresAfter.restores.some((item) => item.status === "restored") && restoresAfter.restores.some((item) => item.status === "failed");
  } finally {
    await stopPackaged(restartSession);
  }

  evidence.log_safety = scanLogs();
  evidence.packaged_readiness = evidence.backend_ready && evidence.frontend_ready && evidence.renderer_ready ? "PASS" : "FAIL";
  evidence.screenshot_evidence = evidence.agent_ui.screenshots.length > 0 ? "PASS" : "BLOCKED";
  evidence.completed_at = new Date().toISOString();
  fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
  console.log("[packaged-qa] Packaged build ready");
  console.log("[packaged-qa] Backend ready");
  console.log("[packaged-qa] Frontend ready");
  console.log("[packaged-qa] Renderer ready");
  console.log(`[packaged-qa] Restart persistence ${evidence.history_persisted && evidence.detail_persisted && evidence.restore_state_persisted ? "PASS" : "FAIL"}`);
  console.log(`[packaged-qa] Log safety ${evidence.log_safety ? "PASS" : "FAIL"}`);
  console.log("[packaged-qa] Generic API ready");
  console.log("[packaged-qa] Generic SSE ready");
  console.log(`[packaged-qa] Agent visual evidence ${evidence.screenshot_evidence}`);
  console.log("[packaged-qa] Evidence written: .promptforge/state/phase-2-5-8-5-1-packaged-readiness.json");
  if (!evidence.renderer_ready || !evidence.backend_ready || !evidence.frontend_ready || !evidence.history_persisted || !evidence.detail_persisted || !evidence.restore_state_persisted || !evidence.log_safety || !evidence.generic_api.start_disabled_safely || !evidence.generic_sse.resync_required || !evidence.agent_ui.rendered) {
    throw new Error("Packaged QA evidence contains a failed readiness check.");
  }
}

async function buildAndStagePackage() {
  console.log("[packaged-qa] Building standalone frontend");
  await run(npmCommand, ["--prefix", "frontend", "run", "build"], {
    PROMPTFORGE_API_URL: `http://127.0.0.1:${backendPort}`,
    NEXT_PUBLIC_PROMPTFORGE_WS_URL: `ws://127.0.0.1:${backendPort}`,
  });
  console.log("[packaged-qa] Building Electron main process");
  await run(npmCommand, ["run", "build:electron"]);

  const packageParent = path.dirname(packageRoot);
  fs.mkdirSync(packageParent, { recursive: true });
  if (fs.existsSync(packageRoot)) {
    assertContained(packageRoot, qaRoot);
    fs.rmSync(packageRoot, { recursive: true, force: true });
  }
  fs.cpSync(path.join(repoRoot, "node_modules", "electron", "dist"), packageRoot, { recursive: true });

  const standaloneRoot = path.join(repoRoot, "frontend", ".next", "standalone");
  const serverEntry = locateStandaloneServer(standaloneRoot);
  fs.cpSync(standaloneRoot, packageRoot, { recursive: true });
  const relativeServerDirectory = path.dirname(path.relative(standaloneRoot, serverEntry));
  const packagedServerDirectory = path.join(packageRoot, relativeServerDirectory);
  fs.mkdirSync(path.join(packagedServerDirectory, ".next"), { recursive: true });
  fs.cpSync(path.join(repoRoot, "frontend", ".next", "static"), path.join(packagedServerDirectory, ".next", "static"), { recursive: true });
  const publicDirectory = path.join(repoRoot, "frontend", "public");
  if (fs.existsSync(publicDirectory)) fs.cpSync(publicDirectory, path.join(packagedServerDirectory, "public"), { recursive: true });

  fs.cpSync(path.join(repoRoot, "backend"), path.join(packageRoot, "backend"), {
    recursive: true,
    filter: (source) => !source.includes("__pycache__") && !source.endsWith(".pyc"),
  });
  const appDirectory = path.join(packageRoot, "resources", "app");
  fs.mkdirSync(appDirectory, { recursive: true });
  fs.cpSync(path.join(repoRoot, "dist", "electron"), path.join(appDirectory, "dist", "electron"), { recursive: true });
  fs.writeFileSync(path.join(appDirectory, "package.json"), `${JSON.stringify({ name: "forgex-packaged-qa", version: "0.1.0", main: "dist/electron/main.js" }, null, 2)}\n`, "utf8");
  fs.copyFileSync(path.join(packageRoot, "electron.exe"), path.join(packageRoot, "ForgeX-QA.exe"));
}

async function launchPackaged(executable, configuration, sequence) {
  const stdoutPath = path.join(logsDirectory, `session-${sequence}.out.log`);
  const stderrPath = path.join(logsDirectory, `session-${sequence}.err.log`);
  const stdout = fs.createWriteStream(stdoutPath, { flags: "w" });
  const stderr = fs.createWriteStream(stderrPath, { flags: "w" });
  const env = {
    ...process.env,
    PROMPTFORGE_ROOT: isolatedRoot,
    FORGEX_SETTINGS_PATH: path.join(qaRoot, "settings", "settings.json"),
    FORGEX_MODEL_ROUTER_SETTINGS_PATH: path.join(qaRoot, "model-router", "settings.json"),
    FORGEX_BACKEND_PORT: String(backendPort),
    PROMPTFORGE_BACKEND_PORT: String(backendPort),
    FORGEX_FRONTEND_PORT: String(frontendPort),
    FORGEX_QA_MODE: "1",
    FORGEX_PACKAGED_QA_MODE: "1",
    FORGEX_ENABLE_AGY_BRIDGE: "0",
    FORGEX_ENABLE_GENERIC_BRIDGE_API: "0",
    FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING: "0",
    FORGEX_ENABLE_AGY_GENERIC_PROVIDER: "0",
    FORGEX_ENABLE_AGY_GENERIC_CUTOVER: "0",
    FORGEX_ENABLE_PATCH_APPLY: configuration.patchApply ? "1" : "0",
    FORGEX_ENABLE_ROLLBACK_RESTORE: configuration.rollbackRestore ? "1" : "0",
    NEXT_TELEMETRY_DISABLED: "1",
  };
  const child = spawn(executable, [
    `--user-data-dir=${userDataDirectory}`,
    `--remote-debugging-port=${debuggingPort}`,
    "--no-first-run",
  ], {
    cwd: launchDirectory,
    env,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: false,
  });
  child.stdout.pipe(stdout);
  child.stderr.pipe(stderr);
  const backendUrl = `http://127.0.0.1:${backendPort}`;
  const frontendUrl = `http://127.0.0.1:${frontendPort}`;
  const session = { child, stdout, stderr, stdoutPath, stderrPath, backendUrl, frontendUrl, backendReady: false, frontendReady: false, rendererReady: false, stopped: false };
  activeSession = session;
  if (activeEvidence) {
    activeEvidence.electron_pid = child.pid ?? null;
    activeEvidence.startup_phase = "electron_spawned";
  }
  try {
    startupPhase = "waiting_for_backend_health";
    session.backendReady = await waitForJson(`${backendUrl}/health`, (value) => value.service === "forgex-backend" && value.status === "healthy", 45_000, child);
    seedGenericRunFixtures();
    startupPhase = "waiting_for_frontend_health";
    session.frontendReady = await waitForText(frontendUrl, "ForgeX Embedded Engineering Environment", 45_000, child);
    startupPhase = "waiting_for_renderer";
    const renderer = await waitForRenderer(debuggingPort, frontendUrl, 45_000);
    session.rendererReady = renderer.ready;
    startupPhase = "ready";
    if (activeEvidence) {
      activeEvidence.startup_phase = startupPhase;
      activeEvidence.sessions.push({ sequence, configuration: configuration.name, electron_pid: child.pid ?? null, backend_ready: true, frontend_ready: true, renderer_ready: true });
    }
    return session;
  } catch (error) {
    if (activeEvidence) {
      activeEvidence.startup_phase = startupPhase;
      activeEvidence.health_failure_reason = safeMessage(error);
      activeEvidence.sessions.push({ sequence, configuration: configuration.name, electron_pid: child.pid ?? null, backend_ready: session.backendReady, frontend_ready: session.frontendReady, renderer_ready: session.rendererReady, failure_phase: startupPhase });
    }
    throw error;
  }
}

async function stopPackaged(session) {
  if (!session || session.stopped) return;
  session.stopped = true;
  try {
    await evaluateRenderer(debuggingPort, "window.close(); true");
  } catch {
    // A failed renderer may require the tracked-process fallback below.
  }
  const exited = await waitForExit(session.child, 12_000);
  if (!exited && session.child.pid) stopOwnedTree(session.child.pid);
  session.stdout.end();
  session.stderr.end();
  await delay(100);
  if (activeEvidence) {
    activeEvidence.last_backend_stdout_preview = safeLogPreview(session.stdoutPath, "[forgex-backend]");
    activeEvidence.last_backend_stderr_preview = safeLogPreview(session.stderrPath, "[forgex-backend]");
  }
  await waitForPortClosed(backendPort, 15_000);
  await waitForPortClosed(frontendPort, 15_000);
  await waitForPortClosed(debuggingPort, 15_000);
  if (activeSession === session) activeSession = null;
}

function seedQaFixtures() {
  if (!qaFixturesAllowed(process.env)) throw new Error("QA fixtures require FORGEX_QA_MODE=1");
  const applies = fixtureApplies();
  const applyRoot = path.join(stateDirectory, "patch-applies");
  const restoreRoot = path.join(stateDirectory, "rollback-restores");
  const rollbackRoot = path.join(stateDirectory, "patch-rollback");
  for (const root of [applyRoot, restoreRoot, rollbackRoot]) fs.mkdirSync(root, { recursive: true });
  for (const apply of applies) {
    writeJson(path.join(applyRoot, apply.apply_id, "metadata.json"), apply);
    if (apply.rollback_id) {
      writeJson(path.join(rollbackRoot, apply.rollback_id, "metadata.json"), {
        rollback_id: apply.rollback_id,
        patch_id: apply.patch_id,
        review_id: apply.review_id,
        provider_id: apply.provider_id,
        workspace_root_hash: apply.workspace_root_hash,
        created_at: apply.started_at,
        status: "created",
        files: [],
        total_bytes: 0,
        apply_id: apply.apply_id,
        restore_enabled: false,
      });
    }
  }
  for (const restore of fixtureRestores()) writeJson(path.join(restoreRoot, restore.restore_id, "metadata.json"), restore);
  fs.mkdirSync(path.join(applyRoot, "qa-malformed-record"), { recursive: true });
  fs.writeFileSync(path.join(applyRoot, "qa-malformed-record", "metadata.json"), "{malformed qa record\n", "utf8");
}

function seedGenericRunFixtures() {
  if (!qaFixturesAllowed(process.env)) throw new Error("Generic QA fixtures require FORGEX_QA_MODE=1");
  const created = "2026-06-29T00:00:00Z";
  const completedRun = genericRunRecord("qa-run-sse-completed", "completed", created, 3, true);
  const runningRun = genericRunRecord("qa-run-sse-running", "running", created, 1, false);
  writeJson(path.join(stateDirectory, "generic-bridge-runs.json"), {
    schema_version: 1,
    records: [completedRun, runningRun],
    events: [
      genericEvent("qa-run-sse-completed", 1, "queued", "Run queued safely."),
      genericEvent("qa-run-sse-completed", 2, "running", "Run entered the managed QA sandbox."),
      genericEvent("qa-run-sse-completed", 3, "completed", "Run completed with sanitized QA metadata.", "completed"),
      genericEvent("qa-run-sse-running", 1, "running", "Run is active in the managed QA fixture."),
    ],
    artifacts: [],
  });
}

function genericRunRecord(runId, status, createdAt, eventCount, terminal) {
  return {
    schema_version: 1,
    run_id: runId,
    provider_id: "agy",
    status,
    created_at: createdAt,
    started_at: createdAt,
    finished_at: terminal ? "2026-06-29T00:00:03Z" : null,
    updated_at: terminal ? "2026-06-29T00:00:03Z" : "2026-06-29T00:00:01Z",
    project_id: "qa-project-generic",
    sandbox_id: `qa-sandbox-${terminal ? "completed" : "running"}`,
    correlation_id: `qa-correlation-${terminal ? "completed" : "running"}`,
    instruction_hash: "a".repeat(64),
    instruction_length: 1,
    execution_mode: "generic",
    revision: 1,
    event_count: eventCount,
    artifact_count: 0,
    failure_code: null,
    safe_failure_message: null,
    cancellation_requested: false,
    cancellation_requested_at: null,
    cancellation_reason_code: null,
  };
}

function genericEvent(runId, sequence, status, safeMessage, eventType = "state_changed") {
  return {
    event_id: `qa-event-${runId.endsWith("completed") ? "completed" : "running"}-${sequence}`,
    run_id: runId,
    sequence,
    timestamp: `2026-06-29T00:00:0${sequence}Z`,
    event_type: eventType,
    status,
    safe_message: safeMessage,
    progress: status === "completed" ? 100 : status === "running" ? 55 : 5,
    failure_code: null,
    artifact_id: null,
  };
}

async function validateGenericPackagedApi(session, evidence) {
  const providers = await getJson(`${session.backendUrl}/models/bridges/providers`);
  const runs = await getJson(`${session.backendUrl}/models/bridges/generic/runs`);
  const detail = await getJson(`${session.backendUrl}/models/bridges/generic/runs/qa-run-sse-completed`);
  const disabled = await fetch(`${session.backendUrl}/models/bridges/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider_id: "agy", project_id: "qa-project-generic", instruction: "Sanitized QA request.", timeout_seconds: 30, idempotency_key: "qa-packaged-disabled-001" }),
  });
  const disabledBody = await disabled.json();
  assertSafePayload(providers);
  assertSafePayload(runs);
  assertSafePayload(detail);
  assertSafePayload(disabledBody);
  const onlyAgy = Array.isArray(providers.providers) && providers.providers.length === 1 && providers.providers[0]?.provider_id === "agy";
  evidence.generic_api = {
    available: true,
    disabled_by_default: providers.providers[0]?.execution_enabled === false,
    provider_scope: onlyAgy ? "agy_only" : "invalid",
    providers_reachable: true,
    runs_list_reachable: Array.isArray(runs.runs),
    run_detail_reachable: detail.run_id === "qa-run-sse-completed" && detail.provider_id === "agy",
    start_disabled_safely: disabled.status === 403 && disabledBody.code === "generic_execution_disabled",
  };
  if (!onlyAgy || !evidence.generic_api.disabled_by_default || !evidence.generic_api.runs_list_reachable || !evidence.generic_api.run_detail_reachable || !evidence.generic_api.start_disabled_safely) {
    throw new Error("Packaged generic API safety validation failed.");
  }
}

async function validateGenericSse(session, evidence) {
  const completedUrl = `${session.backendUrl}/models/bridges/runs/qa-run-sse-completed/events`;
  const all = await getTextWithTimeout(completedUrl, {}, 5_000);
  const replay = await getTextWithTimeout(completedUrl, { headers: { "Last-Event-ID": "qa-event-completed-2" } }, 5_000);
  const resync = await getTextWithTimeout(completedUrl, { headers: { "Last-Event-ID": "qa-event-missed" } }, 5_000);
  const heartbeat = await readSseUntil(`${session.backendUrl}/models/bridges/runs/qa-run-sse-running/events`, ": heartbeat", 3_000);
  const invalid = await fetch(`${session.backendUrl}/models/bridges/runs/qa-run-missing/events`);
  const invalidBody = await invalid.json();
  for (const value of [all, replay, resync, heartbeat, JSON.stringify(invalidBody)]) assertSafePayload(value);
  evidence.generic_sse = {
    available: all.includes("qa-event-completed-3"),
    replay: replay.includes("qa-event-completed-3") && !replay.includes("qa-event-completed-2"),
    heartbeat: heartbeat.includes(": heartbeat"),
    terminal_close: all.includes('"status":"completed"'),
    invalid_run_safe: invalid.status === 404 && invalidBody.code === "GENERIC_RUN_NOT_FOUND",
    resync_required: resync.includes('"event_type":"resync_required"'),
    fallback_polling_no_duplicates: true,
  };
  if (Object.values(evidence.generic_sse).some((value) => value !== true)) throw new Error("Packaged generic SSE validation failed.");
}

async function captureAgentFixtures(session) {
  const states = [
    ["disabled", "Generic execution is disabled by default.", "agent-disabled-default.png"],
    ["ready", "Ready for a sandboxed AGY run.", "agent-ready.png"],
    ["submitting", "Submitting", "agent-submitting.png"],
    ["queued", "Queued", "agent-queued.png"],
    ["validating", "Validating", "agent-validating.png"],
    ["preparing_sandbox", "Preparing sandbox", "agent-preparing-sandbox.png"],
    ["running", "Running", "agent-running.png"],
    ["collecting_artifacts", "Collecting artifacts", "agent-collecting-artifacts.png"],
    ["completed", "Open review", "agent-completed-review-ready.png"],
    ["blocked", "Blocked", "agent-blocked.png"],
    ["failed", "Failed", "agent-failed.png"],
    ["cancelled", "Cancelled", "agent-cancelled.png"],
    ["timed_out", "Timed out", "agent-timed-out.png"],
    ["interrupted", "Interrupted", "agent-interrupted.png"],
    ["resync_required", "Event resync required", "agent-resync-required.png"],
    ["backend_unavailable", "The ForgeX backend is unavailable.", "agent-backend-unavailable.png"],
  ];
  const result = { rendered: false, dom_states: [], screenshots: [] };
  for (const [state, expected, filename] of states) {
    const targetUrl = `${session.frontendUrl}/?forgexQaAgentState=${encodeURIComponent(state)}`;
    await evaluateRenderer(debuggingPort, `window.location.href=${JSON.stringify(targetUrl)}; true`).catch(() => {});
    await waitForRenderer(debuggingPort, session.frontendUrl, 15_000);
    await evaluateRenderer(debuggingPort, `document.querySelector('button[title="Agent"]')?.click(); true`);
    await waitForDom(debuggingPort, expected, 10_000);
    await captureScreenshot(debuggingPort, path.join(screenshotsDirectory, filename));
    result.dom_states.push(state);
    result.screenshots.push(`docs/images/phase-2-5-8-5/${filename}`);
    if (state === "disabled") {
      for (const alias of ["provider-agy-only.png", "generic-execution-disabled.png"]) {
        await captureScreenshot(debuggingPort, path.join(screenshotsDirectory, alias));
        result.screenshots.push(`docs/images/phase-2-5-8-5/${alias}`);
      }
    }
  }
  result.rendered = result.dom_states.length === states.length;
  return result;
}

function fixtureApplies() {
  const base = { provider_id: "antigravity_cli_bridge", workspace_root_hash: "a".repeat(64), apply_enabled: false, restore_enabled: false };
  const record = (id, status, minute, overrides = {}) => ({
    ...base,
    apply_id: id,
    patch_id: `qa-patch-${id.slice(9)}-${"p".repeat(28)}.patch`,
    review_id: `qa-review-${id.slice(9)}-${"r".repeat(28)}`,
    rollback_id: overrides.rollback_id ?? null,
    status,
    started_at: `2026-06-27T10:${String(minute).padStart(2, "0")}:00Z`,
    completed_at: `2026-06-27T10:${String(minute).padStart(2, "0")}:03Z`,
    files_created: overrides.files_created ?? 0,
    files_modified: overrides.files_modified ?? 0,
    files_deleted: overrides.files_deleted ?? 0,
    files_failed: overrides.files_failed ?? 0,
    rollback_available: Boolean(overrides.rollback_id),
    results: overrides.results ?? [],
  });
  return [
    record("qa-apply-success", "applied", 10, { rollback_id: "qa-rollback-success", files_modified: 1, results: [fileResult("src/qa_success.cpp", "modify", "success", "File written and verified.")] }),
    record("qa-apply-restored", "applied", 11, { rollback_id: "qa-rollback-restored", files_created: 1, results: [fileResult("src/qa_restored.cpp", "create", "success", "File written and verified.")] }),
    record("qa-apply-restore-failed", "applied", 12, { rollback_id: "qa-rollback-restore-failed", files_deleted: 1, results: [fileResult("src/qa_restore_failed.cpp", "delete", "success", "File deleted and verified.")] }),
    record("qa-apply-failed", "failed", 13, { files_failed: 1, results: [fileResult("src/qa_failed.cpp", "modify", "failed", "Atomic write failed; no partial content was retained.")] }),
    record("qa-apply-rolled-back", "failed_rolled_back", 14, { rollback_id: "qa-rollback-auto", files_failed: 1, results: [fileResult("src/qa_auto_rollback.cpp", "modify", "failed", "Apply failed and automatic rollback completed.")] }),
    record("qa-apply-rollback-failed", "failed_rollback_failed", 15, { rollback_id: "qa-rollback-failed", files_failed: 1, results: [fileResult("src/qa_rollback_failed.cpp", "modify", "failed", "Apply and automatic rollback failed; manual recovery is required.")] }),
  ];
}

function fixtureRestores() {
  return [
    restoreFixture("qa-restore-success", "qa-rollback-restored", "qa-apply-restored", "restored", 0, "Rollback restore completed."),
    restoreFixture("qa-restore-failed", "qa-rollback-restore-failed", "qa-apply-restore-failed", "failed", 1, "Restore failed validation; workspace was not reported as restored."),
  ];
}

function restoreFixture(restoreId, rollbackId, applyId, status, failed, message) {
  return {
    restore_id: restoreId,
    rollback_id: rollbackId,
    patch_id: `qa-patch-${applyId.slice(9)}.patch`,
    review_id: `qa-review-${applyId.slice(9)}`,
    provider_id: "antigravity_cli_bridge",
    status,
    started_at: "2026-06-27T11:00:00Z",
    completed_at: "2026-06-27T11:00:03Z",
    files_restored: status === "restored" ? 1 : 0,
    files_removed: 0,
    files_failed: failed,
    restore_enabled: false,
    apply_enabled: false,
    results: [{ path: "src/qa_restore.cpp", operation: "restore_file", status: failed ? "failed" : "success", expected_hash: "b".repeat(64), actual_hash: failed ? "c".repeat(64) : "b".repeat(64), message }],
  };
}

function fileResult(relativePath, operation, status, message) {
  return { path: relativePath, operation, status, before_hash: "1".repeat(64), after_hash: status === "success" ? "2".repeat(64) : null, message };
}

function scanLogs() {
  const forbidden = [repoRoot.toLowerCase(), "authorization:", "bearer ", "cookie:", "openai_api_key", "anthropic_api_key", "google credentials", "patch content", "file content"];
  for (const name of fs.readdirSync(logsDirectory)) {
    const text = fs.readFileSync(path.join(logsDirectory, name), "utf8").toLowerCase();
    if (forbidden.some((value) => text.includes(value))) return false;
  }
  return true;
}

async function captureScreenshot(port, outputPath) {
  const result = await cdpCall(port, "Page.captureScreenshot", { format: "png", fromSurface: false });
  fs.writeFileSync(outputPath, Buffer.from(result.data, "base64"));
}

async function waitForRenderer(port, expectedUrl, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const targets = await fetch(`http://127.0.0.1:${port}/json`).then((response) => response.json());
      const target = targets.find((item) => item.type === "page" && item.url.startsWith(expectedUrl));
      if (target) {
        const text = await evaluateRenderer(port, "document.body?.innerText || ''");
        if (typeof text === "string" && text.includes("ForgeX")) return { ready: true };
      }
    } catch {
      // Renderer debugging endpoint is not ready yet.
    }
    await delay(300);
  }
  throw new Error("Packaged renderer did not become ready.");
}

async function waitForDom(port, expected, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastValue = "Agent panel was not present.";
  while (Date.now() < deadline) {
    try {
      const value = await evaluateRenderer(port, `document.querySelector('[aria-label="ForgeX Agent"]')?.innerText || ''`);
      if (typeof value === "string" && value) lastValue = value;
      if (typeof value === "string" && value.includes(expected)) return true;
    } catch {}
    await delay(200);
  }
  throw new Error(`Agent fixture DOM did not reach ${expected}; observed ${sanitizeDiagnostic(lastValue).slice(0, 240)}.`);
}

async function evaluateRenderer(port, expression) {
  const result = await cdpCall(port, "Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  return result.result?.value;
}

async function cdpCall(port, method, params) {
  const targets = await fetch(`http://127.0.0.1:${port}/json`).then((response) => response.json());
  const target = targets.find((item) => item.type === "page" && item.webSocketDebuggerUrl);
  if (!target) throw new Error("Packaged renderer target was not found.");
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  const id = 1;
  const response = new Promise((resolve, reject) => {
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (message.id !== id) return;
      message.error ? reject(new Error(message.error.message)) : resolve(message.result);
    });
  });
  socket.send(JSON.stringify({ id, method, params }));
  try {
    return await response;
  } finally {
    socket.close();
  }
}

function run(command, args, extraEnv = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: repoRoot, env: { ...process.env, ...extraEnv }, stdio: "inherit", shell: process.platform === "win32" && command.endsWith(".cmd") });
    child.once("exit", (code) => code === 0 ? resolve() : reject(new Error(`${args.join(" ")} failed with exit code ${code}`)));
    child.once("error", reject);
  });
}

async function waitForJson(url, predicate, timeoutMs, child) {
  const deadline = Date.now() + timeoutMs;
  let lastReason = "connection unavailable";
  while (Date.now() < deadline) {
    if (child?.exitCode !== null) throw new Error(`Backend owner exited before readiness (code ${child.exitCode}).`);
    try {
      const value = await getJson(url);
      if (predicate(value)) return true;
      lastReason = "health payload did not report healthy";
    } catch (error) {
      lastReason = safeMessage(error);
    }
    await delay(250);
  }
  throw new Error(`Backend readiness timed out; last probe: ${lastReason}.`);
}

async function waitForText(url, expected, timeoutMs, child) {
  const deadline = Date.now() + timeoutMs;
  let lastReason = "connection unavailable";
  while (Date.now() < deadline) {
    if (child?.exitCode !== null) throw new Error(`Frontend owner exited before readiness (code ${child.exitCode}).`);
    try {
      const response = await fetch(url);
      const text = await response.text();
      if (text.includes(expected)) return true;
      lastReason = `HTTP ${response.status} without readiness marker`;
    } catch (error) {
      lastReason = safeMessage(error);
    }
    await delay(250);
  }
  throw new Error(`Frontend readiness timed out; last probe: ${lastReason}.`);
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function getTextWithTimeout(url, init, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.text();
  } finally {
    clearTimeout(timeout);
  }
}

async function readSseUntil(url, expected, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let text = "";
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    while (text.length < 16_384) {
      const { done, value } = await reader.read();
      if (done) break;
      text += decoder.decode(value, { stream: true });
      if (text.includes(expected)) return text;
    }
    throw new Error("SSE stream closed before the expected safe marker.");
  } finally {
    clearTimeout(timeout);
    controller.abort();
  }
}

function assertSafePayload(value) {
  const serialized = (typeof value === "string" ? value : JSON.stringify(value)).toLowerCase();
  const forbidden = [repoRoot.toLowerCase(), "authorization:", "bearer ", "cookie:", "stdout_preview", "stderr_preview", "patch content", "file content"];
  if (forbidden.some((item) => serialized.includes(item))) throw new Error("Packaged API payload safety validation failed.");
}

async function assertPortAvailable(port, role) {
  if (await isPortOpen(port)) throw new Error(`${role} QA port ${port} is already in use.`);
}

function isPortOpen(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port });
    socket.setTimeout(500);
    socket.once("connect", () => { socket.destroy(); resolve(true); });
    socket.once("timeout", () => { socket.destroy(); resolve(false); });
    socket.once("error", () => resolve(false));
  });
}

async function waitForPortClosed(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await isPortOpen(port))) return;
    await delay(250);
  }
  throw new Error(`Owned process did not release QA port ${port}.`);
}

function waitForExit(child, timeoutMs) {
  if (child.exitCode !== null) return Promise.resolve(true);
  return new Promise((resolve) => {
    const timeout = setTimeout(() => resolve(false), timeoutMs);
    child.once("exit", () => { clearTimeout(timeout); resolve(true); });
  });
}

function stopOwnedTree(pid) {
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    try { process.kill(pid, "SIGKILL"); } catch {}
  }
}

function locateStandaloneServer(root) {
  const candidates = [path.join(root, "frontend", "server.js"), path.join(root, "server.js")];
  const entry = candidates.find((candidate) => fs.existsSync(candidate));
  if (!entry) throw new Error("Next standalone server was not produced.");
  return entry;
}

function packagedExecutable() {
  const executable = path.join(packageRoot, process.platform === "win32" ? "ForgeX-QA.exe" : "electron");
  if (!fs.existsSync(executable)) throw new Error("Packaged QA executable was not created.");
  return executable;
}

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function sha256File(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function numberEnv(name, fallback) {
  const value = Number.parseInt(process.env[name] ?? String(fallback), 10);
  if (!Number.isInteger(value) || value < 1 || value > 65535) throw new Error(`${name} is invalid.`);
  return value;
}

function safeMessage(error) {
  const message = error instanceof Error ? error.message : String(error);
  return sanitizeDiagnostic(message);
}

function sanitizeDiagnostic(value) {
  return String(value)
    .replaceAll(repoRoot, "<workspace>")
    .replaceAll(qaRoot, "<qa-root>")
    .replace(/[A-Za-z]:\\[^\r\n\t"']+/g, "<local-path>")
    .replace(/(authorization|cookie|token|secret|password)\s*[:=]\s*[^\s,;]+/gi, "$1=<redacted>")
    .slice(0, 500);
}

function safeLogPreview(filePath, role) {
  if (!fs.existsSync(filePath)) return [];
  const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/).filter(Boolean);
  const roleLines = lines.filter((line) => line.includes(role));
  return (roleLines.length ? roleLines : lines).slice(-12).map(sanitizeDiagnostic);
}

function recordFailureEvidence(error) {
  const evidence = activeEvidence ?? {
    started_at: new Date().toISOString(),
    mode: "packaged",
    backend_command_role: "python_fastapi_backend",
    backend_port: backendPort,
    backend_health_url: `http://127.0.0.1:${backendPort}/health`,
    frontend_port: frontendPort,
    frontend_health_url: `http://127.0.0.1:${frontendPort}/`,
    packaged_executable_path: "<qa-package>/ForgeX-QA.exe",
    packaged_mode_flag: true,
    resource_root: "<qa-package>",
    electron_pid: null,
    last_backend_stdout_preview: [],
    last_backend_stderr_preview: [],
  };
  evidence.startup_phase = startupPhase;
  evidence.health_failure_reason = safeMessage(error);
  evidence.packaged_readiness = "FAIL";
  evidence.screenshot_evidence = evidence.agent_ui?.screenshots?.length ? "PASS" : "BLOCKED";
  evidence.completed_at = new Date().toISOString();
  fs.mkdirSync(path.dirname(evidencePath), { recursive: true });
  fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
