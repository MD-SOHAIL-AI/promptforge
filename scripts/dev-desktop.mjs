import { spawn, spawnSync } from "node:child_process";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendPort = parsePort(process.env.FORGEX_FRONTEND_PORT, 3000, "FORGEX_FRONTEND_PORT");
const backendPort = parsePort(process.env.FORGEX_BACKEND_PORT ?? process.env.PROMPTFORGE_BACKEND_PORT, 8000, "FORGEX_BACKEND_PORT");
const frontendUrl = (process.env.FORGEX_FRONTEND_URL ?? `http://127.0.0.1:${frontendPort}`).replace(/\/$/, "");
const backendUrl = `http://127.0.0.1:${backendPort}`;
const websocketUrl = `ws://127.0.0.1:${backendPort}`;
const npmExecutable = process.platform === "win32" ? "npm.cmd" : "npm";
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const frontendCwd = path.join(repoRoot, "frontend");
const frontendMarkers = ["ForgeX Embedded Engineering Environment", "ForgeX Nexus", "ForgeX"];
const children = new Set();
let ownedFrontend = null;
let shuttingDown = false;

export async function runDesktopLauncher() {
  warnIfPlatformIOMissing();
  console.log("[forgex-desktop] Starting frontend...");
  await ensureFrontend();
  console.log("[forgex-desktop] Launching Electron...");
  await runElectron();
}

function warnIfPlatformIOMissing() {
  const resolved = resolvePlatformIOExecutable();
  if (!resolved) {
    console.warn("[forgex-desktop] PlatformIO not found. Firmware builds will fail until you run: npm run setup:platformio");
    return;
  }
  const probe = spawnSync(resolved, ["--version"], { cwd: repoRoot, encoding: "utf8", windowsHide: true });
  if (probe.error || probe.status !== 0) {
    console.warn(`[forgex-desktop] PlatformIO found at ${resolved}, but readiness check failed. Run: npm run setup:platformio`);
    return;
  }
  const version = `${probe.stdout || probe.stderr}`.trim().split(/\r?\n/)[0] || "version unknown";
  console.log(`[forgex-desktop] PlatformIO ready: ${version}`);
}

function resolvePlatformIOExecutable() {
  for (const key of ["FORGEX_PLATFORMIO_EXECUTABLE", "PLATFORMIO_EXECUTABLE"]) {
    const value = process.env[key]?.trim();
    if (value) return value;
  }
  for (const candidate of [
    path.join(repoRoot, ".venv", "Scripts", "pio.exe"),
    path.join(repoRoot, ".venv", "Scripts", "pio"),
    path.join(repoRoot, ".venv", "bin", "pio"),
    path.join(repoRoot, ".venv", "bin", "pio.exe"),
  ]) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return findOnPath(process.platform === "win32" ? ["pio.cmd", "pio.exe", "platformio.cmd", "platformio.exe"] : ["pio", "platformio"]);
}

