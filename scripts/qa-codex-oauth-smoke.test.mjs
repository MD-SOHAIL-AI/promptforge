import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

import {
  DANGEROUS_CODEX_FLAGS, OAUTH_MARKER, OAUTH_MARKER_CONTENT, OAUTH_SMOKE_CONTENT,
  OAUTH_PROMPT_VARIANT, OAUTH_SMOKE_FILE, assertSafeOAuthSmokeArgs, buildOAuthSmokeArgs, classifyOAuthSmoke,
  compareSnapshots, createOAuthSandbox, guardOAuthSandbox, inspectKnownOAuthSmokeContent,
  isSafeOAuthSmokePass, sanitizedSmokeResult, smokePrompt, snapshotTree, validateOAuthSmokeContent,
} from "./qa-codex-oauth-smoke-core.mjs";

function fixture() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-oauth-test-"));
  const repo = path.join(base, "repo"), active = path.join(repo, "workspace", "active"), managed = path.join(base, "external");
  fs.mkdirSync(active, { recursive: true }); fs.writeFileSync(path.join(active, "baseline.txt"), "safe\n");
  return { base, repo, active, managed, env: { USERPROFILE: path.join(base, "home") } };
}
function sandbox() { const value = fixture(); return { ...value, guarded: createOAuthSandbox(value.repo, value.active, "oauth-0123456789abcdef01234567", value.env, value.managed) }; }
function diff(created = [], modified = [], deleted = []) { return { created, modified, deleted, changedCount: created.length + modified.length + deleted.length }; }
function result(values = {}) { return { status: 0, signal: null, errorCode: null, stdout: "", stderr: "", diff: diff(), expectedExists: false, expectedValid: false, markerUnchanged: true, activeUnchanged: true, ...values }; }

