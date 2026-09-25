import { app, BrowserWindow, protocol, session } from "electron";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { BACKGROUND_ASSET_SCHEME, BackgroundAssetStore, parseBackgroundAssetUrl } from "./background-assets";
import { BackendManager } from "./backend-manager";
import { FrontendManager } from "./frontend-manager";
import { HarnessManager } from "./harness-manager";
import { registerIpcHandlers } from "./ipc";
import {
  resolveBackendPort,
  resolveDesktopBackgroundsDir,
  resolveDesktopDataDir,
  resolveFrontendUrl,
  resolveHarnessPort,
  resolvePreloadPath,
} from "./paths";
import { createBackendErrorWindow, createMainWindow } from "./window-manager";

if (!app.isPackaged) {
  const configuredUserDataDir = process.env.FORGEX_ELECTRON_USER_DATA_DIR || path.join(process.cwd(), ".forgex", "electron-user-data");
  const userDataDir = prepareDevUserDataDir(configuredUserDataDir);
  app.setPath("userData", userDataDir);
  app.commandLine.appendSwitch("no-sandbox");
}

if (process.env.FORGEX_ENABLE_GPU !== "1") {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu");
  app.commandLine.appendSwitch("disable-gpu-compositing");
  app.commandLine.appendSwitch("in-process-gpu");
}

protocol.registerSchemesAsPrivileged([
  {
    scheme: BACKGROUND_ASSET_SCHEME,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: false,
    },
  },
]);

let mainWindow: BrowserWindow | null = null;
let backendManager: BackendManager | null = null;
let frontendManager: FrontendManager | null = null;
let harnessManager: HarnessManager | null = null;
let isQuitting = false;
const LOCAL_SESSION_COOKIE = "forgex_local_session";
const hasSingleInstanceLock = app.requestSingleInstanceLock();

if (hasSingleInstanceLock) {
  app.on("second-instance", () => {
    if (mainWindow === null || mainWindow.isDestroyed()) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });
}

function prepareDevUserDataDir(configuredUserDataDir: string): string {
  try {
    ensureElectronCacheDirs(configuredUserDataDir);
    return configuredUserDataDir;
  } catch (error) {
    const fallback = path.join(process.cwd(), ".forgex", "electron-user-data-dev", String(process.pid));
    console.warn("[forgex-desktop] dev userData directory unavailable; using per-session fallback", error);
    ensureElectronCacheDirs(fallback);
    return fallback;
  }
}

function ensureElectronCacheDirs(userDataDir: string): void {
  for (const relative of ["", "Cache", path.join("Cache", "Cache_Data"), "GPUCache", "Network"]) {
    fs.mkdirSync(path.join(userDataDir, relative), { recursive: true });
  }
}

async function bootstrap(): Promise<void> {
  resolveDesktopDataDir();
  const backgroundAssets = new BackgroundAssetStore(resolveDesktopBackgroundsDir());
  registerBackgroundAssetProtocol(backgroundAssets);

  const backendPort = resolveBackendPort();
  const localToken = randomBytes(32).toString("base64url");
  const harnessToken = randomBytes(32).toString("base64url");
  const harnessPort = resolveHarnessPort();
  process.env.FORGEX_BACKEND_PORT = String(backendPort);
  process.env.FORGEX_BACKEND_URL = `http://127.0.0.1:${backendPort}`;
  process.env.FORGEX_HARNESS_PORT = String(harnessPort);
  process.env.FORGEX_HARNESS_URL = `http://127.0.0.1:${harnessPort}`;
  process.env.FORGEX_HARNESS_TOKEN = harnessToken;
  await installLocalSessionCookies(localToken);

  harnessManager = new HarnessManager({ port: harnessPort, token: harnessToken });
  backendManager = new BackendManager({ port: backendPort, localToken });
  registerIpcHandlers(backendManager, backgroundAssets, harnessManager);

  try {
    await harnessManager.start();
    await backendManager.start();
  } catch (error) {
    console.error("[forgex-desktop] backend startup failed", error);
    mainWindow = createBackendErrorWindow({
      preloadPath: resolvePreloadPath(),
      reason: error instanceof Error ? error.message : String(error),
      port: resolveBackendPort(),
    });
    mainWindow.on("closed", () => {
      mainWindow = null;
    });
    return;
  }

  if (app.isPackaged) {
    frontendManager = new FrontendManager();
    try {
      await frontendManager.start();
    } catch (error) {
      console.error("[forgex-desktop] frontend startup failed", error instanceof Error ? error.message : String(error));
      mainWindow = createBackendErrorWindow({
        preloadPath: resolvePreloadPath(),
        reason: error instanceof Error ? error.message : String(error),
        port: resolveBackendPort(),
      });
      return;
    }
  }

  mainWindow = createMainWindow({
    preloadPath: resolvePreloadPath(),
    frontendUrl: frontendManager?.url ?? resolveFrontendUrl(),
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}


async function installLocalSessionCookies(token: string): Promise<void> {
  const targets = ["http://127.0.0.1/", "http://localhost/"];
  for (const url of targets) {
    await session.defaultSession.cookies.set({
      url,
      name: LOCAL_SESSION_COOKIE,
      value: token,
      path: "/",
      httpOnly: true,
      secure: false,
      sameSite: "strict",
    });
  }
}

function registerBackgroundAssetProtocol(backgroundAssets: BackgroundAssetStore): void {
  protocol.handle(BACKGROUND_ASSET_SCHEME, async (request) => {
    const assetId = parseBackgroundAssetUrl(request.url);
    if (!assetId) return new Response(null, { status: 404 });
    try {
      const asset = await backgroundAssets.read(assetId);
      if (!asset) return new Response(null, { status: 404 });
      return new Response(new Uint8Array(asset.bytes), {
        status: 200,
        headers: {
          "Cache-Control": "private, max-age=86400",
          "Content-Type": asset.mediaType,
          "X-Content-Type-Options": "nosniff",
        },
      });
    } catch (error) {
      console.error("[forgex-desktop] background asset load failed", error);
      return new Response(null, { status: 500 });
    }
  });
}

app.on("before-quit", () => {
  isQuitting = true;
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (mainWindow === null) {
    if (app.isPackaged && frontendManager?.getStatus().state !== "ready") return;
    mainWindow = createMainWindow({
      preloadPath: resolvePreloadPath(),
      frontendUrl: frontendManager?.url ?? resolveFrontendUrl(),
    });
  }
});

app.on("will-quit", (event) => {
  if ((!backendManager && !frontendManager && !harnessManager) || !isQuitting) return;
  event.preventDefault();
  void Promise.all([
    backendManager?.stop() ?? Promise.resolve(),
    frontendManager?.stop() ?? Promise.resolve(),
    harnessManager?.stop() ?? Promise.resolve(),
  ]).finally(() => {
    backendManager = null;
    frontendManager = null;
    harnessManager = null;
    app.exit(0);
  });
});

if (hasSingleInstanceLock) {
  void app.whenReady().then(bootstrap);
} else {
  app.quit();
}
