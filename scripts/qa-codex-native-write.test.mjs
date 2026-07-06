import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

import {
  CODEX_MARKER, CODEX_MARKER_CONTENT, CODEX_SMOKE_CONTENT, CODEX_SMOKE_NAME,
  DANGEROUS_CODEX_FLAGS, OMITTED_SUBSCRIPTION_RETRY_FLAGS, assertSafeCodexArgs,
  buildCodexArgs, classifyCodexNative, compareSnapshots, createManagedSandbox,
  guardManagedSandbox, invocationMatrix, manualStandaloneAttested,
  resolveCodexLauncher, sanitizedResult, snapshotTree,
} from "./qa-codex-native-write-core.mjs";

function fixture() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-codex-test-"));
  const repo = path.join(base, "repo");
  const active = path.join(repo, "workspace", "forgex-apply-test");
  const managed = path.join(base, "external-codex-sandboxes");
  fs.mkdirSync(active, { recursive: true });
  fs.writeFileSync(path.join(active, "QA_NOT_REAL_PROJECT.txt"), "fixture\n");
  const env = { USERPROFILE: path.join(base, "unrelated-home") };
  return { base, repo, active, managed, env };
}
function makeSandbox(runId = "codex-0123456789abcdef") {
  const value = fixture();
  return { ...value, guarded: createManagedSandbox(value.repo, runId, value.active, value.env, value.managed) };
}
function diff(created = [], modified = [], deleted = []) { return { created, modified, deleted, changedCount: created.length + modified.length + deleted.length }; }
function result(overrides = {}) { return { status: 0, signal: null, errorCode: null, stdout: "", stderr: "", diff: diff(), smokeExists: false, smokeMatches: false, markerUnchanged: true, activeUnchanged: true, ...overrides }; }

