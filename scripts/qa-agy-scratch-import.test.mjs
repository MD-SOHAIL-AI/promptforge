import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { snapshotWorkspace } from "./qa-agy-generic-live-core.mjs";
import { TRUSTED_MARKER, TRUSTED_MARKER_VALUE, trustedWorkspacePath } from "./qa-agy-trusted-workspace-core.mjs";
import {
  DANGEROUS_PERMISSION_FLAG, SCRATCH_CLASSIFICATIONS, SCRATCH_SMOKE_MAX_BYTES,
  assertSafeAgyArgs, buildScratchInstruction, createScratchArtifactModel,
  createScratchReviewRecord, expectedArtifactPath, expectedSmokeContent,
  inspectExactScratchArtifact, isBoundedAgyVersion, resolveScratchRoot,
} from "./qa-agy-scratch-import-core.mjs";
import { assertProviderCwd, runScratchImport } from "./qa-agy-scratch-import.mjs";

function fixture() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-agy-scratch-"));
  const active = path.join(repo, "workspace", "forgex-apply-test");
  fs.mkdirSync(active, { recursive: true });
  fs.writeFileSync(path.join(active, "QA_NOT_REAL_PROJECT.txt"), "throwaway\n");
  const trusted = trustedWorkspacePath(repo, active);
  fs.mkdirSync(trusted, { recursive: true });
  fs.writeFileSync(path.join(trusted, TRUSTED_MARKER), `${JSON.stringify(TRUSTED_MARKER_VALUE)}\n`);
  fs.writeFileSync(path.join(trusted, "base.txt"), "base\n");
  const scratch = path.join(repo, "scratch");
  fs.mkdirSync(scratch);
  return { repo, active, trusted, scratch, review: path.join(repo, ".promptforge", "state", "bridge-reviews.jsonl") };
}
function model() { return createScratchArtifactModel({ randomBytes: (size) => Buffer.alloc(size, size) }); }
function writeArtifact(root, value, content = expectedSmokeContent(value)) { fs.writeFileSync(expectedArtifactPath(root, value), content); }
function fakePass(root, value, inspect = null) {
  return (executable, args, options) => {
    inspect?.({ executable, args, options });
    writeArtifact(root, value);
    return { status: 0, signal: null, error: null, stdout: "ignored provider output", stderr: "" };
  };
}

