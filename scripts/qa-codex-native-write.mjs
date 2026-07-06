import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import {
  CODEX_EXTERNAL_SANDBOX_ROOT, CODEX_MARKER, CODEX_MARKER_CONTENT, CODEX_SMOKE_CONTENT,
  CODEX_SMOKE_NAME, CODEX_TIMEOUT_MS, buildCodexArgs, classifyCodexNative,
  compareSnapshots, createManagedSandbox, guardManagedSandbox, manualStandaloneAttested,
  resolveCodexLauncher, sanitizedResult, snapshotTree,
} from "./qa-codex-native-write-core.mjs";

const repositoryRoot = path.resolve(import.meta.dirname, "..");
const activeWorkspace = path.join(repositoryRoot, "workspace", "forgex-apply-test");
const resultsDocument = path.join(repositoryRoot, "docs", "phase-2-5-8-12C-codex-cli-subscription-bridge-integration-results.md");
const stateDirectory = path.join(repositoryRoot, ".promptforge", "state");

if (!process.argv.includes("--confirm-real-codex")) finish("CODEX_NATIVE_UNSAFE_ABORTED", "explicit_confirmation_required", emptyResult(), 2);
if (!process.argv.includes("--subscription-bridge-retry")) finish("CODEX_NATIVE_UNSAFE_ABORTED", "subscription_bridge_retry_required", emptyResult(), 2);
if (!manualStandaloneAttested(resultsDocument)) finish("CODEX_NATIVE_UNSAFE_ABORTED", "manual_standalone_attestation_required", emptyResult(), 2);

const launcher = resolveCodexLauncher();
if (!launcher) finish("CODEX_NATIVE_UNSAFE_ABORTED", "codex_not_installed", emptyResult(), 2);

const runId = `codex-${crypto.randomBytes(12).toString("hex")}`;
let guarded;
try {
  guarded = createManagedSandbox(repositoryRoot, runId, activeWorkspace, process.env, CODEX_EXTERNAL_SANDBOX_ROOT);
  guardManagedSandbox(repositoryRoot, guarded.sandboxRoot, activeWorkspace, process.env, CODEX_EXTERNAL_SANDBOX_ROOT);
} catch (error) {
  finish("CODEX_NATIVE_UNSAFE_ABORTED", safeReason(error), emptyResult(), 2);
}

const versionProbe = spawnSync(launcher.command, [...launcher.prefixArgs, "--version"], {
  cwd: guarded.sandboxRoot, encoding: "utf8", input: "", shell: false, windowsHide: true,
  timeout: 5_000, maxBuffer: 64 * 1024, env: safeEnvironment(process.env),
});
const version = String(versionProbe.stdout || "").trim();
if (versionProbe.status !== 0) finish("CODEX_NATIVE_UNSAFE_ABORTED", "codex_version_not_detected", emptyResult(), 2);
if (!/^[\x20-\x7e]{1,80}$/.test(version)) finish("CODEX_NATIVE_UNSAFE_ABORTED", "codex_version_invalid", emptyResult(), 2);

const activeBefore = snapshotTree(activeWorkspace);
const sandboxBefore = snapshotTree(guarded.sandboxRoot);
if (sandboxBefore.length !== 1 || sandboxBefore[0].relative_path !== CODEX_MARKER || fs.existsSync(path.join(guarded.sandboxRoot, CODEX_SMOKE_NAME))) {
  finish("CODEX_NATIVE_UNSAFE_ABORTED", "sandbox_baseline_invalid", emptyResult(), 2);
}

let args;
try { args = buildCodexArgs(guarded.sandboxRoot); }
catch (error) { finish("CODEX_NATIVE_UNSAFE_ABORTED", safeReason(error), emptyResult(), 2); }

console.log("argv_shape = global_approval_before_exec");
console.log("sandbox_root_kind = external_managed_codex_sandbox");
console.log("shell_false = true");
console.log("dangerous_flags_used = false");
console.log("codex_version_detected = true");
console.log("manual_standalone_attested = true");

