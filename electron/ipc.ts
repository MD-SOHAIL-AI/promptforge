import { BrowserWindow, dialog, ipcMain, type OpenDialogOptions } from "electron";

import type { BackendManager } from "./backend-manager";

export function registerIpcHandlers(backendManager: BackendManager): void {
  ipcMain.handle("desktop:get-status", () => ({
    desktop: true,
    backend: backendManager.getStatus(),
  }));

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
}
