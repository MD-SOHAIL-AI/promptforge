import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  assertSanitizedPublicPayload,
  baselineDigest,
  compareBaselines,
  detectInstalledVersion,
  fastCompletionClassification,
  flagSupportMissing,
  guardThrowawayWorkspace,
  locateAgy,
  missingLiveFlags,
  orderedEvents,
  packageJsonCandidates,
  resolveCommandOnPath,
  selectReadinessStatus,
  snapshotWorkspace,
  terminalEventCount,
} from "./qa-agy-generic-live-core.mjs";

const FLAGS = {
  FORGEX_QA_MODE: "1",
  FORGEX_ENABLE_AGY_BRIDGE: "1",
  FORGEX_ENABLE_GENERIC_BRIDGE_API: "1",
  FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING: "1",
  FORGEX_ENABLE_AGY_GENERIC_PROVIDER: "1",
  FORGEX_ENABLE_AGY_GENERIC_CUTOVER: "1",
  FORGEX_ENABLE_AGY_TRUSTED_WORKSPACE: "1",
};

test("live flag gate fails closed and accepts only the complete matrix", () => {
  assert.deepEqual(missingLiveFlags(FLAGS), []);
  assert.deepEqual(missingLiveFlags({ ...FLAGS, FORGEX_QA_MODE: "0" }), ["FORGEX_QA_MODE"]);
  assert.deepEqual(flagSupportMissing(Object.keys(FLAGS).join("\n")), []);
});

test("check-only executable lookup invokes the locator and never AGY", () => {
  const calls = [];
  const fakeSpawn = (command, args, options) => {
    calls.push({ command, args, options });
    return { status: 0, stdout: process.platform === "win32" ? "C:\\qa\\agy.cmd\n" : "/qa/agy\n" };
  };
  const result = locateAgy(fakeSpawn);
  assert.equal(result.installed, true);
  assert.equal(calls.length, 1);
  assert.match(calls[0].command, /^(where\.exe|which)$/);
  assert.equal(calls[0].options.shell, false);
});

test("missing AGY installation is safely classified without executing a provider", () => {
  const calls = [];
  const result = locateAgy((command, args) => {
    calls.push([command, ...args]);
    return { status: 1, stdout: "" };
  }, process.platform, { PATH: "", PATHEXT: ".EXE" });
  assert.equal(result.installed, false);
  assert.equal(calls.length, 2);
});

test("AGY locator can use a bounded PATH scan when the system locator misses an application", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-agy-path-"));
  const executable = path.join(root, process.platform === "win32" ? "agy.exe" : "agy");
  fs.writeFileSync(executable, "fixture");
  const env = { PATH: root, PATHEXT: ".EXE" };
  assert.equal(resolveCommandOnPath("agy", env).toLowerCase(), path.resolve(executable).toLowerCase());
});

test("version detection uses a fixed non-shell version probe when metadata is absent", () => {
  const calls = [];
  const version = detectInstalledVersion("C:\\qa\\agy.exe", (command, args, options) => {
    calls.push({ command, args, options });
    if (command === "powershell.exe") return { status: 0, stdout: "" };
    return { status: 0, stdout: "1.0.14\n" };
  }, "win32");
  assert.equal(version, "1.0.14");
  assert.equal(calls.length, 2);
  assert.equal(calls[1].command, "C:\\qa\\agy.exe");
  assert.deepEqual(calls[1].args, ["--version"]);
  assert.equal(calls[1].options.shell, false);
});

test("Windows version metadata candidates use Windows path semantics", () => {
  const candidates = packageJsonCandidates("C:\\qa\\agy.exe", "win32");
  assert.equal(candidates[0], "C:\\qa\\package.json");
  assert.equal(candidates[1], "C:\\qa\\node_modules\\agy\\package.json");
  assert.equal(candidates.includes(path.resolve("package.json")), false);
});

test("POSIX version metadata candidates and AGY package metadata remain supported", () => {
  const candidates = packageJsonCandidates("/usr/local/bin/agy", "linux");
  assert.equal(candidates[0], "/usr/local/bin/package.json");
  assert.equal(candidates[1], "/usr/local/bin/node_modules/agy/package.json");

  const root = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-agy-version-"));
  const executable = path.join(root, "bin", "agy");
  fs.mkdirSync(path.dirname(executable), { recursive: true });
  fs.writeFileSync(executable, "fixture");
  fs.writeFileSync(
    path.join(root, "bin", "package.json"),
    JSON.stringify({ name: "@google/antigravity", version: "1.2.3" }),
  );
  assert.equal(detectInstalledVersion(executable, () => {
    throw new Error("metadata should avoid command probing");
  }, process.platform), "1.2.3");
});

