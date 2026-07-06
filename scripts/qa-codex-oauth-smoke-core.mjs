import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const OAUTH_SMOKE_ROOT = "C:\\forgex-codex-oauth-smoke";
export const OAUTH_MARKER = "README_FORGEX_CODEX_OAUTH_SMOKE_SANDBOX.txt";
export const OAUTH_MARKER_CONTENT = "This is a disposable ForgeX Codex OAuth smoke sandbox.\nDo not use as active workspace.\n";
export const OAUTH_SMOKE_FILE = "CODEX_OAUTH_BRIDGE_SMOKE.txt";
export const OAUTH_SMOKE_CONTENT = "ForgeX Codex OAuth bridge smoke completed.";
export const OAUTH_SMOKE_HASH = crypto.createHash("sha256").update(OAUTH_SMOKE_CONTENT).digest("hex");
export const OAUTH_PROMPT_VARIANT = "strict_single_line_v2";
export const OAUTH_SMOKE_TIMEOUT_MS = 330_000;
export const DANGEROUS_CODEX_FLAGS = Object.freeze([
  "--dangerously-bypass-approvals-and-sandbox", "--yolo", "--full-auto",
  "danger-full-access", "--dangerously-bypass-hook-trust", "--add-dir",
  "--search", "--output-last-message", "-o", "--json",
]);

export function smokePrompt() {
  return [
    `Create exactly one file named ${OAUTH_SMOKE_FILE}.`,
    "",
    "The file content must be exactly this single line, with no quotes and no markdown:",
    OAUTH_SMOKE_CONTENT,
    "",
    "Rules:",
    "- Do not add a second line.",
    "- Do not add explanation.",
    "- Do not add markdown.",
    "- Do not add code fences.",
    "- Do not create or modify any other file.",
    "- Do not modify existing files.",
    "- Do not access the network.",
    "- Do not install dependencies.",
    "- Do not run build commands.",
  ].join("\n");
}

export function validateOAuthSmokeContent(actualBytes) {
  const bytes = Buffer.isBuffer(actualBytes) ? actualBytes : Buffer.from(actualBytes);
  const hasBom = bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf;
  const text = bytes.toString("utf8");
  const withoutBom = hasBom ? text.slice(1) : text;
  const crlfCount = (withoutBom.match(/\r\n/g) || []).length;
  const loneLfCount = (withoutBom.replace(/\r\n/g, "").match(/\n/g) || []).length;
  const loneCrCount = (withoutBom.replace(/\r\n/g, "").match(/\r/g) || []).length;
  const lineEndingKind = crlfCount && (loneLfCount || loneCrCount) ? "mixed" : crlfCount ? "crlf" : loneLfCount ? "lf" : loneCrCount ? "mixed" : "none";
  const normalized = withoutBom.replace(/\r\n/g, "\n");
  const normalizedForMatch = normalized.replace(/\n+$/, "");
  const normalizedMatches = normalizedForMatch === OAUTH_SMOKE_CONTENT;
  const leadingWhitespace = /^[ \t]/.test(withoutBom);
  const trailingWhitespace = /[ \t](?:\r?\n)*$/.test(withoutBom);
  const trailingNewline = /(?:\r\n|\n)$/.test(withoutBom);
  let firstDifferenceKind = "unknown";
  if (hasBom) firstDifferenceKind = "bom";
  else if (crlfCount || loneCrCount) firstDifferenceKind = "line_ending";
  else if (trailingNewline && normalizedMatches) firstDifferenceKind = "trailing_newline";
  else if (leadingWhitespace) firstDifferenceKind = "leading_whitespace";
  else if (trailingWhitespace) firstDifferenceKind = "trailing_whitespace";
  else if (normalizedForMatch.length !== OAUTH_SMOKE_CONTENT.length) firstDifferenceKind = "length";
  else if (!normalizedMatches) firstDifferenceKind = "content_text";
  return {
    valid: normalizedMatches,
    expected_content_hash: OAUTH_SMOKE_HASH,
    actual_content_hash: crypto.createHash("sha256").update(bytes).digest("hex"),
    actual_byte_count: bytes.length,
    expected_byte_count: Buffer.byteLength(OAUTH_SMOKE_CONTENT, "utf8"),
    actual_line_count: withoutBom.length === 0 ? 0 : normalized.split("\n").length,
    expected_line_count: 1,
    has_utf8_bom: hasBom,
    line_ending_kind: lineEndingKind,
    leading_whitespace: leadingWhitespace,
    trailing_whitespace: trailingWhitespace,
    trailing_newline: trailingNewline,
    normalized_content_matches: normalizedMatches,
    first_difference_kind: firstDifferenceKind,
    normalization_applied: normalizedMatches && (hasBom || crlfCount || trailingNewline) ? "eof_newline_or_bom_or_crlf_only" : "none",
  };
}

