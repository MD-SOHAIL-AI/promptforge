import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { pythonCandidates, resolveBackendPython } from "../dist/electron/python-resolver.js";

function okRun() {
  return { ok: true, stdout: "python" };
}

test("FORGEX_PYTHON override wins", () => {
  const result = resolveBackendPython({
    repoRoot: "C:\\repo",
    env: { FORGEX_PYTHON: "C:\\custom\\python.exe" },
    platform: "win32",
    existsSync: (filePath) => filePath === "C:\\custom\\python.exe",
    runCheck: okRun,
  });

  assert.equal(result.executable, "C:\\custom\\python.exe");
  assert.equal(result.source, "FORGEX_PYTHON");
});

test("repository .venv is detected before global fallback", () => {
  const repoRoot = "C:\\repo";
  const venvPython = path.win32.join(repoRoot, ".venv", "Scripts", "python.exe");
  const result = resolveBackendPython({
    repoRoot,
    env: {},
    platform: "win32",
    existsSync: (filePath) => filePath === venvPython,
    runCheck: okRun,
  });

  assert.equal(result.executable, venvPython);
  assert.equal(result.source, "repo-venv");
});

test("Windows .venv Scripts python candidate is resolved", () => {
  const candidates = pythonCandidates("C:\\repo", {}, "win32").map((candidate) => candidate.executable);
  assert.equal(candidates[0], "C:\\repo\\.venv\\Scripts\\python.exe");
});

test("Unix .venv bin python candidate is resolved", () => {
  const candidates = pythonCandidates("/repo", {}, "linux").map((candidate) => candidate.executable);
  assert.equal(candidates[0], "/repo/.venv/bin/python");
});

test("falls back when repository virtual environments are absent", () => {
  const result = resolveBackendPython({
    repoRoot: "/repo",
    env: {},
    platform: "linux",
    existsSync: () => false,
    runCheck: (executable) => executable === "python3" ? okRun() : { ok: false, error: "not found" },
  });

  assert.equal(result.executable, "python3");
  assert.equal(result.source, "fallback");
});

test("continues through global fallback without dependencies", () => {
  let dependencyChecks = 0;
  const result = resolveBackendPython({
    repoRoot: "/repo",
    env: {},
    platform: "linux",
    existsSync: () => false,
    runCheck: (executable, args) => {
      if (args.join(" ").includes("fastapi")) {
        dependencyChecks += 1;
        return executable === "python3" ? { ok: false, stderr: "ModuleNotFoundError: fastapi" } : okRun();
      }
      return okRun();
    },
  });

  assert.equal(result.executable, "python");
  assert.equal(dependencyChecks, 2);
});

test("missing Python produces a clear error", () => {
  assert.throws(
    () => resolveBackendPython({
      repoRoot: "/repo",
      env: {},
      platform: "linux",
      existsSync: () => false,
      runCheck: () => ({ ok: false, error: "not found" }),
    }),
    /No usable Python interpreter was found/,
  );
});

test("present virtual environment with missing dependencies does not silently fall back", () => {
  const venvPython = "/repo/.venv/bin/python";
  let checkCount = 0;
  assert.throws(
    () => resolveBackendPython({
      repoRoot: "/repo",
      env: {},
      platform: "linux",
      existsSync: (filePath) => filePath === venvPython,
      runCheck: () => {
        checkCount += 1;
        return checkCount === 1 ? okRun() : { ok: false, stderr: "ModuleNotFoundError: fastapi" };
      },
    }),
    /ForgeX backend dependencies are not installed/,
  );
});
