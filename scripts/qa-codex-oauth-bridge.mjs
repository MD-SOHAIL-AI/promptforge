import crypto from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";

import {
  OAUTH_MARKER, OAUTH_MARKER_CONTENT, OAUTH_SMOKE_CONTENT, OAUTH_SMOKE_FILE,
  OAUTH_SMOKE_ROOT, OAUTH_SMOKE_TIMEOUT_MS, buildOAuthSmokeArgs,
  classifyOAuthSmoke, compareSnapshots, createOAuthSandbox, guardOAuthSandbox,
  inspectKnownOAuthSmokeContent, isSafeOAuthSmokePass, sanitizedSmokeResult, snapshotTree, validateOAuthSmokeContent,
} from "./qa-codex-oauth-smoke-core.mjs";
import { buildCodexSafeUserEnv } from "./codex-safe-user-env.mjs";
import { runSessionParity } from "./codex-session-parity-core.mjs";

const repositoryRoot = path.resolve(import.meta.dirname, "..");
const activeWorkspace = path.join(repositoryRoot, "workspace", "forgex-apply-test");
const stateDirectory = path.join(repositoryRoot, ".promptforge", "state");

export function resolveCodexLauncher(env = process.env, platform = process.platform) {
  if (platform !== "win32") return { command: "codex", prefixArgs: [] };
  const pathValue = env.PATH || env.Path || "";
  for (const entry of pathValue.split(path.delimiter).filter(Boolean)) {
    const native = path.join(entry, "codex.exe");
    if (fs.existsSync(native) && fs.statSync(native).isFile()) return { command: native, prefixArgs: [] };
    const npmEntry = path.join(entry, "node_modules", "@openai", "codex", "bin", "codex.js");
    if (fs.existsSync(npmEntry) && fs.statSync(npmEntry).isFile()) return { command: process.execPath, prefixArgs: [npmEntry] };
  }
  return null;
}

function getSharedAlignedStatus() {
  const result = spawnSync("python", ["-m", "scripts.qa_codex_status"], {
    cwd: repositoryRoot, encoding: "utf8", input: "", shell: false, windowsHide: true,
    timeout: 20_000, maxBuffer: 64 * 1024, env: buildCodexSafeUserEnv(process.env),
  });
  try {
    const value = JSON.parse(String(result.stdout || "{}"));
    if (result.status !== 0 || value.status_runner !== "resolved_executable_runner") throw new Error("unaligned status result");
    return value;
  } catch {
    return { codex_installed: false, codex_version: null, auth_status: "unknown", auth_classification: "CODEX_AUTH_STATUS_UNKNOWN", bridge_classification: "CODEX_OAUTH_BRIDGE_STATUS_UNKNOWN", oauth_bridge_ready: false, exit_code_category: result.error?.code === "ETIMEDOUT" ? "timeout" : "failed", status_runner: "resolved_executable_runner", env_profile: "codex_safe_user_env", safe_env_profile: "codex_safe_user_env", cwd_kind: "neutral_temp", raw_output_persisted: false, auth_files_read: false, tokens_read: false };
  }
}

export function getStatus() {
  const aligned = getSharedAlignedStatus();
  const launcher = aligned.codex_installed ? resolveCodexLauncher(buildCodexSafeUserEnv(process.env)) : null;
  return {
    launcher, installed: aligned.codex_installed === true, version: aligned.codex_version ?? null,
    authStatus: aligned.auth_status, authClassification: aligned.auth_classification,
    bridgeClassification: aligned.bridge_classification, ready: aligned.oauth_bridge_ready === true,
    statusRunner: aligned.status_runner, safeEnvProfile: aligned.env_profile ?? aligned.safe_env_profile,
    cwdKind: aligned.cwd_kind, exitCodeCategory: aligned.exit_code_category,
  };
}

function printStatus(status = getStatus()) {
  console.log(`codex_installed = ${Boolean(status.launcher)}`);
  console.log(`codex_version = ${status.version ?? "unknown"}`);
  console.log(`auth_status = ${status.authStatus}`);
  console.log(`auth_classification = ${status.authClassification}`);
  console.log(`classification = ${status.bridgeClassification}`);
  console.log(`oauth_bridge_ready = ${status.ready}`);
  console.log(`status_runner = ${status.statusRunner}`);
  console.log(`env_profile = ${status.safeEnvProfile}`);
  console.log(`cwd_kind = ${status.cwdKind}`);
  console.log("tokens_read = false\nauth_files_read = false\nraw_output_persisted = false\nproduction_routing_enabled = false");
}

