import { app } from "electron";
import fs from "node:fs";
import path from "node:path";

export function resolveRepoRoot(): string {
  return resolveApplicationRoot(app.isPackaged, process.resourcesPath, process.cwd());
}

export function resolveApplicationRoot(isPackaged: boolean, resourcesPath: string, cwd: string): string {
  return isPackaged ? path.dirname(resourcesPath) : cwd;
}

export function resolveBackendCwd(): string {
  return resolveRepoRoot();
}

export function resolveBackendPort(): number {
  const raw = process.env.FORGEX_BACKEND_PORT ?? process.env.PROMPTFORGE_BACKEND_PORT ?? "8000";
  const port = Number.parseInt(raw, 10);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid backend port: ${raw}`);
  }
  return port;
}

export function resolveBackendUrl(port = resolveBackendPort()): string {
  return `http://127.0.0.1:${port}`;
}

export function resolveFrontendUrl(): string {
  return (process.env.FORGEX_FRONTEND_URL ?? `http://127.0.0.1:${resolveFrontendPort()}`).replace(/\/$/, "");
}

export function resolveFrontendPort(): number {
  const raw = process.env.FORGEX_FRONTEND_PORT ?? "3000";
  const port = Number.parseInt(raw, 10);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid frontend port: ${raw}`);
  }
  return port;
}

export function resolveFrontendServerEntry(): string {
  return resolveFrontendServerEntryFromRoot(resolveRepoRoot());
}

export function resolveFrontendServerEntryFromRoot(root: string): string {
  const candidates = [
    path.join(root, "frontend", "server.js"),
    path.join(root, "frontend", ".next", "standalone", "frontend", "server.js"),
    path.join(root, "frontend", ".next", "standalone", "server.js"),
  ];
  const entry = candidates.find((candidate) => fs.existsSync(candidate));
  if (!entry) {
    throw new Error("Packaged frontend server entry was not found.");
  }
  return entry;
}

export function resolvePreloadPath(): string {
  return path.join(__dirname, "preload.js");
}

export function resolveFrontendStaticIndex(): string | null {
  const candidates = [
    path.join(resolveRepoRoot(), "frontend", "out", "index.html"),
    path.join(resolveRepoRoot(), "dist", "frontend", "index.html"),
  ];

  return candidates.find((candidate) => fs.existsSync(candidate)) ?? null;
}

export function resolveDesktopDataDir(): string {
  const directory = path.join(app.getPath("userData"), "desktop");
  fs.mkdirSync(directory, { recursive: true });
  return directory;
}

export function resolveDesktopBackgroundsDir(): string {
  const directory = path.join(resolveDesktopDataDir(), "backgrounds");
  fs.mkdirSync(directory, { recursive: true });
  return directory;
}
