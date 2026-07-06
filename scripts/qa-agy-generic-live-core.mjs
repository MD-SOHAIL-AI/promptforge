import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const REQUIRED_LIVE_FLAGS = Object.freeze([
  "FORGEX_QA_MODE",
  "FORGEX_ENABLE_AGY_BRIDGE",
  "FORGEX_ENABLE_GENERIC_BRIDGE_API",
  "FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING",
  "FORGEX_ENABLE_AGY_GENERIC_PROVIDER",
  "FORGEX_ENABLE_AGY_GENERIC_CUTOVER",
  "FORGEX_ENABLE_AGY_TRUSTED_WORKSPACE",
]);

export const READINESS = Object.freeze({
  READY: "READY",
  NOT_INSTALLED: "BLOCKED_AGY_NOT_INSTALLED",
  NOT_AUTHENTICATED: "BLOCKED_AGY_NOT_AUTHENTICATED",
  FLAGS_MISSING: "BLOCKED_FLAGS_MISSING",
  WORKSPACE_MISSING: "BLOCKED_WORKSPACE_MISSING",
  TRUSTED_WORKSPACE_NOT_PREPARED: "BLOCKED_TRUSTED_WORKSPACE_NOT_PREPARED",
  TRUSTED_WORKSPACE_NOT_ATTESTED: "BLOCKED_TRUSTED_WORKSPACE_NOT_ATTESTED",
  UNKNOWN: "BLOCKED_UNKNOWN_SAFE_REASON",
});

const IGNORED_BASELINE_DIRECTORIES = new Set([".git", ".pio", ".promptforge", "build", "dist", "node_modules"]);
const FORBIDDEN_PUBLIC_KEYS = new Set([
  "instruction",
  "prompt",
  "stdout",
  "stderr",
  "stdout_preview",
  "stderr_preview",
  "command",
  "arguments",
  "executable",
  "executable_path",
  "environment",
  "workspace_root",
  "sandbox_root",
  "working_directory",
  "patch",
  "file_content",
]);

export function missingLiveFlags(env) {
  return REQUIRED_LIVE_FLAGS.filter((name) => env[name] !== "1");
}

export function flagSupportMissing(sourceText) {
  return REQUIRED_LIVE_FLAGS.filter((name) => !sourceText.includes(name));
}

export function selectReadinessStatus({
  unsupportedFlags = [],
  disabledFlags = [],
  workspaceOk,
  workspaceReason,
  trustedWorkspacePrepared = true,
  trustedWorkspaceReason,
  trustAttested = true,
  installed,
  authenticated,
  version,
  backendReady,
  backendReason,
  agentUiRequired = false,
  agentUiReady = false,
}) {
  if (unsupportedFlags.length || disabledFlags.length) {
    return { status: READINESS.FLAGS_MISSING, reason: unsupportedFlags.length ? "flag_support_missing" : "live_flags_not_enabled" };
  }
  if (!workspaceOk) return { status: READINESS.WORKSPACE_MISSING, reason: workspaceReason || "workspace_guard_failed" };
  if (!trustedWorkspacePrepared) return { status: READINESS.TRUSTED_WORKSPACE_NOT_PREPARED, reason: trustedWorkspaceReason || "trusted_workspace_not_prepared" };
  if (!installed) return { status: READINESS.NOT_INSTALLED, reason: "agy_not_installed" };
  if (!authenticated) return { status: READINESS.NOT_AUTHENTICATED, reason: "agy_authentication_not_attested" };
  if (!version) return { status: READINESS.UNKNOWN, reason: "agy_version_not_safely_detectable" };
  if (!trustAttested) return { status: READINESS.TRUSTED_WORKSPACE_NOT_ATTESTED, reason: "trusted_workspace_operator_attestation_required" };
  if (!backendReady) return { status: READINESS.UNKNOWN, reason: backendReason || "backend_unready" };
  if (agentUiRequired && !agentUiReady) return { status: READINESS.UNKNOWN, reason: "agent_ui_unready" };
  return { status: READINESS.READY, reason: "ready" };
}

