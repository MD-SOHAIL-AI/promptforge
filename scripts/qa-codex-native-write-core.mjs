import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const CODEX_EXTERNAL_SANDBOX_ROOT = "C:\\forgex-codex-sandboxes";
export const CODEX_MARKER = "README_FORGEX_CODEX_SANDBOX.txt";
export const CODEX_MARKER_CONTENT = [
  "This is a disposable ForgeX Codex subscription bridge sandbox.",
  "Codex may only create CODEX_GENERIC_SMOKE.txt for this validation.",
  "",
].join("\n");
export const CODEX_SMOKE_NAME = "CODEX_GENERIC_SMOKE.txt";
export const CODEX_SMOKE_CONTENT = "ForgeX Codex subscription bridge smoke completed.";
export const CODEX_SMOKE_CONTENT_HASH = crypto.createHash("sha256").update(CODEX_SMOKE_CONTENT).digest("hex");
export const CODEX_TIMEOUT_MS = 330_000;
export const DANGEROUS_CODEX_FLAGS = Object.freeze([
  "--dangerously-bypass-approvals-and-sandbox",
  "--yolo",
  "--full-auto",
  "danger-full-access",
  "--dangerously-bypass-hook-trust",
  "--add-dir",
  "--search",
  "--output-last-message",
  "-o",
  "--json",
]);
export const OMITTED_SUBSCRIPTION_RETRY_FLAGS = Object.freeze([
  "--ignore-user-config",
  "--ephemeral",
  "--skip-git-repo-check",
]);

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

export function codexInstruction() {
  return [
    "Create exactly one file named CODEX_GENERIC_SMOKE.txt containing exactly:",
    CODEX_SMOKE_CONTENT,
    "",
    "Do not create or modify anything else.",
    "Do not run build commands.",
    "Do not install dependencies.",
    "Do not access the network.",
  ].join("\n");
}

export function buildCodexArgs(sandboxPath, instruction = codexInstruction()) {
  const args = [
    "--ask-for-approval", "never",
    "exec",
    "--sandbox", "workspace-write",
    "--cd", sandboxPath,
    instruction,
  ];
  assertSafeCodexArgs(args, sandboxPath);
  return args;
}

export function assertSafeCodexArgs(args, sandboxPath) {
  if (!Array.isArray(args) || typeof sandboxPath !== "string" || !sandboxPath) throw new Error("codex_invocation_rejected");
  const expectedPrefix = ["--ask-for-approval", "never", "exec", "--sandbox", "workspace-write", "--cd", sandboxPath];
  const lowered = args.map((value) => String(value).toLowerCase());
  if (DANGEROUS_CODEX_FLAGS.some((flag) => lowered.includes(flag))) throw new Error("dangerous_codex_flag_rejected");
  if (OMITTED_SUBSCRIPTION_RETRY_FLAGS.some((flag) => lowered.includes(flag))) throw new Error("subscription_retry_extra_flag_rejected");
  if (args.length !== expectedPrefix.length + 1) throw new Error("codex_invocation_rejected");
  if (!expectedPrefix.every((value, index) => args[index] === value)) throw new Error("codex_invocation_rejected");
  if (typeof args.at(-1) !== "string" || args.at(-1).length === 0) throw new Error("codex_invocation_rejected");
  if (lowered.includes("on-failure") || lowered.includes("on-request")) throw new Error("unsafe_codex_policy_rejected");
  return true;
}

export function invocationMatrix() {
  return [
    { variant: "codex_exec_subscription_bridge", help_confirmed: true, auth_mode: "official_cli_auth", write_capable: true, safe_to_test: true, reason: "manual_standalone_write_pass" },
    { variant: "codex_run", help_confirmed: false, auth_mode: "unknown", write_capable: false, safe_to_test: false, reason: "not_supported_by_local_help" },
    { variant: "codex_agent", help_confirmed: false, auth_mode: "unknown", write_capable: false, safe_to_test: false, reason: "not_supported_by_local_help" },
  ];
}

export function manualStandaloneAttested(resultsDocument) {
  if (!fs.existsSync(resultsDocument) || !fs.statSync(resultsDocument).isFile()) return false;
  const text = fs.readFileSync(resultsDocument, "utf8");
  return text.includes("CODEX_STANDALONE_WRITE_PASS") && text.includes("codex --ask-for-approval never exec --sandbox workspace-write --cd C:\\forgex-codex-smoke");
}

