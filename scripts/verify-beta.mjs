import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const BETA_CHECKS = Object.freeze([
  Object.freeze({ id: "python", label: "Python tests", runner: "python", args: ["-m", "pytest", "-q"] }),
  Object.freeze({ id: "harness", label: "Harness tests", runner: "npm", args: ["run", "test:harness"] }),
  Object.freeze({ id: "ui-state", label: "Frontend state tests", runner: "npm", args: ["run", "test:ui-state"] }),
  Object.freeze({ id: "electron", label: "Electron tests", runner: "npm", args: ["run", "test:electron"] }),
  Object.freeze({ id: "typecheck", label: "Frontend typecheck", runner: "npm", args: ["run", "typecheck:frontend"] }),
  Object.freeze({ id: "lint", label: "Frontend lint (zero warnings)", runner: "npm", args: ["run", "lint:frontend"] }),
]);

export class VerificationError extends Error {
  constructor(check, exitCode) {
    super(`${check.label} failed with exit code ${exitCode}`);
    this.name = "VerificationError";
    this.check = check;
    this.exitCode = exitCode;
  }
}

export function resolvePythonExecutable(cwd, env = process.env, platform = process.platform) {
  const configured = env.FORGEX_PYTHON?.trim() || env.PYTHON?.trim();
  if (configured) {
    return configured;
  }

  const virtualEnvironmentPython = platform === "win32"
    ? path.join(cwd, ".venv", "Scripts", "python.exe")
    : path.join(cwd, ".venv", "bin", "python");
  if (existsSync(virtualEnvironmentPython)) {
    return virtualEnvironmentPython;
  }
  return platform === "win32" ? "python" : "python3";
}

export function resolveCheckCommand(check, { cwd, env = process.env, platform = process.platform }) {
  if (check.runner === "python") {
    return { command: resolvePythonExecutable(cwd, env, platform), args: [...check.args] };
  }
  if (check.runner === "npm") {
    if (env.npm_execpath) {
      return { command: process.execPath, args: [env.npm_execpath, ...check.args] };
    }
    return { command: platform === "win32" ? "npm.cmd" : "npm", args: [...check.args] };
  }
  throw new Error(`Unknown beta-check runner: ${check.runner}`);
}

export function runCommand(command, args, { cwd, env = process.env } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      env,
      stdio: "inherit",
      windowsHide: true,
    });
    child.once("error", reject);
    child.once("close", (code, signal) => {
      if (signal) {
        reject(new Error(`command terminated by signal ${signal}`));
        return;
      }
      resolve(code ?? 1);
    });
  });
}

export async function runBetaVerification({
  checks = BETA_CHECKS,
  cwd = process.cwd(),
  env = process.env,
  platform = process.platform,
  execute = runCommand,
  output = process.stdout,
} = {}) {
  output.write(`ForgeX production-beta verification (${checks.length} checks)\n`);
  for (const [index, check] of checks.entries()) {
    output.write(`\n[${index + 1}/${checks.length}] ${check.label}\n`);
    const invocation = resolveCheckCommand(check, { cwd, env, platform });
    const exitCode = await execute(invocation.command, invocation.args, { cwd, env });
    if (exitCode !== 0) {
      throw new VerificationError(check, exitCode);
    }
  }
  output.write(`\nProduction-beta verification passed (${checks.length}/${checks.length}).\n`);
  return { passed: checks.length, total: checks.length };
}

function isMainModule() {
  return process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
}

if (isMainModule()) {
  try {
    await runBetaVerification();
  } catch (error) {
    console.error(`\nProduction-beta verification failed: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  }
}
