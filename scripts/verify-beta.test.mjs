import assert from "node:assert/strict";
import test from "node:test";

import {
  BETA_CHECKS,
  VerificationError,
  resolveCheckCommand,
  resolvePythonExecutable,
  runBetaVerification,
} from "./verify-beta.mjs";

const silentOutput = () => ({ write() {} });

test("the beta gate contains every required software check in release order", () => {
  assert.deepEqual(
    BETA_CHECKS.map((check) => check.id),
    ["harness", "python", "ui-state", "read-recovery", "electron", "typecheck", "lint", "conversations"],
  );
});

test("configured Python and npm executables are honored cross-platform", () => {
  assert.equal(resolvePythonExecutable("C:\\repo", { FORGEX_PYTHON: "custom-python" }, "win32"), "custom-python");
  const invocation = resolveCheckCommand(BETA_CHECKS[2], {
    cwd: "/repo",
    env: { npm_execpath: "/tools/npm-cli.js" },
    platform: "linux",
  });
  assert.equal(invocation.command, process.execPath);
  assert.deepEqual(invocation.args, ["/tools/npm-cli.js", "run", "test:ui-state"]);
});

test("the verifier runs all checks without cleanup or shell indirection", async () => {
  const invocations = [];
  const result = await runBetaVerification({
    cwd: process.cwd(),
    env: { FORGEX_PYTHON: "python", npm_execpath: "/tools/npm-cli.js" },
    execute: async (command, args) => {
      invocations.push({ command, args });
      return 0;
    },
    output: silentOutput(),
  });

  assert.deepEqual(result, { passed: 8, total: 8 });
  assert.equal(invocations.length, 8);
  assert.deepEqual(invocations[0], { command: process.execPath, args: ["/tools/npm-cli.js", "run", "test:harness"] });
  assert.deepEqual(invocations[1], { command: "python", args: ["-m", "pytest", "-q"] });
  assert.equal(invocations.some(({ args }) => args.includes("cleanup")), false);
});

test("the verifier stops immediately at the first failing gate", async () => {
  const checks = BETA_CHECKS.slice(0, 3);
  let calls = 0;
  await assert.rejects(
    runBetaVerification({
      checks,
      cwd: process.cwd(),
      env: { FORGEX_PYTHON: "python", npm_execpath: "/tools/npm-cli.js" },
      execute: async () => {
        calls += 1;
        return calls === 2 ? 7 : 0;
      },
      output: silentOutput(),
    }),
    (error) => error instanceof VerificationError && error.check.id === "python" && error.exitCode === 7,
  );
  assert.equal(calls, 2);
});