export function createManagedSandbox(repositoryRoot, runId, activeWorkspace, env = process.env, managedRoot = CODEX_EXTERNAL_SANDBOX_ROOT) {
  if (!/^codex-[a-f0-9]{16,64}$/.test(runId)) throw new Error("sandbox_run_id_rejected");
  const repo = realDirectory(repositoryRoot, "repository_root_invalid");
  const active = realDirectory(activeWorkspace, "active_workspace_invalid");
  const requestedManagedRoot = path.resolve(managedRoot);
  assertExternalManagedRoot(repo, active, requestedManagedRoot, env);
  fs.mkdirSync(requestedManagedRoot, { recursive: true });
  const safeManagedRoot = realDirectory(requestedManagedRoot, "sandbox_managed_root_invalid");
  assertExternalManagedRoot(repo, active, safeManagedRoot, env);
  const root = path.join(safeManagedRoot, runId);
  fs.mkdirSync(root, { recursive: false });
  fs.writeFileSync(path.join(root, CODEX_MARKER), CODEX_MARKER_CONTENT, { encoding: "utf8", flag: "wx" });
  return guardManagedSandbox(repo, root, active, env, safeManagedRoot);
}

export function guardManagedSandbox(repositoryRoot, requestedSandbox, activeWorkspace, env = process.env, managedRoot = CODEX_EXTERNAL_SANDBOX_ROOT) {
  const repo = realDirectory(repositoryRoot, "repository_root_invalid");
  const active = realDirectory(activeWorkspace, "active_workspace_invalid");
  const safeManagedRoot = realDirectory(managedRoot, "sandbox_managed_root_invalid");
  assertExternalManagedRoot(repo, active, safeManagedRoot, env);
  const requested = path.resolve(requestedSandbox);
  if (!fs.existsSync(requested) || fs.lstatSync(requested).isSymbolicLink()) throw new Error("sandbox_missing_or_linked");
  const root = realDirectory(requested, "sandbox_invalid");
  const relative = path.relative(safeManagedRoot, root);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative) || path.dirname(relative) !== ".") throw new Error("sandbox_containment_rejected");
  if (samePath(root, repo)) throw new Error("sandbox_repository_root_rejected");
  if (pathsOverlap(root, active)) throw new Error("sandbox_active_workspace_rejected");
  if (isSensitivePath(root, repo, active, env)) throw new Error("sandbox_sensitive_root_rejected");
  rejectLinksAndSpecialEntries(root);
  const marker = path.join(root, CODEX_MARKER);
  if (!fs.existsSync(marker) || fs.lstatSync(marker).isSymbolicLink() || !fs.statSync(marker).isFile()) throw new Error("sandbox_marker_missing");
  if (fs.readFileSync(marker, "utf8") !== CODEX_MARKER_CONTENT) throw new Error("sandbox_marker_invalid");
  return { repositoryRoot: repo, activeWorkspace: active, managedRoot: safeManagedRoot, sandboxRoot: root };
}

export function snapshotTree(root) {
  const base = realDirectory(root, "snapshot_root_invalid");
  const items = [];
  walk(base, base, items);
  return items.sort((left, right) => left.relative_path.localeCompare(right.relative_path));
}

function walk(base, current, items) {
  for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
    const child = path.join(current, entry.name);
    if (entry.isSymbolicLink()) throw new Error("snapshot_link_rejected");
    if (entry.isDirectory()) walk(base, child, items);
    else if (entry.isFile()) items.push({ relative_path: path.relative(base, child).replaceAll("\\", "/"), sha256: crypto.createHash("sha256").update(fs.readFileSync(child)).digest("hex") });
    else throw new Error("snapshot_special_entry_rejected");
  }
}

export function compareSnapshots(before, after) {
  const oldMap = new Map(before.map((item) => [item.relative_path, item.sha256]));
  const newMap = new Map(after.map((item) => [item.relative_path, item.sha256]));
  const created = [], modified = [], deleted = [];
  for (const [name, hash] of newMap) {
    if (!oldMap.has(name)) created.push(name);
    else if (oldMap.get(name) !== hash) modified.push(name);
  }
  for (const name of oldMap.keys()) if (!newMap.has(name)) deleted.push(name);
  return { created: created.sort(), modified: modified.sort(), deleted: deleted.sort(), changedCount: created.length + modified.length + deleted.length };
}

