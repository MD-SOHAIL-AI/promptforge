import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { buildCodexSafeUserEnv, buildInheritedMinusSecretsEnv, buildMinimalSafeEnv } from "./codex-safe-user-env.mjs";

const NEGATIVE = /\b(?:not logged in|login required|signed out|not authenticated|authentication required|unauthenticated)\b/i;
const LOGGED_IN = /\blogged in\b/i;
const CHATGPT = /\bchatgpt\b/i;
const AUTHENTICATED = /\bauthenticated\b/i;

export function parseCodexStatus(output, exitCode = 0) {
  const normalized = String(output || "").replace(/\s+/g, " ").trim().slice(0, 1000);
  const flags = {
    contains_logged_in_phrase: LOGGED_IN.test(normalized),
    contains_chatgpt_phrase: CHATGPT.test(normalized),
    contains_not_logged_in_phrase: /\bnot logged in\b/i.test(normalized),
    contains_login_required_phrase: /\blogin required\b/i.test(normalized),
  };
  if (NEGATIVE.test(normalized)) return { authStatus: "signed_out", flags };
  if (exitCode === 0 && (flags.contains_logged_in_phrase || flags.contains_chatgpt_phrase || AUTHENTICATED.test(normalized))) return { authStatus: "signed_in", flags };
  return { authStatus: "unknown", flags };
}

export function launcherKind(candidate) {
  const value = String(candidate || "").toLowerCase();
  if (value.includes("windowsapps")) return "windowsapps";
  if (value.includes("node_modules") || /[\\/]npm[\\/]codex\.(?:cmd|ps1|exe)$/.test(value)) return "npm_global_shim";
  if (value.endsWith(".exe")) return "exe";
  if (value.endsWith(".cmd") || value.endsWith(".bat")) return "cmd_shim";
  if (value.endsWith(".ps1")) return "ps1_shim";
  return "unknown";
}

export function resolveCodexExecutables(env = process.env, platform = process.platform, spawn = spawnSync) {
  const whereCommand = env.ComSpec || env.COMSPEC || "cmd.exe";
  const discovery = platform === "win32"
    ? spawn(whereCommand, ["/d", "/s", "/c", "where codex"], { encoding: "utf8", input: "", shell: false, windowsHide: true, timeout: 5000, env })
    : spawn("sh", ["-c", "command -v codex"], { encoding: "utf8", input: "", shell: false, timeout: 5000, env });
  let discovered = String(discovery.stdout || "");
  if (!discovered.trim() && platform === "win32") {
    const powershell = env.SystemRoot ? path.join(env.SystemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe") : "powershell.exe";
    const fallback = spawn(powershell, ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "Get-Command codex -All | Select-Object -ExpandProperty Source"], { encoding: "utf8", input: "", shell: false, windowsHide: true, timeout: 5000, env });
    discovered = String(fallback.stdout || "");
  }
  const candidates = discovered.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  const preference = (item) => /\.cmd$/i.test(item) ? 0 : /\.exe$/i.test(item) ? 1 : /\.ps1$/i.test(item) ? 2 : 3;
  const unique = [...new Map(candidates.map((item) => [item.toLowerCase(), item])).values()].sort((a, b) => preference(a) - preference(b));
  const primary = unique[0] || null;
  return {
    candidates: unique,
    safe: {
      codex_resolution_count: unique.length,
      primary_launcher_kind: launcherKind(primary),
      primary_launcher_path_hash: primary ? crypto.createHash("sha256").update(path.normalize(primary).toLowerCase()).digest("hex") : null,
      multiple_launchers_detected: unique.length > 1,
    },
  };
}

function category(result) {
  if (result?.error?.code === "ETIMEDOUT" || result?.signal) return "timeout";
  if (typeof result?.status !== "number") return "failed";
  return result.status === 0 ? "success" : "nonzero";
}

function invoke(command, args, env, cwd, spawn = spawnSync) {
  return spawn(command, args, { cwd, env, encoding: "utf8", input: "", shell: false, windowsHide: true, timeout: 5000, maxBuffer: 64 * 1024 });
}