function printDiagnostics() {
  const runners = ["qa_status", "backend_status", "aligned_status"].map((runnerName) => {
    const status = getStatus();
    return { runnerName, status };
  });
  for (const { runnerName, status } of runners) {
    console.log(`runner_name = ${runnerName}`);
    console.log(`codex_found = ${status.installed ? "yes" : "no"}`);
    console.log(`codex_version_detected = ${status.version ? "yes" : "no"}`);
    console.log(`auth_status = ${status.authStatus}`);
    console.log(`exit_code_category = ${status.exitCodeCategory}`);
    console.log("env_profile = aligned");
    console.log("cwd_kind = neutral_temp");
    console.log("raw_output_persisted = false");
    console.log("auth_files_read = false");
    console.log("tokens_read = false");
  }
  const auth = runners.map(({ status }) => status.authStatus);
  const classification = auth.every((value) => value === "signed_out") ? "CODEX_STATUS_DIAGNOSTICS_ALL_SIGNED_OUT"
    : new Set(auth).size > 1 ? "CODEX_STATUS_DIAGNOSTICS_MISMATCH"
    : auth.every((value) => value === "signed_in") ? "CODEX_STATUS_DIAGNOSTICS_PASS"
    : "CODEX_STATUS_DIAGNOSTICS_UNKNOWN";
  console.log(`classification = ${classification}`);
  if (classification === "CODEX_STATUS_DIAGNOSTICS_MISMATCH") process.exitCode = 1;
}

function printStatusParity() {
  let report;
  try { report = runSessionParity({ sharedStatus: getSharedAlignedStatus }); }
  catch { report = { classification: "CODEX_SESSION_PARITY_UNSAFE_ABORTED", runners: [], resolution: { codex_resolution_count: 0, primary_launcher_kind: "unknown", primary_launcher_path_hash: null, multiple_launchers_detected: false }, env_profiles: [] }; }
  for (const runner of report.runners) {
    console.log(`runner_name = ${runner.runner_name}`);
    console.log(`runner_kind = ${runner.runner_kind}`);
    console.log(`codex_found = ${runner.codex_found ? "yes" : "no"}`);
    console.log(`codex_version_detected = ${runner.codex_version_detected ? "yes" : "no"}`);
    console.log(`auth_status = ${runner.auth_status}`);
    console.log(`exit_code_category = ${runner.exit_code_category}`);
    for (const [name, value] of Object.entries(runner.parser_flags)) console.log(`parser_flags.${name} = ${value ? "yes" : "no"}`);
    console.log(`env_profile = ${runner.env_profile}`);
    console.log(`cwd_kind = ${runner.cwd_kind}`);
    console.log("raw_output_persisted = false\nauth_files_read = false\ntokens_read = false");
  }
  console.log(`codex_resolution_count = ${report.resolution.codex_resolution_count}`);
  console.log(`primary_launcher_kind = ${report.resolution.primary_launcher_kind}`);
  console.log(`primary_launcher_path_hash = ${report.resolution.primary_launcher_path_hash ?? "none"}`);
  console.log(`multiple_launchers_detected = ${report.resolution.multiple_launchers_detected ? "yes" : "no"}`);
  if (report.resolution.multiple_launchers_detected) console.log("launcher_classification = CODEX_CLI_MULTIPLE_LAUNCHERS_DETECTED");
  for (const profile of report.env_profiles) {
    console.log(`env_profile = ${profile.env_profile}`);
    console.log(`env_key_count = ${profile.env_key_count}`);
    console.log(`status_result = ${profile.status_result}`);
    console.log(`secret_like_keys_removed_count = ${profile.secret_like_keys_removed_count}`);
    console.log("raw_output_persisted = false");
  }
  console.log(`classification = ${report.classification}`);
  console.log("smoke_executed = false\nlogin_launched = false\ncodex_exec_executed = false\nproduction_routing_enabled = false");
  if (["CODEX_SESSION_PARITY_MISMATCH", "CODEX_SESSION_PARITY_UNSAFE_ABORTED"].includes(report.classification)) process.exitCode = 1;
}

