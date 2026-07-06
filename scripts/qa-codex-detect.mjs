import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";

const SAFE_VERSION = /^codex(?:-cli)?\s+\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/;
const STATUS_HELP = /\bstatus\b\s+Show login status/i;

function resolveCodexLauncher(env = process.env, platform = process.platform) {
  if (platform !== "win32") return { command: "codex", prefixArgs: [] };
  const pathValue = env.PATH || env.Path || "";
  for (const entry of pathValue.split(path.delimiter).filter(Boolean)) {
    const native = path.join(entry, "codex.exe");
    if (fs.existsSync(native) && fs.statSync(native).isFile()) return { command: native, prefixArgs: [] };
    const npmEntry = path.join(entry, "node_modules", "@openai", "codex", "bin", "codex.js");
    if (fs.existsSync(npmEntry) && fs.statSync(npmEntry).isFile()) {
      return { command: process.execPath, prefixArgs: [npmEntry] };
    }
  }
  return null;
}

function run(launcher, args) {
  return spawnSync(launcher.command, [...launcher.prefixArgs, ...args], {
    cwd: os.tmpdir(),
    encoding: "utf8",
    input: "",
    shell: false,
    windowsHide: true,
    timeout: 5_000,
    maxBuffer: 64 * 1024,
  });
}

function normalizedOutput(result) {
  return `${String(result?.stdout || "")} ${String(result?.stderr || "")}`.replace(/\s+/g, " ").trim();
}

const launcher = resolveCodexLauncher();
let version = null;
let detectionClassification = "CODEX_CLI_NOT_FOUND";
let authStatus = "not_installed";
let authClassification = "CODEX_OAUTH_NOT_INSTALLED";
let safeStatusChecked = false;

if (launcher) {
  const versionResult = run(launcher, ["--version"]);
  const candidateVersion = normalizedOutput(versionResult);
  if (versionResult.status !== 0) {
    detectionClassification = "CODEX_CLI_DETECTION_FAILED";
    authStatus = "error";
    authClassification = "CODEX_OAUTH_STATUS_FAILED";
  } else if (!SAFE_VERSION.test(candidateVersion)) {
    detectionClassification = "CODEX_CLI_VERSION_UNSUPPORTED";
    authStatus = "unknown";
    authClassification = "CODEX_OAUTH_STATUS_UNKNOWN";
  } else {
    version = candidateVersion;
    const helpResult = run(launcher, ["login", "--help"]);
    if (helpResult.status !== 0 || !STATUS_HELP.test(normalizedOutput(helpResult))) {
      detectionClassification = "CODEX_CLI_VERSION_UNSUPPORTED";
      authStatus = "unknown";
      authClassification = "CODEX_OAUTH_STATUS_UNKNOWN";
    } else {
      detectionClassification = "CODEX_CLI_DETECTED";
      safeStatusChecked = true;
      const statusResult = run(launcher, ["login", "status"]);
      const status = normalizedOutput(statusResult).toLowerCase();
      if (status === "logged in using chatgpt") {
        authStatus = "authenticated";
        authClassification = "CODEX_OAUTH_AUTHENTICATED";
      } else if (status === "not logged in") {
        authStatus = "unauthenticated";
        authClassification = "CODEX_OAUTH_NOT_AUTHENTICATED";
      } else if (statusResult.status !== 0) {
        authStatus = "error";
        authClassification = "CODEX_OAUTH_STATUS_FAILED";
      } else {
        // API-key, agent-identity, and unrecognized modes are deliberately not
        // treated as ChatGPT OAuth. Raw status output is never returned.
        authStatus = "unknown";
        authClassification = "CODEX_OAUTH_STATUS_UNKNOWN";
      }
    }
  }
}

const smokeReady = detectionClassification === "CODEX_CLI_DETECTED" && authStatus === "authenticated";
const result = {
  provider_id: "codex_cli_oauth_bridge",
  provider_name: "Codex CLI OAuth Bridge",
  provider_kind: "local_cli",
  auth_mode: "official_codex_cli_oauth",
  execution_mode: "status_and_login_helper",
  workspace_mode: "none_for_login",
  production_eligible: false,
  qa_only: true,
  installed: Boolean(launcher),
  executable_found: Boolean(launcher),
  version_detected: version !== null,
  version,
  detection_classification: detectionClassification,
  auth_status: authStatus,
  auth_classification: authClassification,
  safe_status_checked: safeStatusChecked,
  login_command: "codex login",
  smoke_ready: smokeReady,
  smoke_classification: smokeReady ? "CODEX_STANDALONE_SMOKE_READY" : "CODEX_STANDALONE_SMOKE_BLOCKED",
  active_workspace_execution: false,
  editing_enabled: false,
};

console.log(JSON.stringify(result));
process.exit(detectionClassification === "CODEX_CLI_DETECTED" ? 0 : 1);
