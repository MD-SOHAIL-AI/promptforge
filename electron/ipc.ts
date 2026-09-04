import { BrowserWindow, dialog, ipcMain, type OpenDialogOptions } from "electron";

import { BackgroundAssetError, BackgroundAssetStore, isBackgroundAssetId } from "./background-assets";
import type { BackendManager } from "./backend-manager";

export function registerIpcHandlers(
  backendManager: BackendManager,
  backgroundAssets: BackgroundAssetStore,
): void {
  ipcMain.handle("desktop:get-status", () => ({
    desktop: true,
    backend: backendManager.getStatus(),
  }));
  ipcMain.on("desktop:get-backend-url", (event) => {
    event.returnValue = backendManager.url;
  });

  ipcMain.handle("desktop:open-folder", async (event) => {
    const parentWindow = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "Open Folder",
      properties: ["openDirectory"],
    };
    const result = parentWindow
      ? await dialog.showOpenDialog(parentWindow, options)
      : await dialog.showOpenDialog(options);
    return {
      canceled: result.canceled,
      path: result.filePaths[0] ?? null,
    };
  });

  ipcMain.handle("desktop:open-project", async (event) => {
    const parentWindow = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "Open ForgeX Project",
      properties: ["openDirectory"],
    };
    const result = parentWindow
      ? await dialog.showOpenDialog(parentWindow, options)
      : await dialog.showOpenDialog(options);
    return {
      canceled: result.canceled,
      path: result.filePaths[0] ?? null,
    };
  });

  ipcMain.handle("desktop:select-background-image", async (event) => {
    const parentWindow = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "Choose a ForgeX background image",
      buttonLabel: "Use Background",
      properties: ["openFile", "dontAddToRecent"],
      filters: [
        { name: "Background images", extensions: ["png", "jpg", "jpeg", "webp"] },
      ],
    };
    const result = parentWindow
      ? await dialog.showOpenDialog(parentWindow, options)
      : await dialog.showOpenDialog(options);
    const selectedPath = result.filePaths[0];
    if (result.canceled || !selectedPath) {
      return { canceled: true, asset: null };
    }
    const asset = await backgroundAssets.importImage(selectedPath);
    return { canceled: false, asset };
  });

  ipcMain.handle("desktop:resolve-background-image", async (_event, assetId: unknown) => {
    if (!isBackgroundAssetId(assetId)) {
      throw new BackgroundAssetError("Invalid background asset identifier.");
    }
    return backgroundAssets.resolve(assetId);
  });

  ipcMain.handle("desktop:remove-background-image", async (_event, assetId: unknown) => {
    if (!isBackgroundAssetId(assetId)) {
      throw new BackgroundAssetError("Invalid background asset identifier.");
    }
    return { removed: await backgroundAssets.remove(assetId) };
  });
}