export function inspectKnownOAuthSmokeContent({ repositoryRoot, activeWorkspace, managedRoot = OAUTH_SMOKE_ROOT, metadata, env = process.env }) {
  if (!metadata || metadata.provider_id !== "codex_cli_oauth_bridge" || !/^oauth-[a-f0-9]{24}$/.test(String(metadata.run_id || ""))) return { classification: "CODEX_OAUTH_SMOKE_INSPECT_NOT_AVAILABLE" };
  try {
    const guarded = guardOAuthSandbox(repositoryRoot, activeWorkspace, path.join(managedRoot, metadata.run_id), env, managedRoot);
    const expected = path.join(guarded.sandboxRoot, OAUTH_SMOKE_FILE);
    if (!fs.existsSync(expected) || fs.lstatSync(expected).isSymbolicLink() || !fs.statSync(expected).isFile()) return { classification: "CODEX_OAUTH_SMOKE_INSPECT_UNKNOWN_SAFE_FAILURE" };
    const diagnostics = validateOAuthSmokeContent(fs.readFileSync(expected));
    return { classification: "CODEX_OAUTH_SMOKE_INSPECT_PASS", ...diagnostics, raw_content_printed: false, codex_executed: false, raw_output_persisted: false, raw_prompt_persisted: false, auth_files_read: false, tokens_read: false };
  } catch {
    return { classification: "CODEX_OAUTH_SMOKE_INSPECT_UNSAFE_ABORTED" };
  }
}

export function buildOAuthSmokeArgs(sandboxPath, prompt = smokePrompt()) {
  const args = ["--ask-for-approval", "never", "exec", "--sandbox", "workspace-write", "--cd", sandboxPath, prompt];
  assertSafeOAuthSmokeArgs(args, sandboxPath);
  return args;
}

export function assertSafeOAuthSmokeArgs(args, sandboxPath) {
  const prefix = ["--ask-for-approval", "never", "exec", "--sandbox", "workspace-write", "--cd", sandboxPath];
  if (!Array.isArray(args) || args.length !== prefix.length + 1 || !prefix.every((value, index) => args[index] === value)) throw new Error("codex_oauth_invocation_rejected");
  const lowered = args.map((value) => String(value).toLowerCase());
  if (DANGEROUS_CODEX_FLAGS.some((flag) => lowered.includes(flag))) throw new Error("dangerous_codex_flag_rejected");
  if (typeof args.at(-1) !== "string" || !args.at(-1)) throw new Error("codex_oauth_invocation_rejected");
  return true;
}