test("builder uses exact global approval exec sandbox cd positional shape", () => {
  const sandbox = "C:\\forgex-codex-sandboxes\\codex-0123456789abcdef";
  const args = buildCodexArgs(sandbox, "runtime-only");
  assert.deepEqual(args, ["--ask-for-approval", "never", "exec", "--sandbox", "workspace-write", "--cd", sandbox, "runtime-only"]);
  assert.equal(assertSafeCodexArgs(args, sandbox), true);
});
test("builder omits unsupported subscription retry extras", () => {
  const args = buildCodexArgs("C:\\safe", "runtime-only");
  for (const flag of OMITTED_SUBSCRIPTION_RETRY_FLAGS) assert.equal(args.includes(flag), false);
});
test("Codex detection handles a missing CLI", () => assert.equal(resolveCodexLauncher({ PATH: "" }, "win32"), null));
test("builder blocks every dangerous bypass flag", () => {
  for (const flag of DANGEROUS_CODEX_FLAGS) assert.throws(() => assertSafeCodexArgs([flag], "C:\\safe"), /dangerous/);
});
test("builder blocks interactive approval policies", () => {
  for (const value of ["on-failure", "on-request"]) {
    const args = buildCodexArgs("C:\\safe", "runtime-only"); args[1] = value;
    assert.throws(() => assertSafeCodexArgs(args, "C:\\safe"), /unsafe|invocation/);
  }
});
test("invocation matrix exposes only official exec subscription bridge", () => {
  const matrix = invocationMatrix();
  assert.equal(matrix.filter((item) => item.safe_to_test).length, 1);
  assert.equal(matrix[0].variant, "codex_exec_subscription_bridge");
  assert.doesNotMatch(JSON.stringify(matrix), /stdout|stderr|absolute_path|prompt/i);
});
test("manual standalone proof must be documented", () => {
  const { base } = fixture(); const doc = path.join(base, "result.md");
  fs.writeFileSync(doc, "CODEX_STANDALONE_WRITE_PASS\ncodex --ask-for-approval never exec --sandbox workspace-write --cd C:\\forgex-codex-smoke\n");
  assert.equal(manualStandaloneAttested(doc), true);
  fs.writeFileSync(doc, "pending\n"); assert.equal(manualStandaloneAttested(doc), false);
});
test("external managed sandbox starts with exactly the marker", () => {
  const { guarded } = makeSandbox();
  assert.deepEqual(snapshotTree(guarded.sandboxRoot).map((item) => item.relative_path), [CODEX_MARKER]);
  assert.equal(fs.readFileSync(path.join(guarded.sandboxRoot, CODEX_MARKER), "utf8"), CODEX_MARKER_CONTENT);
});
test("managed sandbox is a direct child of external root", () => {
  const { managed, guarded } = makeSandbox(); assert.equal(path.dirname(guarded.sandboxRoot), fs.realpathSync.native(managed));
});
test("managed sandbox refuses repository root", () => {
  const { repo, active, managed, env } = fixture(); fs.mkdirSync(managed);
  assert.throws(() => guardManagedSandbox(repo, repo, active, env, managed), /containment|repository/);
});
test("managed sandbox refuses active workspace", () => {
  const { repo, active, managed, env } = fixture(); fs.mkdirSync(managed);
  assert.throws(() => guardManagedSandbox(repo, active, active, env, managed), /containment|active/);
});
test("managed root refuses home Desktop OneDrive and filesystem roots", () => {
  const { repo, active, base } = fixture();
  for (const unsafe of [base, path.join(base, "Desktop"), path.parse(repo).root]) {
    if (!fs.existsSync(unsafe)) fs.mkdirSync(unsafe, { recursive: true });
    assert.throws(() => createManagedSandbox(repo, "codex-fedcba9876543210", active, { USERPROFILE: base, OneDrive: base }, unsafe), /sensitive|repository/);
  }
});
test("managed sandbox refuses symlink or reparse entries", (t) => {
  const { active, guarded, repo, env, managed } = makeSandbox("codex-fedcba9876543210");
  try { fs.symlinkSync(active, path.join(guarded.sandboxRoot, "escape"), "junction"); }
  catch { t.skip("host does not permit link creation"); return; }
  assert.throws(() => guardManagedSandbox(repo, guarded.sandboxRoot, active, env, managed), /link|reparse/);
});
test("relative hash snapshots detect created modified deleted", () => {
  const { active } = fixture(); const before = snapshotTree(active);
  fs.writeFileSync(path.join(active, "new.txt"), "new\n"); fs.writeFileSync(path.join(active, "QA_NOT_REAL_PROJECT.txt"), "changed\n");
  const middle = snapshotTree(active); fs.unlinkSync(path.join(active, "new.txt")); const after = snapshotTree(active);
  assert.deepEqual(compareSnapshots(before, middle).created, ["new.txt"]); assert.deepEqual(compareSnapshots(before, middle).modified, ["QA_NOT_REAL_PROJECT.txt"]); assert.deepEqual(compareSnapshots(middle, after).deleted, ["new.txt"]);
});
test("fake Codex exact one-file output passes", () => assert.equal(classifyCodexNative(result({ diff: diff([CODEX_SMOKE_NAME]), smokeExists: true, smokeMatches: true })), "CODEX_SUBSCRIPTION_BRIDGE_PASS"));
test("fake Codex no changes is classified", () => assert.equal(classifyCodexNative(result()), "CODEX_NATIVE_NO_CHANGES"));
test("fake Codex extra changes are classified", () => assert.equal(classifyCodexNative(result({ diff: diff(["EXTRA.txt"]) })), "CODEX_NATIVE_EXTRA_CHANGES"));
test("fake Codex invalid content is classified", () => assert.equal(classifyCodexNative(result({ diff: diff([CODEX_SMOKE_NAME]), smokeExists: true })), "CODEX_NATIVE_CONTENT_INVALID"));
test("fake Codex usage limit is classified", () => assert.equal(classifyCodexNative(result({ status: 1, stderr: "weekly usage limit reached" })), "CODEX_NATIVE_USAGE_LIMIT_REACHED"));
test("fake Codex quota exhaustion is classified", () => assert.equal(classifyCodexNative(result({ status: 1, stderr: "quota exceeded" })), "CODEX_NATIVE_QUOTA_EXCEEDED"));
test("fake Codex authentication block is classified", () => assert.equal(classifyCodexNative(result({ status: 1, stderr: "login required" })), "CODEX_NATIVE_AUTH_BLOCKED"));
test("fake Codex permission block is classified", () => assert.equal(classifyCodexNative(result({ status: 1, stderr: "permission denied" })), "CODEX_NATIVE_PERMISSION_BLOCKED"));
test("fake Codex unsupported invocation is classified", () => assert.equal(classifyCodexNative(result({ status: 2, stderr: "unknown option" })), "CODEX_NATIVE_INVOCATION_UNSUPPORTED"));
test("fake Codex timeout is classified", () => assert.equal(classifyCodexNative(result({ signal: "SIGTERM", errorCode: "ETIMEDOUT" })), "CODEX_NATIVE_TIMEOUT"));
test("unknown nonzero exit is a safe unknown failure", () => assert.equal(classifyCodexNative(result({ status: 1, stderr: "internal failure" })), "CODEX_NATIVE_UNKNOWN_SAFE_FAILURE"));
test("active workspace mutation prevents pass", () => assert.equal(classifyCodexNative(result({ diff: diff([CODEX_SMOKE_NAME]), smokeExists: true, smokeMatches: true, activeUnchanged: false })), "CODEX_NATIVE_EXTRA_CHANGES"));
test("marker mutation prevents pass", () => assert.equal(classifyCodexNative(result({ diff: diff([CODEX_SMOKE_NAME], [CODEX_MARKER]), smokeExists: true, smokeMatches: true, markerUnchanged: false })), "CODEX_NATIVE_EXTRA_CHANGES"));
test("sanitized result contains metadata but no prompt or raw output", () => {
  const value = sanitizedResult("CODEX_NATIVE_NO_CHANGES", diff(), false, true, true);
  assert.equal(value.instruction_type, "codex_subscription_bridge_smoke");
  assert.doesNotMatch(JSON.stringify(value), /stdout|stderr|command|absolute_path|session/i);
});
test("smoke content and filename are exact", () => { assert.equal(CODEX_SMOKE_NAME, "CODEX_GENERIC_SMOKE.txt"); assert.equal(CODEX_SMOKE_CONTENT, "ForgeX Codex subscription bridge smoke completed."); });
test("native command refuses without real Codex confirmation", () => {
  const command = path.join(import.meta.dirname, "qa-codex-native-write.mjs");
  const value = spawnSync(process.execPath, [command], { cwd: path.resolve(import.meta.dirname, ".."), encoding: "utf8", shell: false, timeout: 10_000 });
  assert.equal(value.status, 2); assert.match(value.stdout, /explicit_confirmation_required/);
});
test("native command refuses without subscription retry gate", () => {
  const command = path.join(import.meta.dirname, "qa-codex-native-write.mjs");
  const value = spawnSync(process.execPath, [command, "--confirm-real-codex"], { cwd: path.resolve(import.meta.dirname, ".."), encoding: "utf8", shell: false, timeout: 10_000 });
  assert.equal(value.status, 2); assert.match(value.stdout, /subscription_bridge_retry_required/);
});
test("native source uses direct argv shell false and exact gates", () => {
  const source = fs.readFileSync(path.join(import.meta.dirname, "qa-codex-native-write.mjs"), "utf8");
  assert.match(source, /spawnSync\(launcher\.command/); assert.match(source, /shell: false/); assert.doesNotMatch(source, /shell: true/);
  assert.match(source, /--confirm-real-codex/); assert.match(source, /--subscription-bridge-retry/);
});
test("native source reads no auth file and persists no raw prompt or output", () => {
  const source = fs.readFileSync(path.join(import.meta.dirname, "qa-codex-native-write.mjs"), "utf8");
  assert.doesNotMatch(source, /auth\.json|\.codex[\\/]auth|writeFileSync\([^\n]*(?:stdout|stderr|instruction)/i);
  assert.doesNotMatch(source, /console\.(?:log|error)\([^\n]*(?:stdout|stderr|instruction)/i);
});
test("Codex bridge has no apply build flash or competing provider execution", () => {
  const source = fs.readFileSync(path.join(import.meta.dirname, "qa-codex-native-write.mjs"), "utf8");
  assert.doesNotMatch(source, /auto[_-]?(?:apply|build|flash)|\bagy\b|\bclaude\b|\bopencode\b/i);
});