export function classifyCodexNative({ status, signal, errorCode, stdout, stderr, diff, smokeExists, smokeMatches, markerUnchanged, activeUnchanged }) {
  const combined = `${stdout || ""}\n${stderr || ""}`.toLowerCase();
  if (signal || errorCode === "ETIMEDOUT") return "CODEX_NATIVE_TIMEOUT";
  if (/(usage limit|rate limit|too many requests|try again later|weekly limit|monthly limit|limit reached)/.test(combined)) return "CODEX_NATIVE_USAGE_LIMIT_REACHED";
  if (/(quota exceeded|insufficient quota|billing quota|credits exhausted)/.test(combined)) return "CODEX_NATIVE_QUOTA_EXCEEDED";
  if (/(not logged in|login required|authentication required|unauthenticated|401 unauthorized|invalid authentication|session expired)/.test(combined)) return "CODEX_NATIVE_AUTH_BLOCKED";
  if (/(permission denied|operation not permitted|approval required|workspace.*not trusted|read-only|native permission)/.test(combined)) return "CODEX_NATIVE_PERMISSION_BLOCKED";
  if (/(unknown (command|option|flag)|unexpected argument|unrecognized option|usage: codex)/.test(combined)) return "CODEX_NATIVE_INVOCATION_UNSUPPORTED";
  if (status !== 0) return "CODEX_NATIVE_UNKNOWN_SAFE_FAILURE";
  if (smokeExists && !smokeMatches) return "CODEX_NATIVE_CONTENT_INVALID";
  const exact = diff.changedCount === 1 && diff.created.length === 1 && diff.created[0] === CODEX_SMOKE_NAME && diff.modified.length === 0 && diff.deleted.length === 0;
  if (exact && smokeExists && smokeMatches && markerUnchanged && activeUnchanged) return "CODEX_SUBSCRIPTION_BRIDGE_PASS";
  if (!markerUnchanged || !activeUnchanged || diff.changedCount > 0) return "CODEX_NATIVE_EXTRA_CHANGES";
  if (!smokeExists && diff.changedCount === 0) return "CODEX_NATIVE_NO_CHANGES";
  return "CODEX_NATIVE_UNKNOWN_SAFE_FAILURE";
}

export function sanitizedResult(classification, diff, smokeCreated, activeUnchanged, markerUnchanged, reviewCreated = false, executionCount = 1) {
  return {
    classification,
    instruction_type: "codex_subscription_bridge_smoke",
    expected_filename: CODEX_SMOKE_NAME,
    expected_content_hash: CODEX_SMOKE_CONTENT_HASH,
    execution_count: executionCount,
    created_file_count: diff.created.length,
    modified_file_count: diff.modified.length,
    deleted_file_count: diff.deleted.length,
    smoke_file_created: smokeCreated,
    review_created: reviewCreated,
    active_workspace_unchanged: activeUnchanged,
    marker_unchanged: markerUnchanged,
    raw_prompt_persisted: false,
    raw_output_persisted: false,
    auth_files_read: false,
    dangerous_flags_used: false,
    production_routing_enabled: false,
  };
}

function assertExternalManagedRoot(repo, active, managedRoot, env) {
  if (pathsOverlap(managedRoot, repo)) throw new Error("sandbox_repository_root_rejected");
  if (pathsOverlap(managedRoot, active)) throw new Error("sandbox_active_workspace_rejected");
  if (isSensitivePath(managedRoot, repo, active, env)) throw new Error("sandbox_sensitive_root_rejected");
}

function realDirectory(value, code) {
  const requested = path.resolve(value);
  if (!fs.existsSync(requested) || fs.lstatSync(requested).isSymbolicLink() || !fs.statSync(requested).isDirectory()) throw new Error(code);
  const real = fs.realpathSync.native(requested);
  if (!samePath(real, requested)) throw new Error(code);
  return real;
}

function rejectLinksAndSpecialEntries(root) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const child = path.join(root, entry.name);
    if (entry.isSymbolicLink()) throw new Error("sandbox_link_rejected");
    const real = fs.realpathSync.native(child);
    if (!samePath(real, child)) throw new Error("sandbox_reparse_rejected");
    if (entry.isDirectory()) rejectLinksAndSpecialEntries(child);
    else if (!entry.isFile()) throw new Error("sandbox_special_entry_rejected");
  }
}

function sensitiveRoots(repo, active, env) {
  const home = path.resolve(env.USERPROFILE || env.HOME || os.homedir());
  return [repo, active, home, path.join(home, "Desktop"), env.OneDrive, env.OneDriveConsumer, env.OneDriveCommercial]
    .filter(Boolean).map((item) => path.resolve(item));
}

function isSensitivePath(candidate, repo, active, env) {
  if (samePath(candidate, path.parse(candidate).root)) return true;
  return sensitiveRoots(repo, active, env).some((root) => samePath(candidate, root) || isInside(root, candidate));
}

function isInside(parent, child) {
  const relative = path.relative(parent, child);
  return Boolean(relative) && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function pathsOverlap(first, second) {
  const forward = path.relative(first, second);
  const reverse = path.relative(second, first);
  return forward === "" || (!forward.startsWith("..") && !path.isAbsolute(forward)) || reverse === "" || (!reverse.startsWith("..") && !path.isAbsolute(reverse));
}

function samePath(first, second) {
  return path.resolve(first).toLowerCase() === path.resolve(second).toLowerCase();
}
