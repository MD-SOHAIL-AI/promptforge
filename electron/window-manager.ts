import { BrowserWindow, app, screen } from "electron";
import fs from "node:fs";
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

interface PersistedWindowState {
  x: number;
  y: number;
  width: number;
  height: number;
  maximized: boolean;
}

const DEFAULT_WINDOW_WIDTH = 1440;
const DEFAULT_WINDOW_HEIGHT = 920;
const MIN_WINDOW_WIDTH = 960;
const MIN_WINDOW_HEIGHT = 640;
const DEV_FRONTEND_READY_TIMEOUT_MS = 180_000;

function windowStatePath(): string {
  return path.join(app.getPath("userData"), "window-state.json");
}

function loadPersistedWindowState(): PersistedWindowState | null {
  try {
    const parsed = JSON.parse(fs.readFileSync(windowStatePath(), "utf8")) as Partial<PersistedWindowState>;
    const { x, y, width, height } = parsed;
    if (![x, y, width, height].every((value) => Number.isFinite(value))) return null;
    if ((width as number) < 1 || (height as number) < 1) return null;
    return {
      x: x as number,
      y: y as number,
      width: width as number,
      height: height as number,
      maximized: Boolean(parsed.maximized),
    };
  } catch {
    return null;
  }
}

// Clamps restored bounds onto the closest visible display work area; returns null when off-screen.
function resolveRestoredWindowState(): PersistedWindowState | null {
  const saved = loadPersistedWindowState();
  if (!saved) return null;

  let bestOverlap = 0;
  let bestWorkArea: Electron.Rectangle | null = null;
  for (const display of screen.getAllDisplays()) {
    const workArea = display.workArea;
    const overlapWidth = Math.min(saved.x + saved.width, workArea.x + workArea.width) - Math.max(saved.x, workArea.x);
    const overlapHeight = Math.min(saved.y + saved.height, workArea.y + workArea.height) - Math.max(saved.y, workArea.y);
    const overlap = Math.max(overlapWidth, 0) * Math.max(overlapHeight, 0);
    if (overlap > bestOverlap) {
      bestOverlap = overlap;
      bestWorkArea = workArea;
    }
  }
  if (!bestWorkArea || bestOverlap <= 0) return null;

  const width = Math.min(Math.max(saved.width, MIN_WINDOW_WIDTH), bestWorkArea.width);
  const height = Math.min(Math.max(saved.height, MIN_WINDOW_HEIGHT), bestWorkArea.height);
  return {
    x: Math.min(Math.max(saved.x, bestWorkArea.x), bestWorkArea.x + bestWorkArea.width - width),
    y: Math.min(Math.max(saved.y, bestWorkArea.y), bestWorkArea.y + bestWorkArea.height - height),
    width,
    height,
    maximized: saved.maximized,
  };
}

function persistWindowBounds(window: BrowserWindow): void {
  window.on("close", () => {
    try {
      const bounds = window.getNormalBounds();
      const state: PersistedWindowState = {
        x: bounds.x,
        y: bounds.y,
        width: bounds.width,
        height: bounds.height,
        maximized: window.isMaximized(),
      };
      fs.mkdirSync(app.getPath("userData"), { recursive: true });
      fs.writeFileSync(windowStatePath(), JSON.stringify(state));
    } catch (error) {
      console.error("[forgex-desktop] failed to persist window state", error);
    }
  });
}

export function createMainWindow(options: MainWindowOptions): BrowserWindow {
  const restored = resolveRestoredWindowState();
  const window = new BrowserWindow({
    x: restored?.x,
    y: restored?.y,
    width: restored?.width ?? DEFAULT_WINDOW_WIDTH,
    height: restored?.height ?? DEFAULT_WINDOW_HEIGHT,
    minWidth: MIN_WINDOW_WIDTH,
    minHeight: MIN_WINDOW_HEIGHT,
    title: "ForgeX",
    backgroundColor: "#07090d",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: options.preloadPath,
    },
  });

  if (restored?.maximized) window.maximize();
  persistWindowBounds(window);
  attachRendererDiagnostics(window);
  installNavigationGuards(window, options.frontendUrl ?? resolveFrontendUrl());
  void loadFrontend(window, options.frontendUrl).catch((error) => {
    console.error("[forgex-desktop] failed to load frontend", safeErrorMessage(error));
  });
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
    backgroundColor: "#07090d",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: options.preloadPath,
    },
  });
  attachRendererDiagnostics(window);
  installNavigationGuards(window, "data:text/html");
  const html = backendErrorHtml(options.reason, options.port);
  void window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
  return window;
}

function installNavigationGuards(window: BrowserWindow, allowedUrl: string): void {
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, targetUrl) => {
    if (!isAllowedNavigation(targetUrl, allowedUrl)) event.preventDefault();
  });
}

function isAllowedNavigation(targetUrl: string, allowedUrl: string): boolean {
  try {
    const target = new URL(targetUrl);
    const allowed = new URL(allowedUrl);
    if (allowed.protocol === "file:") return target.protocol === "file:";
    if (allowed.protocol === "data:") return target.protocol === "data:";
    return target.origin === allowed.origin;
  } catch {
    return false;
  }
}

function shouldOpenDevTools(): boolean {
  const value = process.env.FORGEX_OPEN_DEVTOOLS?.trim().toLowerCase();
  return value === "1" || value === "true" || value === "yes";
}

async function loadFrontend(window: BrowserWindow, requestedFrontendUrl?: string): Promise<void> {
  const frontendUrl = requestedFrontendUrl ?? resolveFrontendUrl();
  if (!app.isPackaged || requestedFrontendUrl || process.env.FORGEX_FRONTEND_URL) {
    console.log(`[forgex-desktop] Loading frontend URL: ${sanitizeUrl(frontendUrl)}`);
    await waitForUrl(frontendUrl, DEV_FRONTEND_READY_TIMEOUT_MS).catch((error) => {
      console.error("[forgex-desktop] frontend readiness check failed", error);
    });
    await loadWindowUrl(window, frontendUrl);
    return;
  }

  const staticIndex = resolveFrontendStaticIndex();
  if (staticIndex) {
    const staticUrl = pathToFileURL(staticIndex).toString();
    console.log(`[forgex-desktop] Loading frontend URL: ${staticUrl}`);
    await loadWindowUrl(window, staticUrl);
    return;
  }

  const fallback = pathToFileURL(path.join(__dirname, "missing-frontend.html")).toString();
  console.log(`[forgex-desktop] Loading frontend URL: ${fallback}`);
  await loadWindowUrl(window, fallback).catch(async () => {
    console.log(`[forgex-desktop] Loading frontend URL: ${frontendUrl}`);
    await loadWindowUrl(window, frontendUrl);
  });
}

async function loadWindowUrl(window: BrowserWindow, url: string): Promise<void> {
  try {
    await window.loadURL(url);
  } catch (error) {
    if (isNavigationAbort(error)) {
      console.warn("[forgex-desktop] frontend navigation aborted; continuing");
      return;
    }
    throw error;
  }
}

function isNavigationAbort(error: unknown): boolean {
  return error instanceof Error && error.message.includes("ERR_ABORTED");
}

function safeErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
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
