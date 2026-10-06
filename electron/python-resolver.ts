import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

export interface PythonResolution {
  executable: string;
  source: "FORGEX_PYTHON" | "repo-venv" | "fallback";
}

export interface PythonResolverOptions {
  repoRoot: string;
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  existsSync?: (filePath: string) => boolean;
  runCheck?: (executable: string, args: string[], cwd: string) => PythonCheckResult;
}

export interface PythonCheckResult {
  ok: boolean;
  stdout?: string;
  stderr?: string;
  error?: string;
}

export interface PythonCandidate {
  executable: string;
  source: PythonResolution["source"];
  required: boolean;
}

const DEPENDENCY_CHECK = "import fastapi, uvicorn";

export function resolveBackendPython(options: PythonResolverOptions): PythonResolution {
  const env = options.env ?? process.env;
  const platform = options.platform ?? process.platform;
  const existsSync = options.existsSync ?? fs.existsSync;
  const candidates = pythonCandidates(options.repoRoot, env, platform);

  for (const candidate of candidates) {
    if (isPathLike(candidate.executable) && !existsSync(candidate.executable)) {
      if (candidate.source === "FORGEX_PYTHON") {
        throw pythonEnvironmentError(candidate.executable, `Python executable was not found: ${candidate.executable}`, "FORGEX_PYTHON points to a Python executable that does not exist.");
      }
      continue;
    }

    const runnable = checkPython(candidate.executable, ["-B", "-c", "import sys; print(sys.executable)"], options.repoRoot, options.runCheck);
    if (!runnable.ok) {
      if (candidate.required || candidate.source === "repo-venv") {
        throw pythonEnvironmentError(
          candidate.executable,
          runnable.error ?? runnable.stderr ?? "Python executable is not runnable.",
          "The repository virtual environment exists but its Python interpreter cannot be executed.",
        );
      }
      continue;
    }

    const dependencies = checkPython(candidate.executable, ["-B", "-c", DEPENDENCY_CHECK], options.repoRoot, options.runCheck);
    if (!dependencies.ok) {
      if (candidate.required || candidate.source === "repo-venv") {
        throw pythonEnvironmentError(
          candidate.executable,
          dependencies.stderr ?? dependencies.error ?? "Required backend dependencies are missing.",
          "ForgeX backend dependencies are not installed in the selected repository environment.",
        );
      }
      continue;
    }

    return {
      executable: candidate.executable,
      source: candidate.source,
    };
  }

  throw new Error(
    [
      "No usable Python interpreter was found for the ForgeX backend.",
      "",
      "Create and install the repository virtual environment:",
      platform === "win32"
        ? "py -3.11 -m venv .venv"
        : "python3 -m venv .venv",
      platform === "win32"
        ? ".venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        : ".venv/bin/python -m pip install -r requirements.txt",
    ].join("\n"),
  );
}

export function pythonCandidates(repoRoot: string, env: NodeJS.ProcessEnv = process.env, platform: NodeJS.Platform = process.platform): PythonCandidate[] {
  const candidates: PythonCandidate[] = [];
  const override = env.FORGEX_PYTHON?.trim();
  if (override) {
    candidates.push({ executable: override, source: "FORGEX_PYTHON", required: true });
  }

  if (platform === "win32") {
    candidates.push(
      { executable: path.win32.join(repoRoot, ".venv", "Scripts", "python.exe"), source: "repo-venv", required: false },
      { executable: path.win32.join(repoRoot, "venv", "Scripts", "python.exe"), source: "repo-venv", required: false },
      { executable: "python", source: "fallback", required: false },
      { executable: "py", source: "fallback", required: false },
    );
  } else {
    candidates.push(
      { executable: path.posix.join(repoRoot, ".venv", "bin", "python"), source: "repo-venv", required: false },
      { executable: path.posix.join(repoRoot, "venv", "bin", "python"), source: "repo-venv", required: false },
      { executable: "python3", source: "fallback", required: false },
      { executable: "python", source: "fallback", required: false },
    );
  }
  return candidates;
}

function checkPython(
  executable: string,
  args: string[],
  cwd: string,
  runCheck: PythonResolverOptions["runCheck"],
): PythonCheckResult {
  if (runCheck) return runCheck(executable, args, cwd);
  const result = spawnSync(executable, args, {
    cwd,
    encoding: "utf8",
    windowsHide: true,
  });
  return {
    ok: result.status === 0,
    stdout: result.stdout || undefined,
    stderr: result.stderr || undefined,
    error: result.error?.message,
  };
}

function pythonEnvironmentError(executable: string, detail: string, summary = "ForgeX backend dependencies are not installed."): Error {
  return new Error(
    [
      summary,
      "",
      `Detected Python: ${executable}`,
      "",
      detail,
      "",
      "Run:",
      `${executable} -m pip install -r requirements.txt`,
    ].join("\n"),
  );
}

function isPathLike(value: string): boolean {
  return value.includes("/") || value.includes("\\") || path.isAbsolute(value);
}
