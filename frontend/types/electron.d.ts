export {};

declare global {
  interface Window {
    forgexDesktop?: {
      getStatus: () => Promise<unknown>;
      openFolder: () => Promise<{ canceled: boolean; path: string | null }>;
      openProject: () => Promise<{ canceled: boolean; path: string | null }>;
    };
  }
}