export function createOAuthSandbox(repositoryRoot, activeWorkspace, runId, env = process.env, managedRoot = OAUTH_SMOKE_ROOT) {
  if (!/^oauth-[a-f0-9]{24}$/.test(runId)) throw new Error("sandbox_run_id_rejected");
  const repo = realDirectory(repositoryRoot, "repository_root_invalid");
  const active = realDirectory(activeWorkspace, "active_workspace_invalid");
  const requestedRoot = path.resolve(managedRoot);
  assertExternalRoot(requestedRoot, repo, active, env);
  fs.mkdirSync(requestedRoot, { recursive: true });
  const root = realDirectory(requestedRoot, "sandbox_managed_root_invalid");
  assertExternalRoot(root, repo, active, env);
  const sandbox = path.join(root, runId);
  fs.mkdirSync(sandbox, { recursive: false });
  fs.writeFileSync(path.join(sandbox, OAUTH_MARKER), OAUTH_MARKER_CONTENT, { encoding: "utf8", flag: "wx" });
  const initialized = spawnSync("git", ["init", "--quiet", sandbox], { cwd: root, encoding: "utf8", input: "", shell: false, windowsHide: true, timeout: 10_000 });
  if (initialized.status !== 0 || !fs.existsSync(path.join(sandbox, ".git"))) throw new Error("sandbox_git_init_failed");
  return guardOAuthSandbox(repo, active, sandbox, env, root);
}

export function guardOAuthSandbox(repositoryRoot, activeWorkspace, requestedSandbox, env = process.env, managedRoot = OAUTH_SMOKE_ROOT) {
  const repo = realDirectory(repositoryRoot, "repository_root_invalid");
  const active = realDirectory(activeWorkspace, "active_workspace_invalid");
  const root = realDirectory(managedRoot, "sandbox_managed_root_invalid");
  assertExternalRoot(root, repo, active, env);
  const requested = path.resolve(requestedSandbox);
  if (!fs.existsSync(requested) || fs.lstatSync(requested).isSymbolicLink()) throw new Error("sandbox_missing_or_linked");
  const sandbox = realDirectory(requested, "sandbox_invalid");
  const relative = path.relative(root, sandbox);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative) || path.dirname(relative) !== ".") throw new Error("sandbox_containment_rejected");
  if (samePath(sandbox, repo) || overlaps(sandbox, repo)) throw new Error("sandbox_repository_root_rejected");
  if (overlaps(sandbox, active)) throw new Error("sandbox_active_workspace_rejected");
  rejectLinksAndSpecialEntries(sandbox);
  const marker = path.join(sandbox, OAUTH_MARKER);
  if (!fs.existsSync(marker) || fs.lstatSync(marker).isSymbolicLink() || fs.readFileSync(marker, "utf8") !== OAUTH_MARKER_CONTENT) throw new Error("sandbox_marker_invalid");
  return { repositoryRoot: repo, activeWorkspace: active, managedRoot: root, sandboxRoot: sandbox };
}

export function snapshotTree(root, { ignore = [] } = {}) {
  const base = realDirectory(root, "snapshot_root_invalid");
  const ignored = new Set(ignore.map((value) => value.toLowerCase()));
  const items = [];
  walk(base, base, items, ignored);
  return items.sort((a, b) => a.relative_path.localeCompare(b.relative_path));
}

function walk(base, current, items, ignored) {
  for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
    if (ignored.has(entry.name.toLowerCase())) continue;
    const child = path.join(current, entry.name);
    if (entry.isSymbolicLink()) throw new Error("snapshot_link_rejected");
    if (entry.isDirectory()) walk(base, child, items, ignored);
    else if (entry.isFile()) items.push({ relative_path: path.relative(base, child).replaceAll("\\", "/"), sha256: crypto.createHash("sha256").update(fs.readFileSync(child)).digest("hex") });
    else throw new Error("snapshot_special_entry_rejected");
  }
}

export function compareSnapshots(before, after) {
  const oldMap = new Map(before.map((item) => [item.relative_path, item.sha256]));
  const newMap = new Map(after.map((item) => [item.relative_path, item.sha256]));
  const created = [], modified = [], deleted = [];
  for (const [name, hash] of newMap) oldMap.has(name) ? (oldMap.get(name) !== hash && modified.push(name)) : created.push(name);
  for (const name of oldMap.keys()) if (!newMap.has(name)) deleted.push(name);
  return { created: created.sort(), modified: modified.sort(), deleted: deleted.sort(), changedCount: created.length + modified.length + deleted.length };
}

