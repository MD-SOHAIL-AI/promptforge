import { app, BrowserWindow, protocol } from "electron";

import { BACKGROUND_ASSET_SCHEME, BackgroundAssetStore, parseBackgroundAssetUrl } from "./background-assets";
import { BackendManager } from "./backend-manager";
import { FrontendManager } from "./frontend-manager";
import { registerIpcHandlers } from "./ipc";
import {
  resolveBackendPort,
  resolveDesktopBackgroundsDir,
  resolveDesktopDataDir,
  resolveFrontendUrl,
  resolvePreloadPath,
} from "./paths";
import { createBackendErrorWindow, createMainWindow } from "./window-manager";

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
let isQuitting = false;

async function bootstrap(): Promise<void> {
  resolveDesktopDataDir();
  const backgroundAssets = new BackgroundAssetStore(resolveDesktopBackgroundsDir());
  registerBackgroundAssetProtocol(backgroundAssets);

  backendManager = new BackendManager({ port: resolveBackendPort() });
  registerIpcHandlers(backendManager, backgroundAssets);

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
    mainWindow = createMainWindow({
      preloadPath: resolvePreloadPath(),
      frontendUrl: resolveFrontendUrl(),
    });
  }
});

app.on("will-quit", (event) => {
  if ((!backendManager && !frontendManager) || !isQuitting) return;
  event.preventDefault();
  const shutdown = Promise.all([
    backendManager?.stop() ?? Promise.resolve(),
    frontendManager?.stop() ?? Promise.resolve(),
  ]);
  const timeout = new Promise<void>((resolve) => setTimeout(resolve, 8_000));
  void Promise.race([shutdown.then(() => undefined), timeout]).finally(() => {
    backendManager = null;
    frontendManager = null;
    app.exit(0);
  });
});

void app.whenReady().then(bootstrap);