test("OAuth smoke argv uses global approval before exec and workspace-write", () => {
  const root = "C:\\forgex-codex-oauth-smoke\\oauth-0123456789abcdef01234567";
  assert.deepEqual(buildOAuthSmokeArgs(root, "runtime-only"), ["--ask-for-approval", "never", "exec", "--sandbox", "workspace-write", "--cd", root, "runtime-only"]);
});
test("OAuth smoke blocks dangerous flags", () => { for (const flag of DANGEROUS_CODEX_FLAGS) assert.throws(() => assertSafeOAuthSmokeArgs([flag], "C:\\safe")); });
test("OAuth sandbox is external direct child with exact marker and git repo", () => { const { guarded, managed } = sandbox(); assert.equal(path.dirname(guarded.sandboxRoot), fs.realpathSync.native(managed)); assert.equal(fs.readFileSync(path.join(guarded.sandboxRoot, OAUTH_MARKER), "utf8"), OAUTH_MARKER_CONTENT); assert.equal(fs.existsSync(path.join(guarded.sandboxRoot, ".git")), true); });
test("OAuth sandbox refuses repository and active workspace", () => { const { repo, active, managed, env } = fixture(); fs.mkdirSync(managed); assert.throws(() => guardOAuthSandbox(repo, active, repo, env, managed)); assert.throws(() => guardOAuthSandbox(repo, active, active, env, managed)); });
test("OAuth sandbox refuses home Desktop OneDrive and filesystem roots", () => { const { repo, active, base } = fixture(); for (const unsafe of [base, path.join(base, "Desktop"), path.parse(base).root]) { if (!fs.existsSync(unsafe)) fs.mkdirSync(unsafe, { recursive: true }); assert.throws(() => createOAuthSandbox(repo, active, "oauth-fedcba9876543210fedcba98", { USERPROFILE: base, OneDrive: base }, unsafe)); } });
test("OAuth sandbox rejects symlink or reparse escape", (t) => { const { guarded, active, repo, env, managed } = sandbox(); try { fs.symlinkSync(active, path.join(guarded.sandboxRoot, "escape"), "junction"); } catch { t.skip("host does not permit links"); return; } assert.throws(() => guardOAuthSandbox(repo, active, guarded.sandboxRoot, env, managed)); });
test("snapshots detect created modified and deleted files", () => { const { active } = fixture(); const before = snapshotTree(active); fs.writeFileSync(path.join(active, "new.txt"), "new"); fs.writeFileSync(path.join(active, "baseline.txt"), "changed"); const after = snapshotTree(active); assert.deepEqual(compareSnapshots(before, after), { created: ["new.txt"], modified: ["baseline.txt"], deleted: [], changedCount: 2 }); });
test("exact output passes", () => assert.equal(classifyOAuthSmoke(result({ diff: diff([OAUTH_SMOKE_FILE]), expectedExists: true, expectedValid: true })), "CODEX_OAUTH_SMOKE_PASS"));
test("nonzero exit with normalized trailing newline artifact still passes", () => {
  const contentDiagnostics = validateOAuthSmokeContent(Buffer.from(`${OAUTH_SMOKE_CONTENT}\n`));
  assert.equal(contentDiagnostics.valid, true);
  assert.equal(classifyOAuthSmoke(result({ status: 1, diff: diff([OAUTH_SMOKE_FILE]), expectedExists: true, expectedValid: true, contentDiagnostics })), "CODEX_OAUTH_SMOKE_PASS");
  const payload = sanitizedSmokeResult("CODEX_OAUTH_SMOKE_PASS", diff([OAUTH_SMOKE_FILE]), { authStatus: "signed_in", ready: true, executionCount: 1, expectedExists: true, expectedValid: true, contentDiagnostics, markerUnchanged: true, activeUnchanged: true });
  assert.equal(isSafeOAuthSmokePass(payload), true);
  assert.equal(payload.actual_byte_count, 43);
  assert.equal(payload.expected_byte_count, 42);
});
test("final pass predicate fails closed for every unsafe metadata flag", () => {
  const contentDiagnostics = validateOAuthSmokeContent(Buffer.from(`${OAUTH_SMOKE_CONTENT}\n`));
  const valid = sanitizedSmokeResult("CODEX_OAUTH_SMOKE_PASS", diff([OAUTH_SMOKE_FILE]), { authStatus: "signed_in", ready: true, executionCount: 1, expectedExists: true, expectedValid: true, contentDiagnostics, markerUnchanged: true, activeUnchanged: true });
  for (const [key, value] of [["dangerous_flags_used", true], ["raw_prompt_persisted", true], ["raw_output_persisted", true], ["tokens_read", true], ["auth_files_read", true], ["marker_unchanged", false], ["active_workspace_unchanged", false], ["oauth_bridge_ready", false]]) assert.equal(isSafeOAuthSmokePass({ ...valid, [key]: value }), false, key);
  assert.equal(isSafeOAuthSmokePass({ ...valid, auth_status: "signed_out" }), false);
  assert.equal(isSafeOAuthSmokePass({ ...valid, created_file_count: 2 }), false);
});
test("strict content validator accepts only harmless encoding and EOF normalization", () => {
  for (const value of [OAUTH_SMOKE_CONTENT, `${OAUTH_SMOKE_CONTENT}\n`, `${OAUTH_SMOKE_CONTENT}\r\n`, `\ufeff${OAUTH_SMOKE_CONTENT}`]) assert.equal(validateOAuthSmokeContent(Buffer.from(value)).valid, true);
});
test("strict content validator rejects extra or altered text", () => {
  for (const value of [`${OAUTH_SMOKE_CONTENT}\nextra`, `\`\`\`\n${OAUTH_SMOKE_CONTENT}\n\`\`\``, `"${OAUTH_SMOKE_CONTENT}"`, "forgex Codex OAuth bridge smoke completed.", "ForgeX Codex OAuth bridge smoke completed", ` ${OAUTH_SMOKE_CONTENT}`, `${OAUTH_SMOKE_CONTENT} \n`]) assert.equal(validateOAuthSmokeContent(Buffer.from(value)).valid, false);
});
test("content diagnostics are hash-only and classify normalization", () => {
  const newline = validateOAuthSmokeContent(Buffer.from(`${OAUTH_SMOKE_CONTENT}\n`));
  const crlf = validateOAuthSmokeContent(Buffer.from(`${OAUTH_SMOKE_CONTENT}\r\n`));
  const bom = validateOAuthSmokeContent(Buffer.from(`\ufeff${OAUTH_SMOKE_CONTENT}`));
  const wrong = validateOAuthSmokeContent(Buffer.from("wrong"));
  assert.equal(newline.first_difference_kind, "trailing_newline");
  assert.equal(crlf.first_difference_kind, "line_ending");
  assert.equal(bom.first_difference_kind, "bom");
  assert.equal(wrong.normalized_content_matches, false);
  assert.match(newline.actual_content_hash, /^[a-f0-9]{64}$/);
  assert.doesNotMatch(JSON.stringify(newline), /ForgeX Codex OAuth bridge smoke completed/);
});
test("strict prompt v2 removes ambiguity without persistence", () => {
  assert.equal(OAUTH_PROMPT_VARIANT, "strict_single_line_v2");
  assert.match(smokePrompt(), /no quotes and no markdown/);
  assert.match(smokePrompt(), /Do not add a second line/);
});
test("known smoke inspection is contained and does not execute Codex", () => {
  const { guarded, repo, active, managed, env } = sandbox();
  fs.writeFileSync(path.join(guarded.sandboxRoot, OAUTH_SMOKE_FILE), `${OAUTH_SMOKE_CONTENT}\n`);
  const metadata = { provider_id: "codex_cli_oauth_bridge", run_id: path.basename(guarded.sandboxRoot) };
  const inspected = inspectKnownOAuthSmokeContent({ repositoryRoot: repo, activeWorkspace: active, managedRoot: managed, metadata, env });
  assert.equal(inspected.classification, "CODEX_OAUTH_SMOKE_INSPECT_PASS");
  assert.equal(inspected.codex_executed, false);
  assert.equal(inspected.normalized_content_matches, true);
  assert.equal(inspectKnownOAuthSmokeContent({ repositoryRoot: repo, activeWorkspace: active, managedRoot: managed, metadata: { ...metadata, run_id: "outside" }, env }).classification, "CODEX_OAUTH_SMOKE_INSPECT_NOT_AVAILABLE");
});
test("known smoke inspection rejects symlink or reparse entries", (t) => {
  const { guarded, repo, active, managed, env } = sandbox();
  try { fs.symlinkSync(active, path.join(guarded.sandboxRoot, "escape"), "junction"); } catch { t.skip("host does not permit links"); return; }
  const inspected = inspectKnownOAuthSmokeContent({ repositoryRoot: repo, activeWorkspace: active, managedRoot: managed, metadata: { provider_id: "codex_cli_oauth_bridge", run_id: path.basename(guarded.sandboxRoot) }, env });
  assert.equal(inspected.classification, "CODEX_OAUTH_SMOKE_INSPECT_UNSAFE_ABORTED");
});
test("no changes does not pass", () => assert.equal(classifyOAuthSmoke(result()), "CODEX_OAUTH_SMOKE_NO_CHANGES"));
test("extra changes do not pass", () => assert.equal(classifyOAuthSmoke(result({ diff: diff(["extra.txt"]) })), "CODEX_OAUTH_SMOKE_EXTRA_CHANGES"));
test("wrong content does not pass", () => assert.equal(classifyOAuthSmoke(result({ diff: diff([OAUTH_SMOKE_FILE]), expectedExists: true, contentDiagnostics: { normalized_content_matches: false } })), "CODEX_OAUTH_SMOKE_CONTENT_INVALID_TEXT_MISMATCH"));
test("usage limit does not pass", () => assert.equal(classifyOAuthSmoke(result({ status: 1, stderr: "usage limit reached" })), "CODEX_OAUTH_SMOKE_USAGE_LIMIT_REACHED"));
test("quota does not pass", () => assert.equal(classifyOAuthSmoke(result({ status: 1, stderr: "quota exceeded" })), "CODEX_OAUTH_SMOKE_QUOTA_EXCEEDED"));
test("permission block does not pass", () => assert.equal(classifyOAuthSmoke(result({ status: 1, stderr: "permission denied" })), "CODEX_OAUTH_SMOKE_PERMISSION_BLOCKED"));
test("auth block does not pass", () => assert.equal(classifyOAuthSmoke(result({ status: 1, stderr: "login required" })), "CODEX_OAUTH_SMOKE_AUTH_BLOCKED"));
test("timeout does not pass", () => assert.equal(classifyOAuthSmoke(result({ signal: "SIGTERM", errorCode: "ETIMEDOUT" })), "CODEX_OAUTH_SMOKE_TIMEOUT"));
test("marker or active workspace mutation prevents pass", () => { const exact = { diff: diff([OAUTH_SMOKE_FILE]), expectedExists: true, expectedValid: true }; assert.equal(classifyOAuthSmoke(result({ ...exact, markerUnchanged: false })), "CODEX_OAUTH_SMOKE_EXTRA_CHANGES"); assert.equal(classifyOAuthSmoke(result({ ...exact, activeUnchanged: false })), "CODEX_OAUTH_SMOKE_EXTRA_CHANGES"); });
test("sanitized metadata contains no raw prompt output auth or URL data", () => { const value = sanitizedSmokeResult("CODEX_OAUTH_SMOKE_PASS", diff([OAUTH_SMOKE_FILE])); const text = JSON.stringify(value); assert.doesNotMatch(text, /stdout|stderr|auth\.json|callback_url|access_token|refresh_token|id_token/i); });
test("expected OAuth file and content are exact", () => { assert.equal(OAUTH_SMOKE_FILE, "CODEX_OAUTH_BRIDGE_SMOKE.txt"); assert.equal(OAUTH_SMOKE_CONTENT, "ForgeX Codex OAuth bridge smoke completed."); });
test("command refuses without explicit real Codex confirmation", () => { const command = path.join(import.meta.dirname, "qa-codex-oauth-bridge.mjs"); const value = spawnSync(process.execPath, [command, "--standalone-smoke"], { cwd: path.resolve(import.meta.dirname, ".."), encoding: "utf8", shell: false, timeout: 10_000 }); assert.equal(value.status, 2); assert.match(value.stdout, /CODEX_OAUTH_SMOKE_CONFIRMATION_REQUIRED/); });
test("command source uses direct argv shell false and no competing providers", () => { const source = fs.readFileSync(path.join(import.meta.dirname, "qa-codex-oauth-bridge.mjs"), "utf8"); assert.match(source, /spawnSync\(status\.launcher\.command/); assert.match(source, /shell: false/); assert.doesNotMatch(source, /shell: true|\bagy\b|\bopencode\b|\bclaude\b/i); });
test("command source does not read auth files or persist prompt and raw output", () => { const source = fs.readFileSync(path.join(import.meta.dirname, "qa-codex-oauth-bridge.mjs"), "utf8"); assert.doesNotMatch(source, /auth\.json|\.codex[\\/]auth|writeFileSync\([^\n]*(stdout|stderr|smokePrompt)/i); });
test("UI smoke button is gated by signed-in ready feature status", () => { const source = fs.readFileSync(path.join(import.meta.dirname, "..", "frontend", "components", "ide", "model-settings-panel.tsx"), "utf8"); assert.match(source, /Run Sandboxed Smoke/); assert.match(source, /auth_status !== "signed_in"/); assert.match(source, /oauth_bridge_ready/); assert.match(source, /sandbox_smoke_enabled/); });
test("UI requires the exact smoke confirmation", () => { const source = fs.readFileSync(path.join(import.meta.dirname, "..", "frontend", "components", "ide", "model-settings-panel.tsx"), "utf8"); assert.match(source, /ForgeX will run one Codex CLI smoke test inside an external disposable sandbox\. It will not edit your active workspace\. Continue\?/); });
test("UI shows Open Review only after exact pass", () => { const source = fs.readFileSync(path.join(import.meta.dirname, "..", "frontend", "components", "ide", "model-settings-panel.tsx"), "utf8"); assert.match(source, /classification === "CODEX_OAUTH_SMOKE_PASS"[\s\S]*Open Review/); });
