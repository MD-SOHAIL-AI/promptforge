import { desktopLog } from "./desktop-logging";
import { BackendRecoveryPolicy } from "./backend-recovery";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import http from "node:http";
import path from "node:path";

import { resolveBackendCwd, resolveBackendPort, resolveBackendUrl } from "./paths";
import { resolveBackendPython, type PythonResolution } from "./python-resolver";

export type BackendState = "stopped" | "starting" | "ready" | "crashed";

export interface BackendStatus {
  state: BackendState;
  url: string;
  port: number;
  ownsProcess: boolean;
  pid?: number;
  lastError?: string;
  health?: unknown;
  recoveryAttempts?: number;
  restartScheduled?: boolean;
}

export interface BackendHealthProbeResult {
  reachable: boolean;
  forgeX: boolean;
  healthy: boolean;
  statusCode?: number;
  body?: unknown;
  error?: string;
  timedOut?: boolean;
}

interface BackendManagerOptions {
  port?: number;
  cwd?: string;
  readinessTimeoutMs?: number;
  readinessIntervalMs?: number;
  healthProbe?: (url: string) => Promise<BackendHealthProbeResult>;
  spawnBackend?: typeof spawn;
  resolvePython?: (repoRoot: string) => PythonResolution;
  expectedRuntimeContract?: string;
  expectedSourceFingerprint?: string | null;
  localToken?: string;
  restartDelaysMs?: readonly number[];
}

export const BACKEND_RUNTIME_CONTRACT = "forgex-agent-runtime-v3";

export class BackendManager {
  private process: ChildProcessWithoutNullStreams | null = null;
  private state: BackendState = "stopped";
  private lastError: string | undefined;
  private health: unknown;
  private readonly port: number;
  private readonly cwd: string;
  private readonly readinessTimeoutMs: number;
  private readonly readinessIntervalMs: number;
  private readonly healthProbe: (url: string) => Promise<BackendHealthProbeResult>;
  private readonly spawnBackend: typeof spawn;
  private readonly resolvePython: (repoRoot: string) => PythonResolution;
  private readonly expectedRuntimeContract: string;
  private readonly expectedSourceFingerprint: string | null;
  private readonly localToken: string | undefined;
  private ownsProcess = false;
  private intentionalStop = false;
  private restartTimer: ReturnType<typeof setTimeout> | null = null;
  private healthTimer: ReturnType<typeof setInterval> | null = null;
  private healthCheckRunning = false;
  private readonly recovery: BackendRecoveryPolicy;
  private starting: Promise<BackendStatus> | null = null;

  constructor(options: BackendManagerOptions = {}) {
    this.port = options.port ?? resolveBackendPort();
    this.cwd = options.cwd ?? resolveBackendCwd();
    this.readinessTimeoutMs = options.readinessTimeoutMs ?? 60_000;
    this.readinessIntervalMs = options.readinessIntervalMs ?? 500;
    this.healthProbe = options.healthProbe ?? fetchForgeXHealth;
    this.spawnBackend = options.spawnBackend ?? spawn;
    this.resolvePython = options.resolvePython ?? ((repoRoot) => resolveBackendPython({ repoRoot }));
    this.expectedRuntimeContract = options.expectedRuntimeContract ?? BACKEND_RUNTIME_CONTRACT;
    this.expectedSourceFingerprint = options.expectedSourceFingerprint === undefined
      ? computeBackendSourceFingerprint(this.cwd)
      : options.expectedSourceFingerprint;
    this.localToken = options.localToken?.trim() || undefined;
    this.recovery = new BackendRecoveryPolicy(options.restartDelaysMs);
  }

  get url(): string {
    return resolveBackendUrl(this.port);
  }

  async start(): Promise<BackendStatus> {
    this.intentionalStop = false;
    if (this.state === "ready" && this.ownsProcess && this.process) return this.getStatus();
    if (this.starting) return this.starting;
    this.starting = this.startInternal();
    try { return await this.starting; }
    finally { this.starting = null; }
  }