export function classifyOAuthSmoke({ status, signal, errorCode, stdout, stderr, diff, expectedExists, expectedValid, contentDiagnostics, markerUnchanged, activeUnchanged }) {
  const combined = `${stdout || ""}\n${stderr || ""}`.toLowerCase();
  if (signal || errorCode === "ETIMEDOUT") return "CODEX_OAUTH_SMOKE_TIMEOUT";
  if (/(usage limit|rate limit|too many requests|try again later|weekly limit|monthly limit|limit reached)/.test(combined)) return "CODEX_OAUTH_SMOKE_USAGE_LIMIT_REACHED";
  if (/(quota exceeded|insufficient quota|billing quota|credits exhausted)/.test(combined)) return "CODEX_OAUTH_SMOKE_QUOTA_EXCEEDED";
  if (/(not logged in|login required|authentication required|unauthenticated|401 unauthorized|session expired)/.test(combined)) return "CODEX_OAUTH_SMOKE_AUTH_BLOCKED";
  if (/(permission denied|operation not permitted|approval required|workspace.*not trusted|read-only)/.test(combined)) return "CODEX_OAUTH_SMOKE_PERMISSION_BLOCKED";
  if (/(unknown (command|option|flag)|unexpected argument|unrecognized option|usage: codex)/.test(combined)) return "CODEX_OAUTH_SMOKE_INVOCATION_UNSUPPORTED";
  if (expectedExists && !expectedValid) return contentDiagnostics?.normalized_content_matches ? "CODEX_OAUTH_SMOKE_CONTENT_INVALID_NORMALIZATION_ONLY" : "CODEX_OAUTH_SMOKE_CONTENT_INVALID_TEXT_MISMATCH";
  const exact = diff.created.length === 1 && diff.created[0] === OAUTH_SMOKE_FILE && diff.modified.length === 0 && diff.deleted.length === 0;
  if (exact && expectedExists && expectedValid && markerUnchanged && activeUnchanged) return "CODEX_OAUTH_SMOKE_PASS";
  if (status !== 0) return "CODEX_OAUTH_SMOKE_UNKNOWN_SAFE_FAILURE";
  if (diff.changedCount === 0 && !expectedExists) return "CODEX_OAUTH_SMOKE_NO_CHANGES";
  if (!markerUnchanged || !activeUnchanged || diff.changedCount > 0) return "CODEX_OAUTH_SMOKE_EXTRA_CHANGES";
  return "CODEX_OAUTH_SMOKE_UNKNOWN_SAFE_FAILURE";
}

export function isSafeOAuthSmokePass(payload) {
  return payload?.classification === "CODEX_OAUTH_SMOKE_PASS"
    && payload.auth_status === "signed_in"
    && payload.oauth_bridge_ready === true
    && payload.execution_count === 1
    && payload.sandbox_kind === "external_disposable_oauth_smoke"
    && payload.argv_shape === "global_approval_before_exec"
    && payload.shell_false === true
    && payload.dangerous_flags_used === false
    && payload.expected_file_created === true
    && payload.expected_content_valid === true
    && payload.normalized_content_matches === true
    && payload.created_file_count === 1
    && payload.modified_file_count === 0
    && payload.deleted_file_count === 0
    && payload.marker_unchanged === true
    && payload.active_workspace_unchanged === true
    && payload.tokens_read === false
    && payload.auth_files_read === false
    && payload.raw_prompt_persisted === false
    && payload.raw_output_persisted === false
    && payload.production_routing_enabled === false
    && payload.auto_apply === false
    && payload.auto_build === false
    && payload.auto_flash === false;
}