async function launchLogin() {
  const status = getStatus();
  if (!status.launcher) return "CODEX_CLI_NOT_FOUND";
  if (!status.version) return "CODEX_LOGIN_UNKNOWN_SAFE_FAILURE";
  let cwd;
  try { cwd = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-codex-login-")); } catch { return "CODEX_LOGIN_UNSAFE_ABORTED"; }
  return await new Promise((resolve) => {
    const child = spawn(status.launcher.command, [...status.launcher.prefixArgs, "login"], { cwd, detached: true, shell: false, stdio: "ignore", windowsHide: true, env: buildCodexSafeUserEnv(process.env) });
    child.once("error", () => resolve("CODEX_LOGIN_COMMAND_FAILED"));
    child.once("spawn", () => { child.unref(); resolve("CODEX_LOGIN_LAUNCHED"); });
  });
}

function runSmoke(args) {
  if (!args.has("--confirm-real-codex")) return finishSmoke(sanitizedSmokeResult("CODEX_OAUTH_SMOKE_CONFIRMATION_REQUIRED"), 2);
  const status = getStatus();
  if (!status.ready) {
    const classification = status.authStatus === "signed_out" ? "CODEX_OAUTH_SMOKE_LOGIN_REQUIRED" : "CODEX_OAUTH_SMOKE_STATUS_UNKNOWN";
    return finishSmoke(sanitizedSmokeResult(classification, undefined, { authStatus: status.authStatus, ready: false }), 2);
  }
  if (!status.launcher) return finishSmoke(sanitizedSmokeResult("CODEX_OAUTH_SMOKE_UNSAFE_ABORTED"), 2);
  const runId = `oauth-${crypto.randomBytes(12).toString("hex")}`;
  let guarded;
  try { guarded = createOAuthSandbox(repositoryRoot, activeWorkspace, runId, process.env, OAUTH_SMOKE_ROOT); }
  catch { return finishSmoke(sanitizedSmokeResult("CODEX_OAUTH_SMOKE_UNSAFE_ABORTED", undefined, { authStatus: status.authStatus, ready: true, runId }), 2); }

  const ignored = [".git", ".pio", ".promptforge", "node_modules", "dist", "build", ".next"];
  const activeBefore = snapshotTree(activeWorkspace, { ignore: ignored });
  const sandboxBefore = snapshotTree(guarded.sandboxRoot, { ignore: [".git"] });
  if (sandboxBefore.length !== 1 || sandboxBefore[0].relative_path !== OAUTH_MARKER) return finishSmoke(sanitizedSmokeResult("CODEX_OAUTH_SMOKE_UNSAFE_ABORTED"), 2);
  const codexArgs = buildOAuthSmokeArgs(guarded.sandboxRoot);
  const result = spawnSync(status.launcher.command, [...status.launcher.prefixArgs, ...codexArgs], {
    cwd: guarded.sandboxRoot, encoding: "utf8", input: "", shell: false, windowsHide: true,
    timeout: OAUTH_SMOKE_TIMEOUT_MS, maxBuffer: 1024 * 1024, env: buildCodexSafeUserEnv(process.env),
  });
  let payload;
  try {
    guardOAuthSandbox(repositoryRoot, activeWorkspace, guarded.sandboxRoot, process.env, OAUTH_SMOKE_ROOT);
    const diff = compareSnapshots(sandboxBefore, snapshotTree(guarded.sandboxRoot, { ignore: [".git"] }));
    const activeUnchanged = compareSnapshots(activeBefore, snapshotTree(activeWorkspace, { ignore: ignored })).changedCount === 0;
    const markerUnchanged = fs.readFileSync(path.join(guarded.sandboxRoot, OAUTH_MARKER), "utf8") === OAUTH_MARKER_CONTENT;
    const expected = path.join(guarded.sandboxRoot, OAUTH_SMOKE_FILE);
    const expectedExists = fs.existsSync(expected) && fs.statSync(expected).isFile() && !fs.lstatSync(expected).isSymbolicLink();
    const contentDiagnostics = expectedExists ? validateOAuthSmokeContent(fs.readFileSync(expected)) : null;
    const expectedValid = contentDiagnostics?.valid === true;
    let classification = classifyOAuthSmoke({ status: result.status, signal: result.signal, errorCode: result.error?.code, stdout: result.stdout, stderr: result.stderr, diff, expectedExists, expectedValid, contentDiagnostics, markerUnchanged, activeUnchanged });
    const passCandidate = sanitizedSmokeResult(classification, diff, { authStatus: status.authStatus, ready: true, runId, executionCount: 1, expectedExists, expectedValid, contentDiagnostics, markerUnchanged, activeUnchanged });
    if (classification === "CODEX_OAUTH_SMOKE_PASS" && !isSafeOAuthSmokePass(passCandidate)) classification = "CODEX_OAUTH_SMOKE_UNKNOWN_SAFE_FAILURE";
    let reviewCreated = false, reviewId = null;
    if (classification === "CODEX_OAUTH_SMOKE_PASS") {
      const review = spawnSync("python", ["-m", "backend.bridges.codex_oauth_smoke_review", "--sandbox", guarded.sandboxRoot, "--state-dir", stateDirectory], { cwd: repositoryRoot, encoding: "utf8", input: "", shell: false, windowsHide: true, timeout: 30_000, maxBuffer: 64 * 1024, env: buildCodexSafeUserEnv(process.env) });
      try { const value = JSON.parse(String(review.stdout || "{}")); reviewCreated = review.status === 0 && value.review_created === true && typeof value.review_id === "string"; reviewId = reviewCreated ? value.review_id : null; } catch { reviewCreated = false; }
      if (!reviewCreated) classification = "CODEX_OAUTH_SMOKE_UNKNOWN_SAFE_FAILURE";
    }
    payload = sanitizedSmokeResult(classification, diff, { authStatus: status.authStatus, ready: true, runId, executionCount: 1, expectedExists, expectedValid, contentDiagnostics, markerUnchanged, activeUnchanged, reviewCreated, reviewId });
  } catch { payload = sanitizedSmokeResult("CODEX_OAUTH_SMOKE_UNSAFE_ABORTED", undefined, { authStatus: status.authStatus, ready: true, runId, executionCount: 1, markerUnchanged: false }); }
  persistSanitizedStatus(payload);
  return finishSmoke(payload, payload.classification === "CODEX_OAUTH_SMOKE_PASS" ? 0 : 1);
}

function inspectLastSmokeContent() {
  const statusFile = path.join(stateDirectory, "codex-oauth-smoke-status.json");
  let metadata;
  try { metadata = JSON.parse(fs.readFileSync(statusFile, "utf8")); }
  catch { return printInspect({ classification: "CODEX_OAUTH_SMOKE_INSPECT_NOT_AVAILABLE" }, 0); }
  if (!metadata || !/^oauth-[a-f0-9]{24}$/.test(String(metadata.run_id || "")) || metadata.provider_id !== "codex_cli_oauth_bridge") return printInspect({ classification: "CODEX_OAUTH_SMOKE_INSPECT_NOT_AVAILABLE" }, 0);
  const payload = inspectKnownOAuthSmokeContent({ repositoryRoot, activeWorkspace, managedRoot: OAUTH_SMOKE_ROOT, metadata, env: process.env });
  return printInspect(payload, payload.classification === "CODEX_OAUTH_SMOKE_INSPECT_PASS" ? 0 : 1);
}

function printInspect(payload, code) {
  for (const [key, value] of Object.entries(payload)) if (key !== "valid") console.log(`${key} = ${typeof value === "boolean" ? (value ? "yes" : "no") : value}`);
  process.exitCode = code;
  return payload;
}

function persistSanitizedStatus(payload) { fs.mkdirSync(stateDirectory, { recursive: true }); fs.writeFileSync(path.join(stateDirectory, "codex-oauth-smoke-status.json"), `${JSON.stringify(payload, null, 2)}\n`, "utf8"); }
function finishSmoke(payload, code) { for (const [key, value] of Object.entries(payload)) if (!["expected_content_hash", "instruction_type", "sandbox_kind_metadata", "browser_url_read"].includes(key)) console.log(`${key} = ${value}`); process.exitCode = code; return payload; }
const args = new Set(process.argv.slice(2));
if (args.has("--login-help")) console.log("codex login");
else if (args.has("--status-diagnostics")) printDiagnostics();
else if (args.has("--session-parity") || args.has("--status-parity")) printStatusParity();
else if (args.has("--status")) printStatus();
else if (args.has("--inspect-last-smoke-content")) inspectLastSmokeContent();
else if (args.has("--launch-login")) {
  if (!args.has("--confirm-launch-codex-login")) { console.log("classification = CODEX_LOGIN_CONFIRMATION_REQUIRED"); process.exitCode = 2; }
  else { const classification = await launchLogin(); console.log(`classification = ${classification}`); if (classification !== "CODEX_LOGIN_LAUNCHED") process.exitCode = 1; }
} else if (args.has("--standalone-smoke")) runSmoke(args);
else { console.log("Use --login-help, --status, --status-diagnostics, --session-parity, --inspect-last-smoke-content, --launch-login --confirm-launch-codex-login, or --standalone-smoke --confirm-real-codex"); process.exitCode = 2; }
