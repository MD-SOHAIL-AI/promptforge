import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import http from "node:http";
import path from "node:path";

import { resolveFrontendPort, resolveFrontendServerEntry, resolveFrontendUrl } from "./paths";

export type FrontendState = "stopped" | "starting" | "ready" | "crashed";

export interface FrontendStatus {
  state: FrontendState;
  url: string;
  port: number;
  ownsProcess: boolean;
  pid?: number;
  lastError?: string;
}

export interface FrontendProbeResult {
  reachable: boolean;
  forgeX: boolean;
  statusCode?: number;
  error?: string;
}

interface FrontendManagerOptions {
  port?: number;
  serverEntry?: string;
  readinessTimeoutMs?: number;
  readinessIntervalMs?: number;
  probe?: (url: string) => Promise<FrontendProbeResult>;
  spawnFrontend?: typeof spawn;
  executable?: string;
}

export class FrontendManager {
  private process: ChildProcessWithoutNullStreams | null = null;
  private state: FrontendState = "stopped";
  private lastError: string | undefined;
  private readonly port: number;
  private readonly serverEntry: string;
  private readonly readinessTimeoutMs: number;
  private readonly readinessIntervalMs: number;
  private readonly probe: (url: string) => Promise<FrontendProbeResult>;
  private readonly spawnFrontend: typeof spawn;
  private readonly executable: string;
  private ownsProcess = false;

  constructor(options: FrontendManagerOptions = {}) {
    this.port = options.port ?? resolveFrontendPort();
    this.serverEntry = options.serverEntry ?? resolveFrontendServerEntry();
    this.readinessTimeoutMs = options.readinessTimeoutMs ?? 30_000;
    this.readinessIntervalMs = options.readinessIntervalMs ?? 300;
    this.probe = options.probe ?? probeForgeXFrontend;
    this.spawnFrontend = options.spawnFrontend ?? spawn;
    this.executable = options.executable ?? process.execPath;
  }

  get url(): string {
    return (process.env.FORGEX_FRONTEND_URL ?? `http://127.0.0.1:${this.port}`).replace(/\/$/, "");
  }

  async start(): Promise<FrontendStatus> {
    const existing = await this.probe(this.url);
    if (existing.reachable) {
      if (!existing.forgeX) {
        this.state = "crashed";
        this.lastError = `Frontend port ${this.port} is already in use by another service.`;
        throw new Error(this.lastError);
      }
      this.state = "ready";
      this.ownsProcess = false;
      return this.getStatus();
    }

    this.state = "starting";
    this.lastError = undefined;
    this.ownsProcess = true;
    this.process = this.spawnFrontend(this.executable, [this.serverEntry], {
      cwd: path.dirname(this.serverEntry),
      env: {
        ...process.env,
        ELECTRON_RUN_AS_NODE: "1",
        HOSTNAME: "127.0.0.1",
        PORT: String(this.port),
        NEXT_TELEMETRY_DISABLED: "1",
      },
      windowsHide: true,
    });
    console.log(`[forgex-frontend] Spawned frontend pid=${this.process.pid ?? "unknown"} port=${this.port}`);
    this.process.stdout.on("data", (chunk: Buffer) => console.log(`[forgex-frontend] ${safeLogChunk(chunk)}`));
    this.process.stderr.on("data", (chunk: Buffer) => console.error(`[forgex-frontend] ${safeLogChunk(chunk)}`));
    this.process.on("exit", (code, signal) => {
      if (this.state !== "stopped") {
        this.state = "crashed";
        this.lastError = `Frontend exited with code ${code ?? "null"} and signal ${signal ?? "null"}`;
      }
      this.process = null;
    });
    this.process.on("error", (error) => {
      this.state = "crashed";
      this.lastError = error.message;
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
        if (!child.killed) child.kill("SIGKILL");
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

  getStatus(): FrontendStatus {
    return {
      state: this.state,
      url: this.url,
      port: this.port,
      ownsProcess: this.ownsProcess,
      pid: this.process?.pid,
      lastError: this.lastError,
    };
  }

  private async waitForReady(): Promise<void> {
    const deadline = Date.now() + this.readinessTimeoutMs;
    while (Date.now() < deadline) {
      if (this.state === "crashed" && !this.process) {
        throw new Error(this.lastError ?? "Frontend exited before it became ready.");
      }
      const result = await this.probe(this.url);
      if (result.reachable && result.forgeX) {
        this.state = "ready";
        this.lastError = undefined;
        return;
      }
      await delay(this.readinessIntervalMs);
    }
    this.state = "crashed";
    this.lastError = `Frontend did not become ready within ${this.readinessTimeoutMs}ms.`;
    throw new Error(this.lastError);
  }
}

export function probeForgeXFrontend(url: string): Promise<FrontendProbeResult> {
  return new Promise((resolve) => {
    const request = http.get(url, { timeout: 2_000 }, (response) => {
      const chunks: Buffer[] = [];
      response.on("data", (chunk: Buffer) => chunks.push(chunk));
      response.on("end", () => {
        const body = Buffer.concat(chunks).toString("utf8");
        resolve({
          reachable: true,
          forgeX: body.includes("ForgeX Embedded Engineering Environment"),
          statusCode: response.statusCode,
        });
      });
    });
    request.on("timeout", () => {
      request.destroy();
      resolve({ reachable: false, forgeX: false, error: "Frontend request timed out" });
    });
    request.on("error", (error: NodeJS.ErrnoException) => {
      resolve({ reachable: false, forgeX: false, error: error.code ?? "Frontend unavailable" });
    });
  });
}

function safeLogChunk(chunk: Buffer): string {
  const text = chunk.toString("utf8").trim();
  return text.length > 2_000 ? `${text.slice(0, 2_000)}... [truncated]` : text;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
