import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("forgexDesktop", {
  getStatus: () => ipcRenderer.invoke("desktop:get-status"),
  openFolder: () => ipcRenderer.invoke("desktop:open-folder"),
  openProject: () => ipcRenderer.invoke("desktop:open-project"),
});
