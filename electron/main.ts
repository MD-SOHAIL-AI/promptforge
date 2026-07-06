import { app, BrowserWindow } from "electron";

import { BackendManager } from "./backend-manager";
import { FrontendManager } from "./frontend-manager";
import { registerIpcHandlers } from "./ipc";
import { resolveBackendPort, resolveDesktopDataDir, resolveFrontendUrl, resolvePreloadPath } from "./paths";
import { createBackendErrorWindow, createMainWindow } from "./window-manager";

let mainWindow: BrowserWindow | null = null;
let backendManager: BackendManager | null = null;
let frontendManager: FrontendManager | null = null;
let isQuitting = false;

async function bootstrap(): Promise<void> {
  resolveDesktopDataDir();

  backendManager = new BackendManager({ port: resolveBackendPort() });
  registerIpcHandlers(backendManager);

  try {
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

  if (app.isPackaged && !process.env.FORGEX_FRONTEND_URL) {
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
    frontendUrl: resolveFrontendUrl(),
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
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
    mainWindow = createMainWindow({
      preloadPath: resolvePreloadPath(),
      frontendUrl: resolveFrontendUrl(),
    });
  }
});

app.on("will-quit", (event) => {
  if ((!backendManager && !frontendManager) || !isQuitting) return;
  event.preventDefault();
  void Promise.all([
    backendManager?.stop() ?? Promise.resolve(),
    frontendManager?.stop() ?? Promise.resolve(),
  ]).finally(() => {
    backendManager = null;
    frontendManager = null;
    app.exit(0);
  });
});

void app.whenReady().then(bootstrap);
