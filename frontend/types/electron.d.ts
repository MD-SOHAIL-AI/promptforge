export interface DesktopBackgroundAsset {
  assetId: string;
  url: string;
  filename: string;
  mediaType: "image/png" | "image/jpeg" | "image/webp";
  size: number;
}

export interface DesktopBackgroundSelectionResult {
  canceled: boolean;
  asset: DesktopBackgroundAsset | null;
}

export interface ForgeXDesktopBridge {
  getBackendUrl: () => string;
  getStatus: () => Promise<unknown>;
  openFolder: () => Promise<{ canceled: boolean; path: string | null }>;
  openProject: () => Promise<{ canceled: boolean; path: string | null }>;
  selectBackgroundImage: () => Promise<DesktopBackgroundSelectionResult>;
  resolveBackgroundImage: (assetId: string) => Promise<DesktopBackgroundAsset | null>;
  removeBackgroundImage: (assetId: string) => Promise<{ removed: boolean }>;
}

declare global {
  interface Window {
    forgexDesktop?: ForgeXDesktopBridge;
  }
}
