import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";

const root = process.cwd();
const backendPort = process.env.FORGEX_BACKEND_PORT ?? process.env.PROMPTFORGE_BACKEND_PORT ?? "8000";
const frontendPort = process.env.FORGEX_FRONTEND_PORT ?? "3000";
const dataRoot = process.env.PROMPTFORGE_DATA_ROOT
  ?? process.env.PROMPTFORGE_RUNTIME_ROOT
  ?? process.env.PROMPTFORGE_ROOT
  ?? path.join(process.env.LOCALAPPDATA ?? os.tmpdir(), "ForgeX", "desktop", "backend-data");
const backendUrl = `http://localhost:${backendPort}`;
const frontendUrl = (process.env.FORGEX_FRONTEND_URL ?? `http://localhost:${frontendPort}`).replace(/\/$/, "");
const healthUrl = `${backendUrl}/health`;
const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
const smokeMode = process.argv.includes("--smoke") || process.env.FORGEX_QA_SMOKE === "1";
const qaEnv = {
  ...process.env,
  FORGEX_ENABLE_AGY_BRIDGE: process.env.FORGEX_ENABLE_AGY_BRIDGE ?? "1",
  FORGEX_ENABLE_ROLLBACK_RESTORE: process.env.FORGEX_ENABLE_ROLLBACK_RESTORE ?? "1",
  FORGEX_ENABLE_PATCH_APPLY: process.env.FORGEX_ENABLE_PATCH_APPLY ?? "1",
  FORGEX_QA_MODE: process.env.FORGEX_QA_MODE ?? "1",
  FORGEX_BACKEND_PORT: backendPort,
  PROMPTFORGE_BACKEND_PORT: backendPort,
  PROMPTFORGE_BACKEND_URL: backendUrl,
  PROMPTFORGE_DATA_ROOT: dataRoot,
  FORGEX_FRONTEND_URL: frontendUrl,
};
const children = [];

main().catch((error) => {
  console.error(`[qa] Launch failed: ${error instanceof Error ? error.message : String(error)}`);
  cleanup();
  process.exit(1);
});

async function main() {
  console.log("[qa] ForgeX desktop QA launch starting");
  console.log(`[qa] Backend port: ${backendPort}`);
  console.log(`[qa] Frontend port: ${frontendPort}`);
  console.log(`[qa] Feature flags: agy=${flag("FORGEX_ENABLE_AGY_BRIDGE")} rollback_restore=${flag("FORGEX_ENABLE_ROLLBACK_RESTORE")} patch_apply=${flag("FORGEX_ENABLE_PATCH_APPLY")} qa_mode=${flag("FORGEX_QA_MODE")}`);

  if (await probe(healthUrl)) {
    console.log(`[qa] Backend ready: ${healthUrl}`);
  } else {
    const backend = spawnChild("backend", npmCommand, ["run", "dev:backend"]);
    await waitForUrl(healthUrl, 60_000, "Backend ready");
  }

  if (await probe(frontendUrl)) {
    console.log(`[qa] Frontend ready: ${frontendUrl}`);
  } else {
    spawnChild("frontend", npmCommand, ["--prefix", "frontend", "run", "dev"]);
    await waitForUrl(frontendUrl, 90_000, "Frontend ready");
  }

  await runCommand("electron-build", npmCommand, ["run", "build:electron"]);
  const electron = spawnChild("electron", electronExecutable(), ["."], { shell: process.platform === "win32" && electronExecutable().endsWith(".cmd") });
  console.log(`[qa] Electron process PID: ${electron.pid ?? "unknown"}`);
  console.log(`[qa] Electron renderer URL: ${frontendUrl}`);
  console.log("[qa] Electron launched");

  writeQaSession(true);
  console.log("[qa] QA session written: .promptforge/state/qa-session.json");
  console.log(`Backend: ${healthUrl}`);
  console.log(`Frontend URL: ${frontendUrl}`);
  console.log("Electron: launched");
  console.log("[qa] Use the Electron window for QA. If it cannot be captured, open the frontend URL and record Electron as BLOCKED.");

  if (smokeMode) {
    console.log("[qa] Smoke mode complete; stopping QA child processes.");
    cleanup();
    return;
  }

  await waitForExit();
}

function spawnChild(label, command, args, options = {}) {
  const shell = options.shell ?? (process.platform === "win32" && command.toLowerCase().endsWith(".cmd"));
  const child = spawn(command, args, {
    cwd: root,
    env: qaEnv,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: label !== "electron",
    ...options,
    shell,
  });
  children.push(child);
  child.stdout?.on("data", (chunk) => prefixLines(label, chunk));
  child.stderr?.on("data", (chunk) => prefixLines(label, chunk));
  child.on("exit", (code, signal) => {
    console.log(`[qa] ${label} exited code=${code ?? "null"} signal=${signal ?? "null"}`);
  });
  return child;
}

function prefixLines(label, chunk) {
  String(chunk).split(/\r?\n/).filter(Boolean).forEach((line) => {
    console.log(`[${label}] ${line}`);
  });
}

function runCommand(label, command, args) {
  return new Promise((resolve, reject) => {
    const child = spawnChild(label, command, args);
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${label} failed with exit code ${code}`));
    });
  });
}

async function waitForUrl(url, timeoutMs, readyLabel) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await probe(url)) {
      console.log(`[qa] ${readyLabel}: ${url}`);
      return;
    }
    await delay(500);
  }
  throw new Error(`Timed out waiting for ${url}`);
}

function probe(url) {
  return new Promise((resolve) => {
    const request = http.request(url, { method: "GET", timeout: 2_000 }, (response) => {
      response.resume();
      resolve((response.statusCode ?? 0) >= 200 && (response.statusCode ?? 0) < 500);
    });
    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
    request.on("error", () => resolve(false));
    request.end();
  });
}

function writeQaSession(electronStarted) {
  const stateDir = path.join(root, ".promptforge", "state");
  fs.mkdirSync(stateDir, { recursive: true });
  const session = {
    started_at: new Date().toISOString(),
    backend_url: backendUrl,
    frontend_url: frontendUrl,
    electron_started: electronStarted,
    feature_flags: {
      agy_bridge: qaEnv.FORGEX_ENABLE_AGY_BRIDGE === "1",
      rollback_restore: qaEnv.FORGEX_ENABLE_ROLLBACK_RESTORE === "1",
      patch_apply: qaEnv.FORGEX_ENABLE_PATCH_APPLY === "1",
    },
  };
  fs.writeFileSync(path.join(stateDir, "qa-session.json"), `${JSON.stringify(session, null, 2)}\n`, "utf-8");
}

function electronExecutable() {
  const direct = process.platform === "win32"
    ? path.join(root, "node_modules", "electron", "dist", "electron.exe")
    : path.join(root, "node_modules", "electron", "dist", "electron");
  if (fs.existsSync(direct)) return direct;
  return path.join(root, "node_modules", ".bin", process.platform === "win32" ? "electron.cmd" : "electron");
}

function flag(name) {
  return qaEnv[name] === "1" ? "enabled" : "disabled";
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function waitForExit() {
  return new Promise((resolve) => {
    for (const signal of ["SIGINT", "SIGTERM"]) {
      process.on(signal, () => {
        cleanup();
        resolve();
      });
    }
  });
}

function cleanup() {
  for (const child of children) {
    if (!child.pid || child.killed) continue;
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      child.kill();
    }
  }
}