const result = spawnSync(launcher.command, [...launcher.prefixArgs, ...args], {
  cwd: guarded.sandboxRoot,
  encoding: "utf8",
  input: "",
  shell: false,
  windowsHide: true,
  timeout: CODEX_TIMEOUT_MS,
  maxBuffer: 1024 * 1024,
  env: safeEnvironment(process.env),
});

let classification;
let details;
try {
  guardManagedSandbox(repositoryRoot, guarded.sandboxRoot, activeWorkspace, process.env, CODEX_EXTERNAL_SANDBOX_ROOT);
  const sandboxAfter = snapshotTree(guarded.sandboxRoot);
  const activeAfter = snapshotTree(activeWorkspace);
  const diff = compareSnapshots(sandboxBefore, sandboxAfter);
  const activeUnchanged = compareSnapshots(activeBefore, activeAfter).changedCount === 0;
  const marker = path.join(guarded.sandboxRoot, CODEX_MARKER);
  const markerUnchanged = fs.existsSync(marker) && fs.readFileSync(marker, "utf8") === CODEX_MARKER_CONTENT;
  const smoke = path.join(guarded.sandboxRoot, CODEX_SMOKE_NAME);
  const smokeExists = fs.existsSync(smoke) && fs.statSync(smoke).isFile() && !fs.lstatSync(smoke).isSymbolicLink();
  const smokeMatches = smokeExists && fs.readFileSync(smoke, "utf8") === CODEX_SMOKE_CONTENT;
  classification = classifyCodexNative({ status: result.status, signal: result.signal, errorCode: result.error?.code, stdout: result.stdout, stderr: result.stderr, diff, smokeExists, smokeMatches, markerUnchanged, activeUnchanged });
  let reviewCreated = false;
  if (classification === "CODEX_SUBSCRIPTION_BRIDGE_PASS") {
    const review = spawnSync("python", ["-m", "backend.bridges.codex_subscription_review", "--sandbox", guarded.sandboxRoot, "--state-dir", stateDirectory], {
      cwd: repositoryRoot, encoding: "utf8", input: "", shell: false, windowsHide: true, timeout: 30_000, maxBuffer: 64 * 1024,
      env: safeEnvironment(process.env),
    });
    reviewCreated = review.status === 0 && /"review_created"\s*:\s*true/.test(String(review.stdout || ""));
    if (!reviewCreated) classification = "CODEX_NATIVE_UNKNOWN_SAFE_FAILURE";
  }
  details = sanitizedResult(classification, diff, smokeExists, activeUnchanged, markerUnchanged, reviewCreated);
} catch (error) {
  classification = "CODEX_NATIVE_UNSAFE_ABORTED";
  details = { ...emptyResult(), reason: safeReason(error), execution_count: 1 };
}

try {
  fs.mkdirSync(stateDirectory, { recursive: true });
  fs.writeFileSync(
    path.join(stateDirectory, "codex-subscription-bridge-status.json"),
    `${JSON.stringify({ ...details, classification }, null, 2)}\n`,
    { encoding: "utf8" },
  );
} catch {
  classification = "CODEX_NATIVE_UNKNOWN_SAFE_FAILURE";
  details = { ...details, classification, review_created: false };
}

finish(classification, classification.toLowerCase(), details, classification === "CODEX_SUBSCRIPTION_BRIDGE_PASS" ? 0 : 1);

function safeEnvironment(env) {
  const allowed = ["PATH", "Path", "PATHEXT", "SystemRoot", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "CODEX_HOME", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR"];
  return Object.fromEntries(allowed.filter((name) => typeof env[name] === "string").map((name) => [name, env[name]]));
}
function emptyResult() { return { execution_count: 0, created_file_count: 0, modified_file_count: 0, deleted_file_count: 0, smoke_file_created: false, review_created: false, active_workspace_unchanged: true, marker_unchanged: true, raw_prompt_persisted: false, raw_output_persisted: false, auth_files_read: false, dangerous_flags_used: false, production_routing_enabled: false }; }
function safeReason(error) { return error instanceof Error && /^[a-z0-9_]+$/i.test(error.message) ? error.message : "unknown_safe_failure"; }
function finish(classification, reason, details, code) { console.log(classification); console.log(JSON.stringify({ ...details, classification, reason })); process.exit(code); }
