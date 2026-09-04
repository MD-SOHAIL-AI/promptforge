import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("forgexDesktop", {
  getBackendUrl: () => ipcRenderer.sendSync("desktop:get-backend-url") as string,
  getStatus: () => ipcRenderer.invoke("desktop:get-status"),
  openFolder: () => ipcRenderer.invoke("desktop:open-folder"),
  openProject: () => ipcRenderer.invoke("desktop:open-project"),
  selectBackgroundImage: () => ipcRenderer.invoke("desktop:select-background-image"),
  resolveBackgroundImage: (assetId: string) => ipcRenderer.invoke("desktop:resolve-background-image", assetId),
  removeBackgroundImage: (assetId: string) => ipcRenderer.invoke("desktop:remove-background-image", assetId),
});