  private async startInternal(): Promise<BackendStatus> {
    desktopLog.log(`[forgex-backend] Checking port ${this.port}...`);
    const existing = await this.fetchHealth();
    if (this.intentionalStop) throw new Error("Backend startup stopped");
    if (existing.reachable) {
      if (!existing.forgeX) {
        this.state = "crashed";
        this.lastError = `Port ${this.port} is already in use by another process. Close that process or configure a different ForgeX backend port.`;
        this.health = existing.body;
        desktopLog.error(`[forgex-backend] ${this.lastError}`);
        throw new Error(this.lastError);
      }
      if (!existing.healthy) {
        this.state = "crashed";
        this.lastError = `Existing ForgeX backend detected on port ${this.port}, but it is not healthy. Restart it or configure a different ForgeX backend port.`;
        this.health = existing.body;
        desktopLog.error(`[forgex-backend] ${this.lastError}`);
        throw new Error(this.lastError);
      }
      const incompatibility = this.compatibilityError(existing.body);
      if (incompatibility) {
        this.state = "crashed";
        this.lastError = incompatibility;
        this.health = existing.body;
        desktopLog.error(`[forgex-backend] ${this.lastError}`);
        throw new Error(this.lastError);
      }
      this.health = existing.body;
      this.state = "ready";
      this.ownsProcess = false;
      this.process = null;
      this.lastError = undefined;
      desktopLog.log(`Existing ForgeX backend detected on port ${this.port}. Reusing backend.`);
      return this.getStatus();
    }
    if (existing.timedOut) {
      this.state = "crashed";
      this.lastError = `Backend health check timed out on port ${this.port}. Port may be occupied by an unresponsive service.`;
      desktopLog.error(`[forgex-backend] ${this.lastError}`);
      throw new Error(this.lastError);
    }

    if (existing.error) {
      desktopLog.log(`[forgex-backend] No reusable backend detected on port ${this.port}: ${existing.error}`);
    } else {
      desktopLog.log(`[forgex-backend] No reusable backend detected on port ${this.port}. Starting a new backend.`);
    }

    this.state = "starting";
    this.lastError = undefined;
    this.health = undefined;
    this.ownsProcess = false;

    desktopLog.log("[forgex-backend] Resolving Python environment...");
    let python: PythonResolution;
    try {
      python = this.resolvePython(this.cwd);
    } catch (error) {
      this.state = "crashed";
      this.lastError = error instanceof Error ? error.message : String(error);
      desktopLog.error(`[forgex-backend] ${this.lastError}`);
      throw error;
    }
    const pythonExecutable = python.executable;
    desktopLog.log(`[forgex-backend] Using Python: ${pythonExecutable}`);
    desktopLog.log("[forgex-backend] Starting FastAPI backend...");
    try {
      this.process = this.spawnBackend(
        pythonExecutable,
        [
          "-B",
          "-m",
          "uvicorn",
          "backend.api.app:create_app",
          "--factory",
          "--host",
          "127.0.0.1",
          "--port",
          String(this.port),
          "--no-access-log",
        ],
        {
          cwd: this.cwd,
          env: {
            ...process.env,
            FORGEX_BACKEND_PORT: String(this.port),
            FORGEX_DESKTOP: "1",
            FORGEX_NODE_EXECUTABLE: process.execPath,
            FORGEX_BACKEND_URL: this.url,
            ...(this.localToken ? { FORGEX_LOCAL_TOKEN: this.localToken } : {}),
          },
          windowsHide: true,
        },
      );
    } catch (error) {
      this.state = "crashed";
      this.lastError = `Failed to start ForgeX backend process: ${error instanceof Error ? error.message : String(error)}`;
      this.process = null;
      this.ownsProcess = false;
      desktopLog.error(`[forgex-backend] ${this.lastError}`);
      throw new Error(this.lastError);
    }

    this.ownsProcess = true;
    const child = this.process;
    const backendInstance = randomUUID();
    desktopLog.log(`[forgex-backend] Spawned backend pid=${this.process.pid ?? "unknown"} port=${this.port}`);
    this.process.stdout.on("data", (chunk: Buffer) => {
      desktopLog.log(`[forgex-backend] ${safeLogChunk(chunk)}`);
    });
    this.process.stderr.on("data", (chunk: Buffer) => {
      desktopLog.error(`[forgex-backend] ${safeLogChunk(chunk)}`);
    });
    this.process.on("exit", (code, signal) => {
      if (this.process !== child) return;
      desktopLog.error(`[forgex-backend] ${JSON.stringify({ event: "backend.exited", instance: backendInstance, pid: this.process?.pid, code, signal, expected: this.state === "stopped", timestamp: new Date().toISOString() })}`);
      if (this.state !== "stopped") {
        this.state = "crashed";
        this.lastError = `Backend exited with code ${code ?? "null"} and signal ${signal ?? "null"}`;
        desktopLog.error(`[forgex-backend] ${this.lastError}`);
      }
      this.process = null;
      this.ownsProcess = false;
      if (!this.intentionalStop) this.scheduleOwnedRestart();
    });
    this.process.on("error", (error) => {
      if (this.process !== child) return;
      this.state = "crashed";
      this.lastError = error.message;
      desktopLog.error(`[forgex-backend] ${this.lastError}`);
      this.process = null;
      this.ownsProcess = false;
      if (!this.intentionalStop) this.scheduleOwnedRestart();
    });

    try {
      await this.waitForReady();
      this.startHealthSupervisor();
      return this.getStatus();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await this.terminateOwnedProcess();
      this.state = "crashed";
      this.lastError = message;
      throw error;
    }
  }