export function guardThrowawayWorkspace(repositoryRoot, workspaceRoot, markerName = "QA_NOT_REAL_PROJECT.txt") {
  const repo = fs.realpathSync.native(repositoryRoot);
  const requested = path.resolve(workspaceRoot);
  if (!fs.existsSync(requested)) return { ok: false, reason: "workspace_missing" };
  const workspace = fs.realpathSync.native(requested);
  const expectedRelative = path.relative(repo, workspace).replaceAll("\\", "/");
  if (expectedRelative !== "workspace/forgex-apply-test") return { ok: false, reason: "workspace_not_allowlisted" };
  if (workspace === repo || workspace === path.parse(workspace).root) return { ok: false, reason: "workspace_root_rejected" };
  if (fs.lstatSync(requested).isSymbolicLink() || requested !== workspace) return { ok: false, reason: "workspace_symlink_rejected" };

  const home = path.resolve(os.homedir());
  const forbiddenRoots = new Set([
    home,
    path.join(home, "Desktop"),
    process.env.OneDrive ? path.resolve(process.env.OneDrive) : "",
    process.env.OneDriveConsumer ? path.resolve(process.env.OneDriveConsumer) : "",
    process.env.OneDriveCommercial ? path.resolve(process.env.OneDriveCommercial) : "",
  ].filter(Boolean).map((item) => item.toLowerCase()));
  if (forbiddenRoots.has(workspace.toLowerCase())) return { ok: false, reason: "workspace_sensitive_root_rejected" };

  const marker = path.join(workspace, markerName);
  if (!fs.existsSync(marker)) return { ok: false, reason: "marker_missing" };
  const markerStat = fs.lstatSync(marker);
  if (!markerStat.isFile() || markerStat.isSymbolicLink()) return { ok: false, reason: "marker_invalid" };
  return { ok: true, repositoryRoot: repo, workspaceRoot: workspace };
}

export function locateAgy(spawnSync, platform = process.platform, env = process.env) {
  const locator = platform === "win32" ? "where.exe" : "which";
  for (const command of ["agy", "antigravity"]) {
    const result = spawnSync(locator, [command], {
      encoding: "utf8",
      windowsHide: true,
      shell: false,
      timeout: 5_000,
    });
    if (result.status !== 0) continue;
    const resolved = String(result.stdout || "").split(/\r?\n/).map((item) => item.trim()).find(Boolean);
    if (resolved) return { installed: true, command, resolvedPath: path.resolve(resolved) };
  }
  for (const command of ["agy", "antigravity"]) {
    const resolved = resolveCommandOnPath(command, env, platform);
    if (resolved) return { installed: true, command, resolvedPath: resolved };
  }
  return { installed: false, command: null, resolvedPath: null };
}

export function resolveCommandOnPath(command, env = process.env, platform = process.platform) {
  const delimiter = platform === "win32" ? ";" : ":";
  const extensions = platform === "win32"
    ? String(env.PATHEXT || ".EXE;.CMD;.BAT").split(";").filter(Boolean)
    : [""];
  for (const directory of String(env.PATH || "").split(delimiter).filter(Boolean)) {
    const cleanDirectory = directory.replace(/^"|"$/g, "");
    for (const extension of extensions) {
      for (const suffix of new Set([extension, extension.toLowerCase(), extension.toUpperCase()])) {
        const candidate = path.join(cleanDirectory, `${command}${suffix}`);
        try {
          if (fs.statSync(candidate).isFile()) return path.resolve(candidate);
        } catch {}
      }
    }
  }
  return null;
}

