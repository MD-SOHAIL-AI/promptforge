import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { resolveCommandOnPath, snapshotWorkspace } from "./qa-agy-generic-live-core.mjs";
import { TRUSTED_MARKER } from "./qa-agy-trusted-workspace-core.mjs";

export const NATIVE_SMOKE_NAME = "AGY_NATIVE_SMOKE.txt";
export const GENERIC_SMOKE_NAME = "AGY_GENERIC_SMOKE.txt";
export const NATIVE_SMOKE_CONTENT = "ForgeX native AGY minimal write reproduction completed.\n";
export const MAX_NATIVE_ATTEMPTS = 4;
export const DANGEROUS_PERMISSION_FLAG = "--dangerously-skip-permissions";

export function resolveAgyOnPath(env = process.env, platform = process.platform) {
  return resolveCommandOnPath("agy", env, platform);
}

export function assertSafeNativeArgs(args) {
  if (!Array.isArray(args) || args.length !== 2 || args[0] !== "-p" || typeof args[1] !== "string" || !args[1]) {
    throw new Error("native_invocation_rejected");
  }
  if (args.includes(DANGEROUS_PERMISSION_FLAG) || args[1].includes(DANGEROUS_PERMISSION_FLAG)) {
    throw new Error("dangerous_permission_flag_rejected");
  }
  return true;
}

export function nativeBaseline(workspaceRoot) {
  const marker = path.join(workspaceRoot, TRUSTED_MARKER);
  const markerHash = fs.existsSync(marker)
    ? crypto.createHash("sha256").update(fs.readFileSync(marker)).digest("hex")
    : null;
  const files = snapshotWorkspace(workspaceRoot).filter((item) => item.relative_path !== TRUSTED_MARKER);
  return { files, markerHash };
}

export function compareNativeBaselines(before, after) {
  const beforeMap = new Map(before.files.map((item) => [item.relative_path, item.sha256]));
  const afterMap = new Map(after.files.map((item) => [item.relative_path, item.sha256]));
  const created = [];
  const modified = [];
  const deleted = [];
  for (const [name, hash] of afterMap) {
    if (!beforeMap.has(name)) created.push(name);
    else if (beforeMap.get(name) !== hash) modified.push(name);
  }
  for (const name of beforeMap.keys()) if (!afterMap.has(name)) deleted.push(name);
  return {
    created: created.sort(), modified: modified.sort(), deleted: deleted.sort(),
    changedCount: created.length + modified.length + deleted.length,
    markerUnchanged: before.markerHash !== null && before.markerHash === after.markerHash,
  };
}

export function classifyNativeResult({ status, signal, errorCode, stdout, stderr, diff, smokeExists, smokeMatches, activeUnchanged }) {
  const combined = `${stdout || ""}\n${stderr || ""}`.toLowerCase();
  if (signal || errorCode === "ETIMEDOUT") return "NATIVE_PROVIDER_ERROR";
  if (/(sign in|login|authentication required|unauthenticated)/.test(combined)) return "NATIVE_AUTH_BLOCKED";
  if (/(permission|approval|do you trust|trusted workspace|access denied)/.test(combined)) return "NATIVE_PERMISSION_BLOCKED";
  if (/(unknown (command|flag)|flag provided but not defined|invalid argument|usage of agy)/.test(combined)) return "NATIVE_INVOCATION_UNSUPPORTED";
  if (status !== 0) return "NATIVE_PROVIDER_ERROR";
  if (smokeExists && smokeMatches && diff.changedCount === 1 && diff.created[0] === NATIVE_SMOKE_NAME && diff.markerUnchanged && activeUnchanged) {
    return "NATIVE_WRITE_PASS";
  }
  if (!smokeExists && diff.changedCount === 0 && diff.markerUnchanged && activeUnchanged) return "NATIVE_NO_CHANGES";
  return "NATIVE_UNKNOWN_SAFE_FAILURE";
}

export function sanitizedNativeResult(classification, diff, executionCount, smokeCreated, activeUnchanged) {
  return {
    classification,
    execution_count: executionCount,
    changed_file_count: diff.changedCount,
    created_file_count: diff.created.length,
    modified_file_count: diff.modified.length,
    deleted_file_count: diff.deleted.length,
    smoke_file_created: smokeCreated,
    active_workspace_unchanged: activeUnchanged,
    marker_unchanged: diff.markerUnchanged,
  };
}

export function classifyAuthProbe({ available, status, stdout, stderr }) {
  if (!available) return "NATIVE_AUTH_STATUS_UNAVAILABLE";
  const combined = `${stdout || ""}\n${stderr || ""}`.toLowerCase();
  if (/(sign in|login|authentication required|unauthenticated|not authenticated)/.test(combined)) return "NATIVE_AUTH_BLOCKED";
  if (status !== 0) return "NATIVE_AUTH_UNKNOWN";
  if (/(authenticated|signed in|session ready|auth ready)/.test(combined)) return "NATIVE_AUTH_READY";
  return "NATIVE_AUTH_UNKNOWN";
}

export function sanitizedInvocationMatrix() {
  return [
    { variant: "agy_print", help_confirmed: true, auth_requirement_known: false, write_capable: "unknown", safe_to_test: true, reason: "official_non_interactive_mode" },
    ...["run", "exec", "agent", "workspace"].map((variant) => ({
      variant: `agy_${variant}`, help_confirmed: false, auth_requirement_known: false,
      write_capable: "unknown", safe_to_test: false, reason: "not_supported_by_local_help",
    })),
  ];
}

export function nativeFollowupDecision(nativeClassification, genericClassification = null) {
  if (nativeClassification !== "NATIVE_WRITE_PASS") {
    return { rerun_generic: false, outcome: "native_write_not_proven", comparison_diagnostics: [] };
  }
  if (genericClassification === null) {
    return { rerun_generic: true, outcome: "generic_rerun_required", comparison_diagnostics: [] };
  }
  if (genericClassification === "GENERIC_WRITE_PASS") {
    return { rerun_generic: false, outcome: "native_and_generic_pass", comparison_diagnostics: [] };
  }
  return {
    rerun_generic: false,
    outcome: "native_pass_generic_failed",
    comparison_diagnostics: ["cwd", "argv", "instruction_shape", "environment_allowlist", "trust_state", "reset_timing", "diff_source", "ignored_filters"],
  };
}

export function validateInvocationMatrix(variants) {
  if (!Array.isArray(variants) || variants.length > MAX_NATIVE_ATTEMPTS) throw new Error("native_attempt_limit_exceeded");
  for (const variant of variants) assertSafeNativeArgs(variant);
  return true;
}
