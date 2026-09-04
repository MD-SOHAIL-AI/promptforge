import { spawn } from "node:child_process";
import http from "node:http";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const requestedFrontendPort = parsePort(process.env.FORGEX_FRONTEND_PORT, 3000, "FORGEX_FRONTEND_PORT");
const backendPort = parsePort(process.env.FORGEX_BACKEND_PORT ?? process.env.PROMPTFORGE_BACKEND_PORT, 8000, "FORGEX_BACKEND_PORT");
const backendUrl = `http://127.0.0.1:${backendPort}`;
const websocketUrl = `ws://127.0.0.1:${backendPort}`;
const npmExecutable = process.platform === "win32" ? "npm.cmd" : "npm";
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const frontendCwd = path.join(repoRoot, "frontend");
const hasCustomFrontendUrl = Boolean(process.env.FORGEX_FRONTEND_URL);
const children = new Set();
let frontendPort = requestedFrontendPort;
let frontendUrl = resolveFrontendUrl(frontendPort);
let ownedFrontend = null;
let shuttingDown = false;

try {
  console.log("[forgex-desktop] Starting frontend...");
  await ensureFrontend();
  console.log("[forgex-desktop] Launching Electron...");
  await runElectron();
} catch (error) {
  console.error(`[forgex-desktop] ${error instanceof Error ? error.message : String(error)}`);
  await shutdown(1);
}

async function ensureFrontend() {
  const initial = await probeFrontend(frontendUrl);
  if (initial.reachable) {
    if (!initial.forgeX) {
      if (hasCustomFrontendUrl) {
        throw new Error(`${frontendUrl} is already serving a non-ForgeX page. Update FORGEX_FRONTEND_URL, then retry.`);
      }
      console.log(`[forgex-desktop] Port ${frontendPort} is serving another app; looking for a free frontend port...`);
      await useAvailableFrontendPort(frontendPort + 1);
    } else {
      console.log(`[forgex-desktop] Reusing ForgeX frontend at ${frontendUrl}`);
      return;
    }
  } else if (!hasCustomFrontendUrl) {
    await useAvailableFrontendPort(frontendPort);
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

async function useAvailableFrontendPort(startPort) {
  const availablePort = await findAvailablePort(startPort);
  if (availablePort !== frontendPort) {
    console.log(`[forgex-desktop] Using frontend port ${availablePort}.`);
    frontendPort = availablePort;
    frontendUrl = resolveFrontendUrl(frontendPort);
  }
}

async function waitForFrontend() {
  const deadline = Date.now() + 120_000;
  let lastProbe = { reachable: false, forgeX: false, error: "not checked" };
  while (Date.now() < deadline) {
    if (ownedFrontend?.exitCode !== null) {
      throw new Error(`Frontend exited before it became ready with code ${ownedFrontend.exitCode}.`);
    }
    if (!hasCustomFrontendUrl && await isPortAcceptingConnections(frontendPort)) {
      console.log(`[forgex-desktop] Frontend ready at ${frontendUrl}`);
      return;
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
    const request = http.get(url, { timeout: 2_000 }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        const body = Buffer.concat(chunks).toString("utf8");
        resolve({
          reachable: true,
          forgeX: response.statusCode && response.statusCode < 500 && body.includes("ForgeX Embedded Engineering Environment"),
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

function isPortAcceptingConnections(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port });
    socket.setTimeout(1_000);
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("timeout", () => {
      socket.destroy();
      resolve(false);
    });
    socket.once("error", () => resolve(false));
  });
}

async function findAvailablePort(startPort) {
  for (let port = startPort; port <= 65535; port += 1) {
    if (await isPortAvailable(port)) return port;
  }
  throw new Error(`No available frontend port found at or above ${startPort}.`);
}

function isPortAvailable(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen({ host: "127.0.0.1", port });
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
  for (const child of children) {
    if (!child.killed) child.kill("SIGTERM");
  }
  setTimeout(() => process.exit(code), 250).unref();
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

function resolveFrontendUrl(port) {
  return (process.env.FORGEX_FRONTEND_URL ?? `http://127.0.0.1:${port}`).replace(/\/$/, "");
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

process.on("SIGINT", () => void shutdown(130));
process.on("SIGTERM", () => void shutdown(143));