function invokeResolved(candidate, args, env, cwd, spawn = spawnSync) {
  const kind = launcherKind(candidate);
  if (/\.(?:cmd|bat)$/i.test(candidate)) {
    const comspec = env.ComSpec || env.COMSPEC || "cmd.exe";
    return invoke(comspec, ["/d", "/s", "/c", `call "${candidate}" ${args.join(" ")}`], env, cwd, spawn);
  }
  if (/\.ps1$/i.test(candidate)) {
    const powershell = env.SystemRoot ? path.join(env.SystemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe") : "powershell.exe";
    return invoke(powershell, ["-NoLogo", "-NoProfile", "-NonInteractive", "-File", candidate, ...args], env, cwd, spawn);
  }
  return invoke(candidate, args, env, cwd, spawn);
}

function safeRunnerResult(name, kind, statusResult, versionResult, envProfile) {
  const parsed = parseCodexStatus(`${statusResult?.stdout || ""} ${statusResult?.stderr || ""}`, statusResult?.status);
  const version = String(versionResult?.stdout || versionResult?.stderr || "").replace(/\s+/g, " ").trim();
  return {
    runner_name: name, runner_kind: kind,
    codex_found: category(versionResult) !== "failed",
    codex_version_detected: /^codex(?:-cli)?\s+\d+\.\d+\.\d+/i.test(version),
    auth_status: parsed.authStatus, exit_code_category: category(statusResult),
    parser_flags: parsed.flags, env_profile: envProfile, cwd_kind: "neutral_temp",
    raw_output_persisted: false, auth_files_read: false, tokens_read: false,
  };
}

export function classifySessionParity(runners) {
  const byName = Object.fromEntries(runners.map((item) => [item.runner_name, item.auth_status]));
  const values = runners.map((item) => item.auth_status);
  if (values.every((value) => value === "signed_in")) return "CODEX_SESSION_PARITY_PASS";
  if (values.every((value) => value !== "signed_in") && values.some((value) => value === "signed_out")) return "CODEX_SESSION_PARITY_ALL_SIGNED_OUT";
  if (byName.windows_cmd_parity_runner === "signed_in" && [byName.shared_aligned_runner, byName.direct_codex_runner, byName.resolved_executable_runner].every((value) => value !== "signed_in")) return "CODEX_SESSION_PARITY_CMD_ONLY_SIGNED_IN";
  if (byName.direct_codex_runner === "signed_in" && byName.windows_cmd_parity_runner !== "signed_in") return "CODEX_SESSION_PARITY_DIRECT_ONLY_SIGNED_IN";
  if (values.some((value) => value === "signed_in") && values.some((value) => value !== "signed_in")) return "CODEX_SESSION_PARITY_MISMATCH";
  return "CODEX_SESSION_PARITY_UNKNOWN";
}

export function runSessionParity({ sharedStatus, env = process.env, platform = process.platform, spawn = spawnSync, tempRoot = os.tmpdir() } = {}) {
  const safeEnv = buildCodexSafeUserEnv(env);
  const resolution = resolveCodexExecutables(safeEnv, platform, spawn);
  const cwd = fs.mkdtempSync(path.join(tempRoot, "forgex-codex-parity-"));
  let primary = resolution.candidates[0] || null;
  let resolvedVersion = null;
  for (const candidate of resolution.candidates) {
    const attempt = invokeResolved(candidate, ["--version"], safeEnv, cwd, spawn);
    const versionText = String(attempt?.stdout || attempt?.stderr || "").replace(/\s+/g, " ").trim();
    if (attempt?.status === 0 && /^codex(?:-cli)?\s+\d+\.\d+\.\d+/i.test(versionText)) { primary = candidate; resolvedVersion = attempt; break; }
  }
  if (primary) {
    resolution.safe.primary_launcher_kind = launcherKind(primary);
    resolution.safe.primary_launcher_path_hash = crypto.createHash("sha256").update(path.normalize(primary).toLowerCase()).digest("hex");
  }
  const directVersion = invoke("codex", ["--version"], safeEnv, cwd, spawn);
  const directStatus = invoke("codex", ["login", "status"], safeEnv, cwd, spawn);
  resolvedVersion = resolvedVersion || (primary ? invokeResolved(primary, ["--version"], safeEnv, cwd, spawn) : null);
  const resolvedStatus = primary ? invokeResolved(primary, ["login", "status"], safeEnv, cwd, spawn) : null;
  const comspec = safeEnv.ComSpec || safeEnv.COMSPEC || "cmd.exe";
  const cmdVersion = platform === "win32" ? invoke(comspec, ["/d", "/s", "/c", "codex --version"], safeEnv, cwd, spawn) : directVersion;
  const cmdStatus = platform === "win32" ? invoke(comspec, ["/d", "/s", "/c", "codex login status"], safeEnv, cwd, spawn) : directStatus;
  const shared = sharedStatus?.();
  const emptyFlags = { contains_logged_in_phrase: false, contains_chatgpt_phrase: false, contains_not_logged_in_phrase: false, contains_login_required_phrase: false };
  const runners = [
    { runner_name: "shared_aligned_runner", runner_kind: shared?.status_runner || "shared_aligned", codex_found: shared?.codex_installed === true, codex_version_detected: Boolean(shared?.codex_version), auth_status: shared?.auth_status || "unknown", exit_code_category: shared?.exit_code_category || "failed", parser_flags: shared?.parser_flags || emptyFlags, env_profile: shared?.env_profile || shared?.safe_env_profile || "codex_safe_user_env", cwd_kind: "neutral_temp", raw_output_persisted: false, auth_files_read: false, tokens_read: false },
    safeRunnerResult("direct_codex_runner", "direct_argv", directStatus, directVersion, "codex_safe_user_env"),
    safeRunnerResult("resolved_executable_runner", primary ? launcherKind(primary) : "unknown", resolvedStatus, resolvedVersion, "codex_safe_user_env"),
    safeRunnerResult("windows_cmd_parity_runner", "cmd_fixed_status_only", cmdStatus, cmdVersion, "codex_safe_user_env"),
  ];
  const inherited = buildInheritedMinusSecretsEnv(env);
  const profiles = [
    ["minimal_safe_env", buildMinimalSafeEnv(env), 0],
    ["codex_safe_user_env", safeEnv, 0],
    ["inherited_minus_secrets_env", inherited.env, inherited.removed],
  ].map(([name, profileEnv, removed]) => {
    const profileComspec = profileEnv.ComSpec || profileEnv.COMSPEC || "cmd.exe";
    const result = platform === "win32"
      ? invoke(profileComspec, ["/d", "/s", "/c", "codex login status"], profileEnv, cwd, spawn)
      : primary ? invokeResolved(primary, ["login", "status"], profileEnv, cwd, spawn) : invoke("codex", ["login", "status"], profileEnv, cwd, spawn);
    return { env_profile: name, env_key_count: Object.keys(profileEnv).length, status_result: parseCodexStatus(`${result?.stdout || ""} ${result?.stderr || ""}`, result?.status).authStatus, secret_like_keys_removed_count: removed, raw_output_persisted: false };
  });
  try { fs.rmSync(cwd, { recursive: true, force: true }); } catch {}
  return { classification: classifySessionParity(runners), runners, resolution: resolution.safe, env_profiles: profiles, raw_output_persisted: false, auth_files_read: false, tokens_read: false, production_routing_enabled: false };
}