test("version detection ignores unrelated package metadata", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-agy-version-"));
  const executable = path.join(root, "bin", "agy");
  fs.mkdirSync(path.dirname(executable), { recursive: true });
  fs.writeFileSync(executable, "fixture");
  fs.writeFileSync(
    path.join(root, "bin", "package.json"),
    JSON.stringify({ name: "forgex", version: "0.1.0" }),
  );
  const calls = [];
  const version = detectInstalledVersion(executable, (command, args, options) => {
    calls.push({ command, args, options });
    return { status: 0, stdout: "1.0.14\n" };
  }, process.platform);
  assert.equal(version, "1.0.14");
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].args, ["--version"]);
  assert.equal(calls[0].options.shell, false);
});

test("readiness classification safely distinguishes installation authentication and backend blockers", () => {
  const base = {
    workspaceOk: true,
    installed: true,
    authenticated: true,
    version: "1.0.0",
    backendReady: true,
  };
  assert.equal(selectReadinessStatus({ ...base, installed: false }).status, "BLOCKED_AGY_NOT_INSTALLED");
  assert.equal(selectReadinessStatus({ ...base, authenticated: false }).status, "BLOCKED_AGY_NOT_AUTHENTICATED");
  assert.equal(selectReadinessStatus({ ...base, backendReady: false }).status, "BLOCKED_UNKNOWN_SAFE_REASON");
  assert.equal(selectReadinessStatus({ ...base, trustedWorkspacePrepared: false }).status, "BLOCKED_TRUSTED_WORKSPACE_NOT_PREPARED");
  assert.equal(selectReadinessStatus({ ...base, trustAttested: false }).status, "BLOCKED_TRUSTED_WORKSPACE_NOT_ATTESTED");
  assert.equal(selectReadinessStatus(base).status, "READY");
});

test("throwaway guard accepts only the exact marked workspace", () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-agy-live-"));
  const workspace = path.join(repo, "workspace", "forgex-apply-test");
  fs.mkdirSync(workspace, { recursive: true });
  fs.writeFileSync(path.join(workspace, "QA_NOT_REAL_PROJECT.txt"), "QA only\n");
  assert.equal(guardThrowawayWorkspace(repo, workspace).ok, true);
  assert.equal(guardThrowawayWorkspace(repo, repo).ok, false);
});

test("missing throwaway marker blocks the workspace", () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-agy-live-"));
  const workspace = path.join(repo, "workspace", "forgex-apply-test");
  fs.mkdirSync(workspace, { recursive: true });
  assert.deepEqual(guardThrowawayWorkspace(repo, workspace), { ok: false, reason: "marker_missing" });
});

test("active workspace baseline detects every tracked file mutation", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-agy-baseline-"));
  fs.writeFileSync(path.join(root, "QA_NOT_REAL_PROJECT.txt"), "QA\n");
  fs.mkdirSync(path.join(root, "src"));
  fs.writeFileSync(path.join(root, "src", "main.cpp"), "base\n");
  const before = snapshotWorkspace(root);
  assert.equal(compareBaselines(before, snapshotWorkspace(root)), true);
  fs.writeFileSync(path.join(root, "src", "main.cpp"), "changed\n");
  const after = snapshotWorkspace(root);
  assert.equal(compareBaselines(before, after), false);
  assert.notEqual(baselineDigest(before), baselineDigest(after));
  assert.ok(before.every((item) => !path.isAbsolute(item.relative_path)));
});

test("public payload validator rejects prompt output commands environment and paths", () => {
  assert.equal(assertSanitizedPublicPayload({ run_id: "qa-run", provider_id: "agy", status: "completed" }), true);
  for (const key of ["instruction", "prompt", "stdout_preview", "stderr_preview", "command", "executable_path", "environment", "workspace_root", "sandbox_root"]) {
    assert.throws(() => assertSanitizedPublicPayload({ [key]: "unsafe" }));
  }
});

test("event validation rejects duplicates and duplicate terminal events", () => {
  const events = [
    { sequence: 1, status: "running" },
    { sequence: 2, status: "completed" },
  ];
  assert.equal(orderedEvents(events), true);
  assert.equal(terminalEventCount(events), 1);
  assert.equal(orderedEvents([...events, { sequence: 2, status: "completed" }]), false);
  assert.equal(terminalEventCount([...events, { sequence: 3, status: "failed" }]), 2);
});

test("cancellation and timeout fast completion are blocked rather than failed", () => {
  assert.equal(fastCompletionClassification("cancelled", "cancelled"), "PASS");
  assert.equal(fastCompletionClassification("completed", "cancelled"), "BLOCKED_FAST_COMPLETION");
  assert.equal(fastCompletionClassification("completed", "timed_out"), "BLOCKED_FAST_COMPLETION");
  assert.equal(fastCompletionClassification("failed", "timed_out"), "FAIL");
});
