import { BrowserWindow, app } from "electron";
import http from "node:http";
import https from "node:https";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { resolveFrontendStaticIndex, resolveFrontendUrl } from "./paths";

interface MainWindowOptions {
  preloadPath: string;
  frontendUrl?: string;
}

interface BackendErrorWindowOptions {
  preloadPath: string;
  reason: string;
  port: number;
}

export function createMainWindow(options: MainWindowOptions): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 960,
    minHeight: 640,
    title: "ForgeX",
    backgroundColor: "#0d1117",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
      preload: options.preloadPath,
    },
  });

  attachRendererDiagnostics(window);
  void loadFrontend(window, options.frontendUrl);
  if (!app.isPackaged && shouldOpenDevTools()) {
    window.webContents.once("did-finish-load", () => {
      window.webContents.openDevTools({ mode: "detach" });
    });
  }
  return window;
}

export function createBackendErrorWindow(options: BackendErrorWindowOptions): BrowserWindow {
  const window = new BrowserWindow({
    width: 900,
    height: 620,
    minWidth: 720,
    minHeight: 480,
    title: "ForgeX Backend Startup Error",
    backgroundColor: "#0d1117",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
      preload: options.preloadPath,
    },
  });
  attachRendererDiagnostics(window);
  const html = backendErrorHtml(options.reason, options.port);
  void window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
  return window;
}

function shouldOpenDevTools(): boolean {
  const value = process.env.FORGEX_OPEN_DEVTOOLS?.trim().toLowerCase();
  return value === "1" || value === "true" || value === "yes";
}

async function loadFrontend(window: BrowserWindow, requestedFrontendUrl?: string): Promise<void> {
  const frontendUrl = requestedFrontendUrl ?? resolveFrontendUrl();
  if (!app.isPackaged || requestedFrontendUrl || process.env.FORGEX_FRONTEND_URL) {
    console.log(`[forgex-desktop] Loading frontend URL: ${sanitizeUrl(frontendUrl)}`);
    await waitForUrl(frontendUrl, 15_000).catch((error) => {
      console.error("[forgex-desktop] frontend readiness check failed", error);
    });
    await window.loadURL(frontendUrl);
    return;
  }

  const staticIndex = resolveFrontendStaticIndex();
  if (staticIndex) {
    const staticUrl = pathToFileURL(staticIndex).toString();
    console.log(`[forgex-desktop] Loading frontend URL: ${staticUrl}`);
    await window.loadURL(staticUrl);
    return;
  }

  const fallback = pathToFileURL(path.join(__dirname, "missing-frontend.html")).toString();
  console.log(`[forgex-desktop] Loading frontend URL: ${fallback}`);
  await window.loadURL(fallback).catch(async () => {
    console.log(`[forgex-desktop] Loading frontend URL: ${frontendUrl}`);
    await window.loadURL(frontendUrl);
  });
}

function sanitizeUrl(value: string): string {
  try {
    const url = new URL(value);
    return `${url.protocol}//${url.hostname}${url.port ? `:${url.port}` : ""}${url.pathname}`;
  } catch {
    return "unavailable";
  }
}

function attachRendererDiagnostics(window: BrowserWindow): void {
  window.webContents.on("did-finish-load", () => {
    console.log("[forgex-desktop] Renderer did-finish-load");
  });
  window.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL) => {
    console.error(
      `[forgex-desktop] Renderer did-fail-load code=${errorCode} description=${errorDescription} url=${validatedURL}`,
    );
  });
  window.webContents.on("console-message", (_event, level, message, line, sourceId) => {
    console.log(
      `[forgex-renderer] level=${level} source=${sourceId}:${line} ${message}`,
    );
  });
  window.webContents.on("render-process-gone", (_event, details) => {
    console.error(
      `[forgex-desktop] Renderer render-process-gone reason=${details.reason} exitCode=${details.exitCode}`,
    );
  });
}

function backendErrorHtml(reason: string, port: number): string {
  const safeReason = escapeHtml(reason);
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ForgeX Backend Startup Error</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0d1117; color: #e6edf3; }
    main { width: min(680px, calc(100vw - 48px)); border: 1px solid #30363d; background: #161b22; padding: 24px; border-radius: 8px; }
    h1 { margin: 0 0 16px; font-size: 22px; font-weight: 650; letter-spacing: 0; }
    p { margin: 0 0 12px; color: #8b949e; line-height: 1.5; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid #30363d; background: #0d1117; padding: 12px; border-radius: 6px; color: #ffa657; }
    code { color: #79c0ff; }
  </style>
</head>
<body>
  <main>
    <h1>Backend failed to start.</h1>
    <p>Reason:</p>
    <pre>${safeReason}</pre>
    <p>Port <code>${port}</code> may already be in use. Close that process or configure <code>FORGEX_BACKEND_PORT</code> to use a different ForgeX backend port.</p>
  </main>
</body>
</html>`;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function waitForUrl(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError: Error | null = null;
  while (Date.now() < deadline) {
    try {
      const ready = await probeUrl(url);
      if (ready) return;
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
    }
    await delay(300);
  }
  throw lastError ?? new Error(`Timed out waiting for ${url}`);
}

function probeUrl(url: string): Promise<boolean> {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const client = parsed.protocol === "https:" ? https : http;
    const request = client.request(
      parsed,
      { method: "GET", timeout: 2_000 },
      (response) => {
        response.resume();
        resolve((response.statusCode ?? 0) >= 200 && (response.statusCode ?? 0) < 500);
      },
    );
    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
    request.on("error", reject);
    request.end();
  });
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