  async stop(): Promise<void> {
    this.intentionalStop = true;
    if (this.restartTimer) clearTimeout(this.restartTimer);
    this.restartTimer = null;
    if (this.healthTimer) clearInterval(this.healthTimer);
    this.healthTimer = null;
    this.state = "stopped";
    await this.terminateOwnedProcess();
    this.lastError = undefined;
    this.health = undefined;
  }

  private async terminateOwnedProcess(): Promise<void> {
    if (!this.process || !this.ownsProcess) {
      this.process = null;
      this.ownsProcess = false;
      return;
    }

    const child = this.process;
    this.process = null;
    this.ownsProcess = false;

    await new Promise<void>((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        resolve();
      };
      const timeout = setTimeout(() => {
        if (!child.killed) child.kill("SIGKILL");
        finish();
      }, 5_000);

      child.once("exit", finish);
      if (!child.kill("SIGTERM")) finish();
    });
  }

  getStatus(): BackendStatus {
    return {
      state: this.state,
      url: this.url,
      port: this.port,
      ownsProcess: this.ownsProcess,
      pid: this.process?.pid,
      lastError: this.lastError,
      health: this.health,
      recoveryAttempts: this.recovery.used,
      restartScheduled: this.restartTimer !== null,
    };
  }

  private scheduleOwnedRestart(): void {
    if (this.intentionalStop || this.restartTimer) return;
    if (this.healthTimer) clearInterval(this.healthTimer);
    this.healthTimer = null;
    const delayMs = this.recovery.nextDelay();
    if (delayMs === null) {
      this.lastError = "Backend recovery stopped after three attempts. Saved work is preserved. Restart ForgeX to retry.";
      return;
    }
    desktopLog.error(`[forgex-backend] Scheduled owned backend restart in ${delayMs}ms`);
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null;
      if (this.intentionalStop) return;
      void this.start().catch(error => {
        this.lastError = error instanceof Error ? error.message : String(error);
        this.scheduleOwnedRestart();
      });
    }, delayMs);
    this.restartTimer.unref();
  }

  private startHealthSupervisor(): void {
    if (this.healthTimer) clearInterval(this.healthTimer);
    let failures = 0;
    this.healthTimer = setInterval(() => {
      if (this.intentionalStop || !this.ownsProcess || this.state !== "ready" || this.healthCheckRunning) return;
      this.healthCheckRunning = true;
      const child = this.process;
      void this.fetchHealth().catch((error): BackendHealthProbeResult => ({reachable:false, forgeX:false, healthy:false,
        error:error instanceof Error ? error.message : String(error)})).then(async result => {
        if (this.process !== child || this.intentionalStop) return;
        if (result.healthy && !this.compatibilityError(result.body)) { failures = 0; return; }
        if (++failures < 2) return;
        this.state = "crashed";
        this.lastError = "The owned backend stopped responding. Reconnecting to saved work.";
        await this.terminateOwnedProcess();
        if (!this.intentionalStop) this.scheduleOwnedRestart();
      }).catch(() => { failures += 1; }).finally(() => { this.healthCheckRunning = false; });
    }, 10_000);
    this.healthTimer.unref();
  }

  private async waitForReady(): Promise<void> {
    const deadline = Date.now() + this.readinessTimeoutMs;
    let lastProbe: BackendHealthProbeResult | null = null;
    while (Date.now() < deadline) {
      if (this.intentionalStop) throw new Error("Backend startup stopped");
      if (this.state === "crashed" && !this.process) {
        throw new Error(this.lastError ?? "Backend process exited before it became healthy");
      }
      const result = await this.fetchHealth();
      if (this.intentionalStop) throw new Error("Backend startup stopped");
      lastProbe = result;
      if (result.reachable && !result.forgeX) {
        this.state = "crashed";
        this.lastError = `Port ${this.port} is already in use by another process. Close that process or configure a different ForgeX backend port.`;
        throw new Error(this.lastError);
      }
      if (result.reachable && result.forgeX && result.healthy) {
        const incompatibility = this.compatibilityError(result.body);
        if (incompatibility) {
          this.state = "crashed";
          this.lastError = incompatibility;
          throw new Error(this.lastError);
        }
        this.health = result.body;
        this.state = "ready";
        this.lastError = undefined;
        desktopLog.log(`[forgex-backend] Backend healthy at ${this.url}`);
        return;
      }
      await delay(this.readinessIntervalMs);
    }

    this.state = "crashed";
    const suffix = lastProbe?.error ? ` Last health error: ${lastProbe.error}` : "";
    this.lastError = `Backend did not become healthy within ${this.readinessTimeoutMs}ms.${suffix}`;
    throw new Error(this.lastError);
  }

  private fetchHealth(): Promise<BackendHealthProbeResult> {
    return this.healthProbe(this.url);
  }

  private compatibilityError(body: unknown): string | undefined {
    if (!isForgeXHealth(body) || body.runtime_contract !== this.expectedRuntimeContract) {
      return `A stale or incompatible ForgeX backend is already running on port ${this.port}. Fully stop that backend (or run npm run dev:cleanup) and relaunch ForgeX.`;
    }
    if (
      this.expectedSourceFingerprint
      && body.source_fingerprint !== this.expectedSourceFingerprint
    ) {
      return `The ForgeX backend on port ${this.port} was started from older source code. Fully stop it (or run npm run dev:cleanup) and relaunch ForgeX.`;
    }
    if (this.localToken) {
      const expectedAuthFingerprint = createHash("sha256").update(this.localToken, "utf8").digest("hex").slice(0, 24);
      if (body.local_auth_fingerprint !== expectedAuthFingerprint) {
        return `A ForgeX backend from another desktop session is already running on port ${this.port}. Fully stop it (or run npm run dev:cleanup) and relaunch ForgeX.`;
      }
    }
    return undefined;
  }
}

