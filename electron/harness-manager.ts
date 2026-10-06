import { desktopLog } from "./desktop-logging";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import http from "node:http";

import { resolveHarnessDataDir, resolveHarnessEntry, resolveHarnessPort, resolveHarnessUrl } from "./paths";

export type HarnessState = "stopped" | "starting" | "ready" | "crashed";

export interface HarnessStatus {
  state: HarnessState;
  url: string;
  port: number;
  ownsProcess: boolean;
  pid?: number;
  lastError?: string;
  health?: unknown;
}

interface HarnessManagerOptions {
  port?: number;
  token: string;
  entry?: string;
  dataDir?: string;
  readinessTimeoutMs?: number;
  readinessIntervalMs?: number;
  executable?: string;
  spawnHarness?: typeof spawn;
  healthProbe?: (url: string, token: string) => Promise<{ reachable: boolean; healthy: boolean; body?: unknown; error?: string }>;
}

export class HarnessManager {
  private process: ChildProcessWithoutNullStreams | null = null;
  private state: HarnessState = "stopped";
  private lastError: string | undefined;
  private health: unknown;
  private ownsProcess = false;
  private readonly port: number;
  private readonly token: string;
  private readonly entry: string;
  private readonly dataDir: string;
  private readonly timeoutMs: number;
  private readonly intervalMs: number;
  private readonly executable: string;
  private readonly spawnHarness: typeof spawn;
  private readonly healthProbe: NonNullable<HarnessManagerOptions["healthProbe"]>;

  constructor(options: HarnessManagerOptions) {
    if (options.token.trim().length < 32) throw new Error("Harness token must contain at least 32 characters.");
    this.port = options.port ?? resolveHarnessPort();
    this.token = options.token.trim();
    this.entry = options.entry ?? resolveHarnessEntry();
    this.dataDir = options.dataDir ?? resolveHarnessDataDir();
    this.timeoutMs = options.readinessTimeoutMs ?? 20_000;
    this.intervalMs = options.readinessIntervalMs ?? 250;
    this.executable = options.executable ?? process.execPath;
    this.spawnHarness = options.spawnHarness ?? spawn;
    this.healthProbe = options.healthProbe ?? probeHarness;
  }

  get url(): string { return resolveHarnessUrl(this.port); }

  async start(): Promise<HarnessStatus> {
    this.state = "starting";
    this.lastError = undefined;
    const existing = await this.healthProbe(this.url, this.token);
    if (existing.reachable && existing.healthy) {
      this.state = "ready";
      this.health = existing.body;
      return this.getStatus();
    }
    try {
      this.process = this.spawnHarness(this.executable, [this.entry], {
        env: {
          ...process.env,
          ELECTRON_RUN_AS_NODE: "1",
          FORGEX_HARNESS_PORT: String(this.port),
          FORGEX_HARNESS_TOKEN: this.token,
          FORGEX_HARNESS_DATA_DIR: this.dataDir,
        },
        windowsHide: true,
      });
      this.ownsProcess = true;
    } catch (error) {
      this.fail(`Failed to start Forge Harness: ${message(error)}`);
      throw new Error(this.lastError);
    }
    this.process.stdout.on("data", (chunk: Buffer) => desktopLog.log(`[forgex-harness] ${safe(chunk)}`));
    this.process.stderr.on("data", (chunk: Buffer) => desktopLog.error(`[forgex-harness] ${safe(chunk)}`));
    this.process.once("error", (error) => this.fail(error.message));
    this.process.once("exit", (code, signal) => {
      if (this.state !== "stopped") this.fail(`Harness exited with code ${code ?? "null"} and signal ${signal ?? "null"}`);
      this.process = null;
      this.ownsProcess = false;
    });
    const deadline = Date.now() + this.timeoutMs;
    while (Date.now() < deadline) {
      const result = await this.healthProbe(this.url, this.token);
      if (result.healthy) {
        this.state = "ready";
        this.health = result.body;
        return this.getStatus();
      }
      if (this.getStatus().state === "crashed") break;
      await new Promise((resolve) => setTimeout(resolve, this.intervalMs));
    }
    const reason = this.lastError ?? `Harness did not become healthy within ${this.timeoutMs}ms.`;
    await this.stop();
    this.fail(reason);
    throw new Error(reason);
  }

  async stop(): Promise<void> {
    this.state = "stopped";
    const child = this.process;
    this.process = null;
    if (!child || !this.ownsProcess) {
      this.ownsProcess = false;
      return;
    }
    this.ownsProcess = false;
    await new Promise<void>((resolve) => {
      const timeout = setTimeout(() => { if (!child.killed) child.kill("SIGKILL"); resolve(); }, 5_000);
      child.once("exit", () => { clearTimeout(timeout); resolve(); });
      if (!child.kill("SIGTERM")) { clearTimeout(timeout); resolve(); }
    });
  }

  getStatus(): HarnessStatus {
    return { state: this.state, url: this.url, port: this.port, ownsProcess: this.ownsProcess, pid: this.process?.pid, lastError: this.lastError, health: this.health };
  }

  private fail(reason: string): void {
    this.state = "crashed";
    this.lastError = reason;
  }
}

export function probeHarness(url: string, token: string): Promise<{ reachable: boolean; healthy: boolean; body?: unknown; error?: string }> {
  return new Promise((resolve) => {
    const request = http.get(`${url}/health`, { timeout: 2_000, headers: { Authorization: `Bearer ${token}` } }, (response) => {
      const chunks: Buffer[] = [];
      response.on("data", (chunk: Buffer) => chunks.push(chunk));
      response.on("end", () => {
        try {
          const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          resolve({ reachable: true, healthy: response.statusCode === 200 && body?.service === "forgex-harness" && body?.status === "healthy", body });
        } catch { resolve({ reachable: true, healthy: false, error: "Invalid harness health response" }); }
      });
    });
    request.once("timeout", () => request.destroy(new Error("timeout")));
    request.once("error", (error: NodeJS.ErrnoException) => resolve({ reachable: false, healthy: false, error: error.code ?? error.message }));
  });
}

function safe(chunk: Buffer): string { const value = chunk.toString("utf8").trim(); return value.length > 4_000 ? `${value.slice(0, 4_000)}…` : value; }
function message(error: unknown): string { return error instanceof Error ? error.message : String(error); }
