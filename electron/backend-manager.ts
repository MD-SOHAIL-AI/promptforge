import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import http from "node:http";

import { resolveBackendCwd, resolveBackendPort, resolveBackendUrl } from "./paths";

export type BackendState = "stopped" | "starting" | "ready" | "crashed";

export interface BackendStatus {
  state: BackendState;
  url: string;
  port: number;
  ownsProcess: boolean;
  pid?: number;
  lastError?: string;
  health?: unknown;
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
}

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
  private ownsProcess = false;

  constructor(options: BackendManagerOptions = {}) {
    this.port = options.port ?? resolveBackendPort();
    this.cwd = options.cwd ?? resolveBackendCwd();
    this.readinessTimeoutMs = options.readinessTimeoutMs ?? 30_000;
    this.readinessIntervalMs = options.readinessIntervalMs ?? 500;
    this.healthProbe = options.healthProbe ?? fetchForgeXHealth;
    this.spawnBackend = options.spawnBackend ?? spawn;
  }

  get url(): string {
    return resolveBackendUrl(this.port);
  }

  async start(): Promise<BackendStatus> {
    const existing = await this.fetchHealth();
    if (existing.reachable) {
      if (!existing.forgeX) {
        this.state = "crashed";
        this.lastError = `Port ${this.port} is already in use by another process. Close that process or configure a different ForgeX backend port.`;
        this.health = existing.body;
        console.error(`[forgex-backend] ${this.lastError}`);
        throw new Error(this.lastError);
      }
      if (!existing.healthy) {
        this.state = "crashed";
        this.lastError = `Existing ForgeX backend detected on port ${this.port}, but it is not healthy. Restart it or configure a different ForgeX backend port.`;
        this.health = existing.body;
        console.error(`[forgex-backend] ${this.lastError}`);
        throw new Error(this.lastError);
      }
      this.health = existing.body;
      this.state = "ready";
      this.ownsProcess = false;
      this.process = null;
      this.lastError = undefined;
      console.log(`Existing ForgeX backend detected on port ${this.port}. Reusing backend.`);
      return this.getStatus();
    }
    if (existing.timedOut) {
      this.state = "crashed";
      this.lastError = `Backend health check timed out on port ${this.port}. Port may be occupied by an unresponsive service.`;
      console.error(`[forgex-backend] ${this.lastError}`);
      throw new Error(this.lastError);
    }

    if (existing.error) {
      console.log(`[forgex-backend] No reusable backend detected on port ${this.port}: ${existing.error}`);
    } else {
      console.log(`[forgex-backend] No reusable backend detected on port ${this.port}. Starting a new backend.`);
    }

    this.state = "starting";
    this.lastError = undefined;
    this.health = undefined;
    this.ownsProcess = true;

    const pythonExecutable = process.env.FORGEX_PYTHON ?? process.env.PYTHON ?? "python";
    this.process = this.spawnBackend(
      pythonExecutable,
      [
        "-m",
        "uvicorn",
        "backend.api.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        String(this.port),
      ],
      {
        cwd: this.cwd,
        env: {
          ...process.env,
          FORGEX_BACKEND_PORT: String(this.port),
          PROMPTFORGE_BACKEND_PORT: String(this.port),
          PROMPTFORGE_DESKTOP: "1",
          PROMPTFORGE_BACKEND_URL: this.url,
        },
        windowsHide: true,
      },
    );

    console.log(`[forgex-backend] Spawned backend pid=${this.process.pid ?? "unknown"} port=${this.port}`);
    this.process.stdout.on("data", (chunk: Buffer) => {
      console.log(`[forgex-backend] ${safeLogChunk(chunk)}`);
    });
    this.process.stderr.on("data", (chunk: Buffer) => {
      console.error(`[forgex-backend] ${safeLogChunk(chunk)}`);
    });
    this.process.on("exit", (code, signal) => {
      if (this.state !== "stopped") {
        this.state = "crashed";
        this.lastError = `Backend exited with code ${code ?? "null"} and signal ${signal ?? "null"}`;
        console.error(`[forgex-backend] ${this.lastError}`);
      }
      this.process = null;
    });
    this.process.on("error", (error) => {
      this.state = "crashed";
      this.lastError = error.message;
      console.error(`[forgex-backend] ${this.lastError}`);
      this.process = null;
    });

    await this.waitForReady();
    return this.getStatus();
  }

  async stop(): Promise<void> {
    if (!this.process || !this.ownsProcess) {
      this.state = "stopped";
      this.ownsProcess = false;
      return;
    }

    const child = this.process;
    this.state = "stopped";
    this.process = null;
    this.ownsProcess = false;

    await new Promise<void>((resolve) => {
      const timeout = setTimeout(() => {
        if (!child.killed) {
          child.kill("SIGKILL");
        }
        resolve();
      }, 5_000);

      child.once("exit", () => {
        clearTimeout(timeout);
        resolve();
      });

      if (!child.kill("SIGTERM")) {
        clearTimeout(timeout);
        resolve();
      }
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
    };
  }

  private async waitForReady(): Promise<void> {
    const deadline = Date.now() + this.readinessTimeoutMs;
    let lastProbe: BackendHealthProbeResult | null = null;
    while (Date.now() < deadline) {
      if (this.state === "crashed" && !this.process) {
        throw new Error(this.lastError ?? "Backend process exited before it became healthy");
      }
      const result = await this.fetchHealth();
      lastProbe = result;
      if (result.reachable && !result.forgeX) {
        this.state = "crashed";
        this.lastError = `Port ${this.port} is already in use by another process. Close that process or configure a different ForgeX backend port.`;
        throw new Error(this.lastError);
      }
      if (result.reachable && result.forgeX && result.healthy) {
        this.health = result.body;
        this.state = "ready";
        this.lastError = undefined;
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
}

export function fetchForgeXHealth(url: string): Promise<BackendHealthProbeResult> {
  return new Promise((resolve) => {
    const request = http.get(`${url}/health`, { timeout: 2_000 }, (response) => {
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

function isForgeXHealth(body: unknown): body is { service: string; status: string } {
  return typeof body === "object" && body !== null && "service" in body && (body as { service?: unknown }).service === "forgex-backend";
}

function safeLogChunk(chunk: Buffer): string {
  const text = chunk.toString("utf8").trimEnd();
  const capped = text.length > 4_000 ? `${text.slice(0, 4_000)}... [truncated]` : text;
  return capped.replace(/[\r\n]+$/g, "");
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