export function detectInstalledVersion(resolvedPath, spawnSync, platform = process.platform) {
  if (!resolvedPath) return null;
  for (const candidate of packageJsonCandidates(resolvedPath)) {
    try {
      const value = JSON.parse(fs.readFileSync(candidate, "utf8"));
      if (typeof value.version === "string" && /^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/.test(value.version)) return value.version;
    } catch {}
  }
  if (platform === "win32" && path.extname(resolvedPath).toLowerCase() === ".exe") {
    const result = spawnSync(
      "powershell.exe",
      ["-NoProfile", "-Command", "(Get-Item -LiteralPath $args[0]).VersionInfo.ProductVersion", resolvedPath],
      { encoding: "utf8", windowsHide: true, shell: false, timeout: 5_000 },
    );
    const version = String(result.stdout || "").trim();
    if (result.status === 0 && /^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/.test(version)) return version;
  }
  const result = spawnSync(resolvedPath, ["--version"], {
    encoding: "utf8",
    windowsHide: true,
    shell: false,
    timeout: 5_000,
  });
  const version = String(result.stdout || "").trim();
  if (result.status === 0 && /^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/.test(version)) return version;
  return null;
}

function packageJsonCandidates(resolvedPath) {
  const start = path.dirname(resolvedPath);
  const values = [];
  let current = start;
  for (let depth = 0; depth < 6; depth += 1) {
    values.push(path.join(current, "package.json"));
    values.push(path.join(current, "node_modules", "agy", "package.json"));
    values.push(path.join(current, "node_modules", "antigravity", "package.json"));
    values.push(path.join(current, "node_modules", "@google", "agy", "package.json"));
    values.push(path.join(current, "node_modules", "@google", "antigravity", "package.json"));
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return values;
}

export function snapshotWorkspace(workspaceRoot) {
  const entries = [];
  walk(workspaceRoot, "", entries);
  entries.sort((left, right) => left.relative_path.localeCompare(right.relative_path));
  return entries;
}

function walk(root, relative, entries) {
  const directory = path.join(root, relative);
  for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
    if (item.isSymbolicLink()) throw new Error("workspace_symlink_rejected");
    const childRelative = relative ? `${relative}/${item.name}` : item.name;
    if (item.isDirectory()) {
      if (!IGNORED_BASELINE_DIRECTORIES.has(item.name)) walk(root, childRelative, entries);
      continue;
    }
    if (!item.isFile()) throw new Error("workspace_entry_rejected");
    const bytes = fs.readFileSync(path.join(root, ...childRelative.split("/")));
    entries.push({ relative_path: childRelative, sha256: crypto.createHash("sha256").update(bytes).digest("hex") });
  }
}

export function baselineDigest(entries) {
  return crypto.createHash("sha256").update(JSON.stringify(entries)).digest("hex");
}

export function compareBaselines(before, after) {
  return JSON.stringify(before) === JSON.stringify(after);
}

export function assertSanitizedPublicPayload(value) {
  inspectPublicValue(value);
  const serialized = JSON.stringify(value).toLowerCase();
  for (const marker of ["authorization", "bearer ", "cookie", "api_key", "password", "token="]) {
    if (serialized.includes(marker)) throw new Error("unsafe_public_payload");
  }
  if (/[a-z]:\\/i.test(serialized) || serialized.includes("/users/") || serialized.includes("\\users\\")) {
    throw new Error("unsafe_public_path");
  }
  return true;
}

function inspectPublicValue(value) {
  if (Array.isArray(value)) {
    for (const item of value) inspectPublicValue(item);
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, item] of Object.entries(value)) {
    if (FORBIDDEN_PUBLIC_KEYS.has(key.toLowerCase())) throw new Error("unsafe_public_key");
    inspectPublicValue(item);
  }
}

export function orderedEvents(events) {
  if (!Array.isArray(events) || events.length === 0) return false;
  const sequences = events.map((event) => event.sequence);
  if (new Set(sequences).size !== sequences.length) return false;
  return sequences.every((sequence, index) => index === 0 || sequence > sequences[index - 1]);
}

export function terminalEventCount(events) {
  const terminal = new Set(["completed", "failed", "cancelled", "blocked", "timed_out", "interrupted"]);
  return events.filter((event) => event.status && terminal.has(event.status)).length;
}

export function fastCompletionClassification(status, requestedTerminal) {
  if (status === requestedTerminal) return "PASS";
  if (status === "completed") return "BLOCKED_FAST_COMPLETION";
  return "FAIL";
}
