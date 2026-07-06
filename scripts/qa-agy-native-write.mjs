import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import { compareBaselines, snapshotWorkspace } from "./qa-agy-generic-live-core.mjs";
import {
  NATIVE_SMOKE_CONTENT, NATIVE_SMOKE_NAME, GENERIC_SMOKE_NAME,
  assertSafeNativeArgs, classifyNativeResult, compareNativeBaselines,
  nativeBaseline, resolveAgyOnPath, sanitizedInvocationMatrix, sanitizedNativeResult,
} from "./qa-agy-native-write-core.mjs";
import {
  guardActiveWorkspace, guardTrustedWorkspace, prepareTrustedWorkspace, trustedWorkspacePath,
} from "./qa-agy-trusted-workspace-core.mjs";

const repositoryRoot = path.resolve(import.meta.dirname, "..");
const activeWorkspace = path.join(repositoryRoot, "workspace", "forgex-apply-test");
const confirmed = process.argv.includes("--confirm-native-agy");
const attested = process.argv.includes("--trusted-workspace-attested");
const authCheckOnly = process.argv.includes("--auth-check-only");

if (authCheckOnly && confirmed) finish("NATIVE_UNSAFE_ABORTED", "conflicting_native_modes", emptyResult(), 2);
if (!authCheckOnly && !confirmed) finish("NATIVE_UNSAFE_ABORTED", "explicit_confirmation_required", emptyResult(), 2);
if (!attested) finish("NATIVE_UNSAFE_ABORTED", "trusted_workspace_attestation_required", emptyResult(), 2);

let guardedActive;
let trustedWorkspace;
try {
  guardedActive = guardActiveWorkspace(repositoryRoot, activeWorkspace);
  if (!authCheckOnly) prepareTrustedWorkspace(repositoryRoot, guardedActive);
  trustedWorkspace = guardTrustedWorkspace(
    repositoryRoot, guardedActive, trustedWorkspacePath(repositoryRoot, guardedActive),
  ).workspaceRoot;
} catch (error) {
  finish("NATIVE_UNSAFE_ABORTED", safeReason(error), emptyResult(), 2);
}

const executable = resolveAgyOnPath();
if (!executable) finish("NATIVE_UNSAFE_ABORTED", "agy_not_installed", emptyResult(), 2);

if (authCheckOnly) runAuthCheckOnly(executable, guardedActive, trustedWorkspace);

const activeBefore = snapshotWorkspace(guardedActive);
const trustedBefore = nativeBaseline(trustedWorkspace);
if (trustedBefore.files.some((item) => [NATIVE_SMOKE_NAME, GENERIC_SMOKE_NAME].includes(item.relative_path))) {
  finish("NATIVE_UNSAFE_ABORTED", "smoke_baseline_not_clean", emptyResult(), 2);
}

const instruction = nativeInstruction();
const args = ["-p", instruction];
try { assertSafeNativeArgs(args); }
catch (error) { finish("NATIVE_UNSAFE_ABORTED", safeReason(error), emptyResult(), 2); }

const result = spawnSync(executable, args, {
  cwd: trustedWorkspace,
  encoding: "utf8",
  input: "",
  shell: false,
  windowsHide: true,
  timeout: 330_000,
  maxBuffer: 1024 * 1024,
});

const trustedAfter = nativeBaseline(trustedWorkspace);
const activeUnchanged = compareBaselines(activeBefore, snapshotWorkspace(guardedActive));
const diff = compareNativeBaselines(trustedBefore, trustedAfter);
const smokePath = path.join(trustedWorkspace, NATIVE_SMOKE_NAME);
const smokeExists = fs.existsSync(smokePath) && fs.statSync(smokePath).isFile() && !fs.lstatSync(smokePath).isSymbolicLink();
const smokeMatches = smokeExists && fs.readFileSync(smokePath, "utf8") === NATIVE_SMOKE_CONTENT;
const classification = classifyNativeResult({
  status: result.status, signal: result.signal, errorCode: result.error?.code,
  stdout: result.stdout, stderr: result.stderr, diff, smokeExists, smokeMatches, activeUnchanged,
});
const publicResult = sanitizedNativeResult(classification, diff, 1, smokeExists, activeUnchanged);
finish(classification, classification.toLowerCase(), publicResult, classification === "NATIVE_WRITE_PASS" ? 0 : 1);

function nativeInstruction() {
  return [
    `Inside the current working directory, create a new file named ${NATIVE_SMOKE_NAME}.`,
    "", "The file content must be exactly:", NATIVE_SMOKE_CONTENT.trimEnd(), "",
    "Only create this one file.", "Do not modify any existing file.", "Do not run build commands.",
    "Do not install dependencies.", "Do not access the network.",
  ].join("\n");
}

function runAuthCheckOnly(executable, active, trusted) {
  const activeBefore = snapshotWorkspace(active);
  const trustedBefore = nativeBaseline(trusted);
  const versionResult = spawnSync(executable, ["--version"], {
    cwd: trusted, encoding: "utf8", input: "", shell: false, windowsHide: true, timeout: 5_000, maxBuffer: 64 * 1024,
  });
  const version = String(versionResult.stdout || "").trim();
  const versionValid = versionResult.status === 0 && /^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/.test(version);
  const diff = compareNativeBaselines(trustedBefore, nativeBaseline(trusted));
  const activeUnchanged = compareBaselines(activeBefore, snapshotWorkspace(active));
  const clean = diff.changedCount === 0 && diff.markerUnchanged && activeUnchanged;
  const classification = versionValid && clean ? "NATIVE_AUTH_STATUS_UNAVAILABLE" : "NATIVE_AUTH_UNKNOWN";
  finish(classification, classification.toLowerCase(), {
    classification,
    version_detected: versionValid,
    auth_status_command_available: false,
    auth_probe_execution_count: 0,
    version_probe_execution_count: 1,
    write_execution_count: 0,
    changed_file_count: diff.changedCount,
    active_workspace_unchanged: activeUnchanged,
    marker_unchanged: diff.markerUnchanged,
    invocation_matrix: sanitizedInvocationMatrix(),
  }, classification === "NATIVE_AUTH_STATUS_UNAVAILABLE" ? 0 : 1);
}

function emptyResult() {
  return { execution_count: 0, changed_file_count: 0, created_file_count: 0, modified_file_count: 0, deleted_file_count: 0, smoke_file_created: false, active_workspace_unchanged: true, marker_unchanged: true };
}

function safeReason(error) {
  return error instanceof Error && /^[a-z0-9_]+$/i.test(error.message) ? error.message : "unknown_safe_failure";
}

function finish(classification, reason, details, code) {
  console.log(classification);
  console.log(JSON.stringify({ ...details, classification, reason }));
  process.exit(code);
}