test("command refuses without real AGY confirmation", () => { const value = spawnSync(process.execPath, [path.join(import.meta.dirname, "qa-agy-scratch-import.mjs")], { encoding: "utf8" }); assert.equal(value.status, 2); assert.match(value.stdout, /explicit_confirmation_required/); });
test("command refuses without trusted workspace attestation", () => { const value = spawnSync(process.execPath, [path.join(import.meta.dirname, "qa-agy-scratch-import.mjs"), "--confirm-real-agy"], { encoding: "utf8" }); assert.equal(value.status, 2); assert.match(value.stdout, /trusted_workspace_attestation_required/); });
test("missing AGY aborts before execution", () => { const f = fixture(); const value = runScratchImport({ repositoryRoot: f.repo, activeWorkspace: f.active, scratchRoot: f.scratch, env: { PATH: "", PATHEXT: ".EXE" }, model: model() }); assert.equal(value.classification, SCRATCH_CLASSIFICATIONS.ABORTED); assert.equal(value.reason, "agy_not_installed"); assert.equal(value.execution_count, 0); });
test("provider cwd rejects repository root", () => { const f = fixture(); assert.throws(() => assertProviderCwd(f.repo, f.active, f.repo), /repository/); });
test("provider cwd rejects active workspace", () => { const f = fixture(); assert.throws(() => assertProviderCwd(f.repo, f.active, f.active), /active/); });
test("scratch root defaults under home and test override is explicit", () => { assert.equal(resolveScratchRoot({ home: "C:/safe-home" }), path.resolve("C:/safe-home", ".gemini", "antigravity-cli", "scratch")); assert.throws(() => resolveScratchRoot({ testOverride: "C:/tmp" }), /override/); });
test("AGY version is bounded to supported major", () => { assert.equal(isBoundedAgyVersion("1.0.14"), true); assert.equal(isBoundedAgyVersion("2.0.0"), false); assert.equal(isBoundedAgyVersion("not-version"), false); });
test("exact expected filename embeds run and nonce", () => { const value = model(); assert.match(value.expected_filename, new RegExp(`${value.run_id}_${value.nonce}\\.txt$`)); });
test("missing exact artifact is classified missing", () => { const f = fixture(); assert.equal(inspectExactScratchArtifact(f.scratch, model()).classification, SCRATCH_CLASSIFICATIONS.MISSING); });
test("unrelated newest scratch file is ignored", () => { const f = fixture(); fs.writeFileSync(path.join(f.scratch, "newest.txt"), "unrelated"); assert.equal(inspectExactScratchArtifact(f.scratch, model()).classification, SCRATCH_CLASSIFICATIONS.MISSING); });
test("only exact expected artifact is imported", () => { const f = fixture(); const value = model(); fs.writeFileSync(path.join(f.scratch, `other-${value.nonce}.txt`), expectedSmokeContent(value)); assert.equal(inspectExactScratchArtifact(f.scratch, value).classification, SCRATCH_CLASSIFICATIONS.MISSING); });
test("nonce mismatch is rejected", () => { const f = fixture(); const value = model(); writeArtifact(f.scratch, value, `${value.expected_content_marker}\nrun_id=${value.run_id}\nnonce=wrong`); assert.equal(inspectExactScratchArtifact(f.scratch, value).classification, SCRATCH_CLASSIFICATIONS.NONCE_MISMATCH); });
test("invalid content is rejected", () => { const f = fixture(); const value = model(); writeArtifact(f.scratch, value, `${expectedSmokeContent(value)}\nextra`); assert.equal(inspectExactScratchArtifact(f.scratch, value).classification, SCRATCH_CLASSIFICATIONS.CONTENT_INVALID); });
test("too-large artifact is rejected before reading", () => { const f = fixture(); const value = model(); writeArtifact(f.scratch, value, "x".repeat(SCRATCH_SMOKE_MAX_BYTES + 1)); assert.equal(inspectExactScratchArtifact(f.scratch, value).classification, SCRATCH_CLASSIFICATIONS.TOO_LARGE); });
test("path containment escape is rejected", () => { const f = fixture(); const value = { ...model(), expected_filename: "../escape.txt" }; assert.equal(inspectExactScratchArtifact(f.scratch, value).classification, SCRATCH_CLASSIFICATIONS.UNSAFE_PATH); });
test("symlink artifact is blocked", (t) => { const f = fixture(); const value = model(); const target = path.join(f.repo, "outside.txt"); fs.writeFileSync(target, expectedSmokeContent(value)); try { fs.symlinkSync(target, expectedArtifactPath(f.scratch, value), "file"); } catch { t.skip("host cannot create file symlinks"); return; } assert.equal(inspectExactScratchArtifact(f.scratch, value).classification, SCRATCH_CLASSIFICATIONS.SYMLINK); });
test("safe exact artifact passes all checks", () => { const f = fixture(); const value = model(); writeArtifact(f.scratch, value); const result = inspectExactScratchArtifact(f.scratch, value); assert.equal(result.classification, SCRATCH_CLASSIFICATIONS.PASS); assert.equal(result.nonce_verified, true); assert.equal(result.size_verified, true); assert.equal(result.containment_verified, true); });
test("direct argv uses shell false and managed cwd", () => { const f = fixture(); const value = model(); let observed; const result = runScratchImport({ repositoryRoot: f.repo, activeWorkspace: f.active, scratchRoot: f.scratch, executable: "fake-agy", version: "1.0.14", model: value, reviewPath: f.review, spawnSyncImpl: fakePass(f.scratch, value, (item) => { observed = item; }) }); assert.equal(result.classification, SCRATCH_CLASSIFICATIONS.PASS); assert.deepEqual(observed.args.slice(0, 1), ["-p"]); assert.equal(observed.options.shell, false); assert.equal(observed.options.cwd, fs.realpathSync.native(f.trusted)); });
test("dangerous permission bypass is blocked", () => assert.throws(() => assertSafeAgyArgs(["-p", DANGEROUS_PERMISSION_FLAG]), /dangerous/));
test("runtime instruction is not part of sanitized result", () => { const f = fixture(); const value = model(); const result = runScratchImport({ repositoryRoot: f.repo, activeWorkspace: f.active, scratchRoot: f.scratch, executable: "fake", version: "1.0.14", model: value, reviewPath: f.review, spawnSyncImpl: fakePass(f.scratch, value) }); assert.equal(JSON.stringify(result).includes(buildScratchInstruction(value)), false); assert.doesNotMatch(JSON.stringify(result), /stdout|stderr/); });
test("successful import keeps active and managed workspaces unchanged", () => { const f = fixture(); const beforeActive = snapshotWorkspace(f.active); const beforeTrusted = snapshotWorkspace(f.trusted); const value = model(); const result = runScratchImport({ repositoryRoot: f.repo, activeWorkspace: f.active, scratchRoot: f.scratch, executable: "fake", version: "1.0.14", model: value, reviewPath: f.review, spawnSyncImpl: fakePass(f.scratch, value) }); assert.equal(result.active_workspace_unchanged, true); assert.equal(result.managed_workspace_unchanged, true); assert.equal(result.marker_unchanged, true); assert.deepEqual(snapshotWorkspace(f.active), beforeActive); assert.deepEqual(snapshotWorkspace(f.trusted), beforeTrusted); });
test("active workspace mutation prevents pass and review", () => { const f = fixture(); const value = model(); const fake = fakePass(f.scratch, value, () => fs.writeFileSync(path.join(f.active, "bad.txt"), "bad")); const result = runScratchImport({ repositoryRoot: f.repo, activeWorkspace: f.active, scratchRoot: f.scratch, executable: "fake", version: "1.0.14", model: value, reviewPath: f.review, spawnSyncImpl: fake }); assert.equal(result.classification, SCRATCH_CLASSIFICATIONS.UNKNOWN); assert.equal(result.review_artifact_created, false); });
test("managed marker mutation prevents pass and review", () => { const f = fixture(); const value = model(); const fake = fakePass(f.scratch, value, () => fs.writeFileSync(path.join(f.trusted, TRUSTED_MARKER), "changed")); const result = runScratchImport({ repositoryRoot: f.repo, activeWorkspace: f.active, scratchRoot: f.scratch, executable: "fake", version: "1.0.14", model: value, reviewPath: f.review, spawnSyncImpl: fake }); assert.equal(result.classification, SCRATCH_CLASSIFICATIONS.UNKNOWN); assert.equal(result.marker_unchanged, false); });
test("provider failure has no review", () => { const f = fixture(); const value = model(); const result = runScratchImport({ repositoryRoot: f.repo, activeWorkspace: f.active, scratchRoot: f.scratch, executable: "fake", version: "1.0.14", model: value, reviewPath: f.review, spawnSyncImpl: () => ({ status: 1, stdout: "raw", stderr: "raw" }) }); assert.equal(result.classification, SCRATCH_CLASSIFICATIONS.PROVIDER_ERROR); assert.equal(fs.existsSync(f.review), false); });
test("provider timeout has no review", () => { const f = fixture(); const value = model(); const result = runScratchImport({ repositoryRoot: f.repo, activeWorkspace: f.active, scratchRoot: f.scratch, executable: "fake", version: "1.0.14", model: value, reviewPath: f.review, spawnSyncImpl: () => ({ status: null, error: { code: "ETIMEDOUT" } }) }); assert.equal(result.classification, SCRATCH_CLASSIFICATIONS.TIMEOUT); assert.equal(result.review_artifact_created, false); });
test("review is created only on pass with scratch metadata and no changed files", () => { const f = fixture(); const value = model(); const result = runScratchImport({ repositoryRoot: f.repo, activeWorkspace: f.active, scratchRoot: f.scratch, executable: "fake", version: "1.0.14", model: value, reviewPath: f.review, spawnSyncImpl: fakePass(f.scratch, value) }); const review = JSON.parse(fs.readFileSync(f.review, "utf8").trim()); assert.equal(result.review_artifact_created, true); assert.equal(review.provider_id, "agy"); assert.equal(review.artifact_source, "agy_scratch"); assert.equal(review.artifact_type, "scratch_smoke"); assert.deepEqual(review.changed_files, []); assert.equal(review.artifact_metadata.filename, value.expected_filename); });
test("review constructor rejects non-pass artifacts", () => assert.throws(() => createScratchReviewRecord(model(), { classification: SCRATCH_CLASSIFICATIONS.MISSING }, "hash"), /requires_pass/));
test("result explicitly disables apply build and flash", () => { const f = fixture(); const value = model(); const result = runScratchImport({ repositoryRoot: f.repo, activeWorkspace: f.active, scratchRoot: f.scratch, executable: "fake", version: "1.0.14", model: value, reviewPath: f.review, spawnSyncImpl: fakePass(f.scratch, value) }); assert.equal(result.automatic_apply, false); assert.equal(result.automatic_build, false); assert.equal(result.automatic_flash, false); });
test("source contains no recursive scratch scan or newest-file selection", () => { const source = fs.readFileSync(path.join(import.meta.dirname, "qa-agy-scratch-import-core.mjs"), "utf8"); assert.doesNotMatch(source, /readdirSync\(.*scratch|mtime|newest/i); });
test("source does not persist prompt or provider output", () => { const source = fs.readFileSync(path.join(import.meta.dirname, "qa-agy-scratch-import.mjs"), "utf8"); assert.doesNotMatch(source, /writeFileSync\([^\n]*(instruction|stdout|stderr)|appendFileSync\([^\n]*(instruction|stdout|stderr)/i); });
