import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { detectInstalledVersion, snapshotWorkspace, compareBaselines } from "./qa-agy-generic-live-core.mjs";
import { nativeBaseline, resolveAgyOnPath } from "./qa-agy-native-write-core.mjs";
import { guardActiveWorkspace, guardTrustedWorkspace, trustedWorkspacePath } from "./qa-agy-trusted-workspace-core.mjs";
import {
  SCRATCH_CLASSIFICATIONS, appendScratchReview, assertSafeAgyArgs, buildScratchInstruction,
  createScratchArtifactModel, createScratchReviewRecord, expectedArtifactPath,
  hashWorkspaceIdentity, inspectExactScratchArtifact, isBoundedAgyVersion,
  resolveScratchRoot, sanitizedScratchResult, validateScratchRoot,
} from "./qa-agy-scratch-import-core.mjs";

const repositoryRoot = path.resolve(import.meta.dirname, "..");
const activeWorkspace = path.join(repositoryRoot, "workspace", "forgex-apply-test");

export function runScratchImport(options = {}) {
  const spawn = options.spawnSyncImpl || spawnSync;
  const repo = path.resolve(options.repositoryRoot || repositoryRoot);
  const activeRequested = path.resolve(options.activeWorkspace || path.join(repo, "workspace", "forgex-apply-test"));
  const model = options.model || createScratchArtifactModel();
  let active;
  let trusted;
  let scratchRoot;
  let executable;
  let version;

  try {
    active = guardActiveWorkspace(repo, activeRequested);
    trusted = guardTrustedWorkspace(repo, active, trustedWorkspacePath(repo, active)).workspaceRoot;
    assertProviderCwd(repo, active, trusted);
    scratchRoot = validateScratchRoot(options.scratchRoot || resolveScratchRoot());
    const candidate = expectedArtifactPath(scratchRoot, model);
    if (fs.existsSync(candidate)) throw new Error("expected_scratch_artifact_preexists");
    executable = options.executable || resolveAgyOnPath(options.env || process.env);
    if (!executable) throw new Error("agy_not_installed");
    version = options.version || detectInstalledVersion(executable, spawn, process.platform);
    if (!isBoundedAgyVersion(version)) throw new Error("agy_version_out_of_bounds");
  } catch (error) {
    return result(SCRATCH_CLASSIFICATIONS.ABORTED, safeReason(error), sanitizedScratchResult(), { model, version });
  }

  const activeBefore = snapshotWorkspace(active);
  const trustedBefore = nativeBaseline(trusted);
  const instruction = buildScratchInstruction(model);
  const args = ["-p", instruction];
  try { assertSafeAgyArgs(args); }
  catch (error) { return result(SCRATCH_CLASSIFICATIONS.ABORTED, safeReason(error), sanitizedScratchResult(), { model, version }); }

  let provider;
  try {
    provider = spawn(executable, args, {
      cwd: trusted, encoding: "utf8", input: "", shell: false, windowsHide: true,
      timeout: options.timeoutMs || 330_000, maxBuffer: 1024 * 1024,
    });
  } catch {
    provider = { status: null, signal: null, error: { code: "SPAWN_ERROR" }, stdout: "", stderr: "" };
  }

  const activeUnchanged = compareBaselines(activeBefore, snapshotWorkspace(active));
  const trustedAfter = nativeBaseline(trusted);
  const trustedDiff = compareNativeState(trustedBefore, trustedAfter);
  const artifact = inspectExactScratchArtifact(scratchRoot, model);
  let classification = artifact.classification;
  let reason = classification.toLowerCase();
  if (provider.error?.code === "ETIMEDOUT" || provider.signal) {
    classification = SCRATCH_CLASSIFICATIONS.TIMEOUT;
    reason = "provider_timeout";
  } else if (provider.status !== 0) {
    classification = SCRATCH_CLASSIFICATIONS.PROVIDER_ERROR;
    reason = "provider_process_error";
  } else if (!activeUnchanged || !trustedDiff.workspaceUnchanged || !trustedDiff.markerUnchanged) {
    classification = SCRATCH_CLASSIFICATIONS.UNKNOWN;
    reason = "workspace_integrity_changed";
  }

  const details = sanitizedScratchResult({
    execution_count: 1,
    scratch_artifact_found: artifact.found,
    nonce_verified: artifact.nonce_verified,
    size_verified: artifact.size_verified,
    symlink_reparse_blocked: artifact.symlink_blocked,
    path_containment_verified: artifact.containment_verified,
    active_workspace_unchanged: activeUnchanged,
    managed_workspace_unchanged: trustedDiff.workspaceUnchanged,
    marker_unchanged: trustedDiff.markerUnchanged,
  });

  let reviewId = null;
  if (classification === SCRATCH_CLASSIFICATIONS.PASS) {
    const review = createScratchReviewRecord(model, artifact, hashWorkspaceIdentity(active));
    const reviewPath = options.reviewPath || path.join(repo, ".promptforge", "state", "bridge-reviews.jsonl");
    reviewId = appendScratchReview(reviewPath, review);
    details.review_artifact_created = true;
  }
  return result(classification, reason, details, { model, version, reviewId });
}