function findOnPath(commands) {
  const paths = (process.env.PATH || "").split(path.delimiter).filter(Boolean);
  for (const directory of paths) {
    for (const command of commands) {
      const candidate = path.join(directory, command);
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  return null;
}

async function ensureFrontend() {
  const initial = await probeFrontend(frontendUrl);
  if (initial.reachable) {
    if (!initial.forgeX) {
      throw new Error(`Port ${frontendPort} is already serving a non-ForgeX page. Run "npm run dev:cleanup" and free port ${frontendPort}, then retry.`);
    }
    console.log(`[forgex-desktop] Reusing ForgeX frontend at ${frontendUrl}`);
    return;
  }

  ownedFrontend = spawnCommand(
    npmExecutable,
    ["run", "dev", "--", "--hostname", "127.0.0.1", "--port", String(frontendPort)],
    {
      cwd: frontendCwd,
      env: frontendEnv(),
      stdio: "inherit",
      windowsHide: true,
    },
  );
  trackChild(ownedFrontend);
  await waitForFrontend();
}

async function waitForFrontend() {
  const deadline = Date.now() + 240_000;
  let lastProbe = { reachable: false, forgeX: false, error: "not checked" };
  while (Date.now() < deadline) {
    if (ownedFrontend?.exitCode !== null) {
      throw new Error(`Frontend exited before it became ready with code ${ownedFrontend.exitCode}.`);
    }
    lastProbe = await probeFrontend(frontendUrl);
    if (lastProbe.reachable && lastProbe.forgeX) {
      console.log(`[forgex-desktop] Frontend ready at ${frontendUrl}`);
      return;
    }
    await delay(300);
  }
  throw new Error(`Timed out waiting for ForgeX frontend at ${frontendUrl}: ${lastProbe.error ?? "not ready"}`);
}

async function runElectron() {
  const electron = spawnCommand(npmExecutable, ["run", "dev:electron"], {
    env: {
      ...frontendEnv(),
      FORGEX_FRONTEND_PORT: String(frontendPort),
      FORGEX_FRONTEND_URL: frontendUrl,
    },
    stdio: "inherit",
    windowsHide: true,
  });
  trackChild(electron);
  const code = await waitForExit(electron);
  await shutdown(code ?? 0);
}

function frontendEnv() {
  return {
    ...process.env,
    FORGEX_BACKEND_PORT: String(backendPort),
    PROMPTFORGE_BACKEND_PORT: String(backendPort),
    PROMPTFORGE_API_URL: backendUrl,
    NEXT_PUBLIC_PROMPTFORGE_WS_URL: websocketUrl,
    NEXT_TELEMETRY_DISABLED: "1",
  };
}

function probeFrontend(url) {
  return new Promise((resolve) => {
    const request = http.get(url, { timeout: 10_000 }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        const body = Buffer.concat(chunks).toString("utf8");
        resolve({
          reachable: true,
          forgeX: response.statusCode && response.statusCode < 500 && frontendMarkers.some((marker) => body.includes(marker)),
          statusCode: response.statusCode,
        });
      });
    });
    request.on("timeout", () => {
      request.destroy();
      resolve({ reachable: false, forgeX: false, error: "request timed out" });
    });
    request.on("error", (error) => {
      resolve({ reachable: false, forgeX: false, error: error.code ?? error.message });
    });
  });
}

function trackChild(child) {
  children.add(child);
  child.on("exit", () => children.delete(child));
}

function waitForExit(child) {
  return new Promise((resolve) => {
    child.on("exit", (code) => resolve(code));
  });
}

async function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  await Promise.allSettled([...children].map((child) => terminateChildTree(child)));
  process.exit(code);
}

function terminateChildTree(child) {
  if (!child || child.exitCode !== null || child.pid === undefined) return Promise.resolve();
  if (process.platform !== "win32") {
    child.kill("SIGTERM");
    return Promise.resolve();
  }

  return new Promise((resolve) => {
    const killer = spawn(
      process.env.ComSpec ?? "cmd.exe",
      ["/d", "/s", "/c", `taskkill /PID ${child.pid} /T /F >nul 2>&1`],
      { stdio: "ignore", windowsHide: true },
    );
    killer.once("exit", () => resolve());
    killer.once("error", () => {
      if (!child.killed) child.kill();
      resolve();
    });
  });
}

function spawnCommand(command, args, options) {
  if (process.platform !== "win32") {
    return spawn(command, args, options);
  }
  return spawn(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", quoteCmdCommand(command, args)], options);
}

function quoteCmdCommand(command, args) {
  return [command, ...args].map(quoteCmdArg).join(" ");
}

function quoteCmdArg(value) {
  const text = String(value);
  if (/^[A-Za-z0-9_./:=@-]+$/.test(text)) return text;
  return `"${text.replace(/"/g, '\\"')}"`;
}

function parsePort(value, fallback, name) {
  if (value === undefined || value === null || value === "") return fallback;
  const port = Number.parseInt(value, 10);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid ${name}: ${value}`);
  }
  return port;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isMainModule() {
  return process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
}

process.on("SIGINT", () => void shutdown(130));
process.on("SIGTERM", () => void shutdown(143));

if (isMainModule()) {
  try {
    await runDesktopLauncher();
  } catch (error) {
    console.error(`[forgex-desktop] ${error instanceof Error ? error.message : String(error)}`);
    await shutdown(1);
  }
}
