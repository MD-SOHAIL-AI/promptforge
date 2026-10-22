import { spawn } from "node:child_process";
import path from "node:path";

import type { JsonValue } from "./contracts";

export type SandboxMode = "read-only" | "workspace-write";

export interface SandboxPolicy {
  mode: SandboxMode;
  workspaceRoot: string;
  timeoutMs: number;
  maxOutputBytes: number;
}

export interface SandboxRunner {
  executable: string;
  prefixArgs?: string[];
  enforcement: "full" | "partial";
  deviceIsolation: "verified" | "unknown";
}

const HARDWARE_MARKERS = [
  "esptool",
  "avrdude",
  "openocd",
  "dfu-util",
  "stm32cubeprogrammer",
  "bossac",
  "upload",
  "erase_flash",
  "write_flash",
  "debug_attach",
];

export class SandboxUnavailableError extends Error {
  constructor(readonly code = "SANDBOX_UNAVAILABLE") {
    super("A fully enforcing, device-isolated sandbox is unavailable.");
    this.name = "SandboxUnavailableError";
  }
}

export class SandboxedTerminal {
  constructor(private readonly runner: SandboxRunner | null) {}

  available(): boolean {
    return this.runner?.enforcement === "full" && this.runner.deviceIsolation === "verified";
  }

  async execute(argv: readonly string[], policy: SandboxPolicy, signal?: AbortSignal): Promise<JsonValue> {
    if (!this.available() || !this.runner) throw new SandboxUnavailableError();
    const root = path.resolve(policy.workspaceRoot);
    if (!path.isAbsolute(root) || !argv.length || argv.length > 128) throw new Error("SANDBOX_REQUEST_INVALID");
    if (argv.some((item) => !item || item.length > 8_192 || item.includes("\0"))) throw new Error("SANDBOX_ARGV_INVALID");
    assertNoHardwareBypass(argv);
    const timeoutMs = Math.max(1_000, Math.min(policy.timeoutMs, 600_000));
    const maxOutput = Math.max(1_024, Math.min(policy.maxOutputBytes, 4 * 1024 * 1024));
    const wrapped = [
      ...(this.runner.prefixArgs ?? []),
      "--mode", policy.mode,
      "--workspace", root,
      "--deny-devices",
      "--",
      ...argv,
    ];
    return await new Promise<JsonValue>((resolve, reject) => {
      const child = spawn(this.runner!.executable, wrapped, {
        cwd: root,
        windowsHide: true,
        shell: false,
        env: minimalEnvironment(),
      });
      const stdout: Buffer[] = [];
      const stderr: Buffer[] = [];
      let bytes = 0;
      let settled = false;
      const finish = (value: JsonValue, error?: Error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
        if (error) reject(error); else resolve(value);
      };
      const collect = (target: Buffer[]) => (chunk: Buffer) => {
        bytes += chunk.length;
        if (bytes > maxOutput) {
          child.kill("SIGKILL");
          finish(null, new Error("SANDBOX_OUTPUT_LIMIT"));
          return;
        }
        target.push(chunk);
      };
      child.stdout.on("data", collect(stdout));
      child.stderr.on("data", collect(stderr));
      child.once("error", (error) => finish(null, error));
      child.once("exit", (code, exitSignal) => finish({
        exitCode: code,
        signal: exitSignal,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
      }));
      const abort = () => { child.kill("SIGKILL"); finish(null, new Error("SANDBOX_CANCELLED")); };
      signal?.addEventListener("abort", abort, { once: true });
      const timer = setTimeout(() => { child.kill("SIGKILL"); finish(null, new Error("SANDBOX_TIMEOUT")); }, timeoutMs);
    });
  }
}

export function assertNoHardwareBypass(argv: readonly string[]): void {
  const normalized = argv.join(" ").toLocaleLowerCase();
  if (
    HARDWARE_MARKERS.some((marker) => normalized.includes(marker))
    || /(?:^|[^a-z])com\d+(?:$|[^0-9])/i.test(normalized)
    || normalized.includes("\\\\.\\com")
    || normalized.includes("/dev/tty")
    || normalized.includes("/dev/cu.")
  ) throw new Error("HARDWARE_COMMAND_REQUIRES_TYPED_TOOL");
}

function minimalEnvironment(): NodeJS.ProcessEnv {
  const allowed = ["PATH", "Path", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG"];
  return Object.fromEntries(allowed.flatMap((key) => process.env[key] ? [[key, process.env[key]]] : []));
}