export function assertProviderCwd(repository, active, trusted) {
  const values = [repository, active, trusted].map((item) => fs.realpathSync.native(item));
  if (samePath(values[2], values[0])) throw new Error("provider_cwd_repository_rejected");
  if (samePath(values[2], values[1])) throw new Error("provider_cwd_active_workspace_rejected");
  if (!path.isAbsolute(values[2])) throw new Error("provider_cwd_not_absolute");
  return true;
}

function compareNativeState(before, after) {
  const beforeMap = JSON.stringify(before.files);
  const afterMap = JSON.stringify(after.files);
  return { workspaceUnchanged: beforeMap === afterMap, markerUnchanged: before.markerHash !== null && before.markerHash === after.markerHash };
}

function result(classification, reason, details, { model, version, reviewId }) {
  return {
    classification, reason, agy_version: version || null,
    instruction_type: "agy_scratch_smoke",
    run_id: model.run_id,
    nonce_reference: crypto.createHash("sha256").update(model.nonce).digest("hex").slice(0, 16),
    expected_filename: model.expected_filename,
    review_id: reviewId,
    ...details,
  };
}
function safeReason(error) { return error instanceof Error && /^[a-z0-9_]+$/i.test(error.message) ? error.message : "unknown_safe_failure"; }
function samePath(left, right) { return path.resolve(left).toLowerCase() === path.resolve(right).toLowerCase(); }

function main() {
  if (!process.argv.includes("--confirm-real-agy")) finish(result(SCRATCH_CLASSIFICATIONS.ABORTED, "explicit_confirmation_required", sanitizedScratchResult(), { model: createScratchArtifactModel(), version: null, reviewId: null }), 2);
  if (!process.argv.includes("--trusted-workspace-attested")) finish(result(SCRATCH_CLASSIFICATIONS.ABORTED, "trusted_workspace_attestation_required", sanitizedScratchResult(), { model: createScratchArtifactModel(), version: null, reviewId: null }), 2);
  if (process.argv.includes("--artifact-kind") || process.argv.some((item) => item.startsWith("--artifact-kind="))) finish(result(SCRATCH_CLASSIFICATIONS.ABORTED, "unsupported_artifact_kind", sanitizedScratchResult(), { model: createScratchArtifactModel(), version: null, reviewId: null }), 2);
  const output = runScratchImport({ repositoryRoot, activeWorkspace });
  finish(output, output.classification === SCRATCH_CLASSIFICATIONS.PASS ? 0 : 1);
}
function finish(output, code) { console.log(output.classification); console.log(JSON.stringify(output)); process.exit(code); }

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