export function sanitizedSmokeResult(classification, diff = { created: [], modified: [], deleted: [] }, values = {}) {
  return {
    classification, provider_id: "codex_cli_oauth_bridge", auth_status: values.authStatus ?? "unknown",
    oauth_bridge_ready: values.ready === true, run_id: values.runId ?? null,
    sandbox_kind: "external_disposable_oauth_smoke", argv_shape: "global_approval_before_exec",
    shell_false: true, dangerous_flags_used: false, execution_count: values.executionCount ?? 0,
    created_file_count: diff.created.length, modified_file_count: diff.modified.length, deleted_file_count: diff.deleted.length,
    expected_file_created: values.expectedExists === true, expected_content_valid: values.expectedValid === true,
    content_validation_mode: "normalized_single_line", normalization_applied: values.contentDiagnostics?.normalization_applied ?? "none",
    actual_content_hash: values.contentDiagnostics?.actual_content_hash ?? null,
    actual_byte_count: values.contentDiagnostics?.actual_byte_count ?? null,
    expected_byte_count: values.contentDiagnostics?.expected_byte_count ?? Buffer.byteLength(OAUTH_SMOKE_CONTENT, "utf8"),
    actual_line_count: values.contentDiagnostics?.actual_line_count ?? null,
    expected_line_count: values.contentDiagnostics?.expected_line_count ?? 1,
    has_utf8_bom: values.contentDiagnostics?.has_utf8_bom === true,
    line_ending_kind: values.contentDiagnostics?.line_ending_kind ?? "none",
    leading_whitespace: values.contentDiagnostics?.leading_whitespace === true,
    trailing_whitespace: values.contentDiagnostics?.trailing_whitespace === true,
    trailing_newline: values.contentDiagnostics?.trailing_newline === true,
    normalized_content_matches: values.contentDiagnostics?.normalized_content_matches === true,
    first_difference_kind: values.contentDiagnostics?.first_difference_kind ?? "unknown",
    marker_unchanged: values.markerUnchanged !== false, active_workspace_unchanged: values.activeUnchanged !== false,
    review_created: values.reviewCreated === true, review_id: values.reviewId ?? null,
    instruction_type: "codex_oauth_bridge_smoke", expected_filename: OAUTH_SMOKE_FILE,
    expected_content_hash: OAUTH_SMOKE_HASH, prompt_variant: OAUTH_PROMPT_VARIANT, sandbox_kind_metadata: "external_disposable_oauth_smoke",
    production_routing_enabled: false, auto_apply: false, auto_build: false, auto_flash: false,
    tokens_read: false, auth_files_read: false, browser_url_read: false,
    raw_prompt_persisted: false, raw_output_persisted: false,
  };
}

function assertExternalRoot(candidate, repo, active, env) {
  if (overlaps(candidate, repo)) throw new Error("sandbox_repository_root_rejected");
  if (overlaps(candidate, active)) throw new Error("sandbox_active_workspace_rejected");
  const home = path.resolve(env.USERPROFILE || env.HOME || os.homedir());
  if (samePath(candidate, path.parse(candidate).root)) throw new Error("sandbox_sensitive_root_rejected");
  const sensitive = [home, path.join(home, "Desktop"), env.OneDrive, env.OneDriveConsumer, env.OneDriveCommercial].filter(Boolean).map((item) => path.resolve(item));
  if (sensitive.some((root) => samePath(candidate, root) || isInside(root, candidate))) throw new Error("sandbox_sensitive_root_rejected");
}
function realDirectory(value, code) { const requested = path.resolve(value); if (!fs.existsSync(requested) || fs.lstatSync(requested).isSymbolicLink() || !fs.statSync(requested).isDirectory()) throw new Error(code); const real = fs.realpathSync.native(requested); if (!samePath(real, requested)) throw new Error(code); return real; }
function rejectLinksAndSpecialEntries(root) { for (const entry of fs.readdirSync(root, { withFileTypes: true })) { const child = path.join(root, entry.name); if (entry.isSymbolicLink() || !samePath(fs.realpathSync.native(child), child)) throw new Error("sandbox_reparse_rejected"); if (entry.isDirectory()) rejectLinksAndSpecialEntries(child); else if (!entry.isFile()) throw new Error("sandbox_special_entry_rejected"); } }
function samePath(a, b) { return path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase(); }
function isInside(parent, child) { const relative = path.relative(parent, child); return Boolean(relative) && !relative.startsWith("..") && !path.isAbsolute(relative); }
function overlaps(a, b) { return samePath(a, b) || isInside(a, b) || isInside(b, a); }