export function fetchForgeXHealth(url: string): Promise<BackendHealthProbeResult> {
  return new Promise((resolve) => {
    const request = http.get(`${url}/health`, { timeout: 12_000 }, (response) => {
      const chunks: Buffer[] = [];
      response.on("data", (chunk: Buffer) => chunks.push(chunk));
      response.on("end", () => {
        const statusCode = response.statusCode ?? 0;
        const raw = Buffer.concat(chunks).toString("utf8");
        let body: unknown = raw;
        try {
          body = raw ? JSON.parse(raw) : undefined;
        } catch {
          body = raw;
        }
        let forgeX = false;
        let healthy = false;
        if (isForgeXHealth(body)) {
          forgeX = true;
          healthy = statusCode >= 200 && statusCode < 300 && body.status === "healthy";
        }
        resolve({
          reachable: true,
          forgeX,
          healthy,
          statusCode,
          body,
          error: healthy ? undefined : `Unexpected health response status=${statusCode}`,
        });
      });
    });
    request.on("timeout", () => {
      request.destroy();
      resolve({
        reachable: false,
        forgeX: false,
        healthy: false,
        timedOut: true,
        error: "Health request timed out",
      });
    });
    request.on("error", (error: NodeJS.ErrnoException) => {
      resolve({
        reachable: false,
        forgeX: false,
        healthy: false,
        error: error.code ?? error.message,
      });
    });
  });
}

function isForgeXHealth(body: unknown): body is {
  service: string;
  status: string;
  runtime_contract?: unknown;
  source_fingerprint?: unknown;
  local_auth_fingerprint?: unknown;
} {
  return typeof body === "object" && body !== null && "service" in body && (body as { service?: unknown }).service === "forgex-backend";
}

export function computeBackendSourceFingerprint(repoRoot: string): string | null {
  const backendRoot = path.join(repoRoot, "backend");
  if (!existsSync(backendRoot)) return null;
  const files: string[] = [];
  const visit = (directory: string) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.name === "__pycache__") continue;
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(absolute);
      else if (entry.isFile() && entry.name.endsWith(".py")) files.push(absolute);
    }
  };
  visit(backendRoot);
  for (const releaseInput of ["requirements.txt", "requirements.in", "requirements.lock", "pyproject.toml"]) {
    const candidate = path.join(repoRoot, releaseInput);
    if (existsSync(candidate)) files.push(candidate);
  }
  files.sort((left, right) => {
    const leftRelative = normalizeRelative(repoRoot, left);
    const rightRelative = normalizeRelative(repoRoot, right);
    return leftRelative < rightRelative ? -1 : leftRelative > rightRelative ? 1 : 0;
  });
  const digest = createHash("sha256");
  for (const file of files) {
    digest.update(normalizeRelative(repoRoot, file), "utf8");
    digest.update("\0", "utf8");
    digest.update(readFileSync(file));
    digest.update("\0", "utf8");
  }
  return digest.digest("hex");
}

function normalizeRelative(root: string, file: string): string {
  return path.relative(root, file).split(path.sep).join("/");
}

function safeLogChunk(chunk: Buffer): string {
  const text = chunk.toString("utf8").trimEnd();
  const capped = text.length > 4_000 ? `${text.slice(0, 4_000)}... [truncated]` : text;
  return capped.replace(/[\r\n]+$/g, "");
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
