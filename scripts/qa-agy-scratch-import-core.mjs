import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const SCRATCH_CLASSIFICATIONS = Object.freeze({
  PASS: "SCRATCH_IMPORT_PASS",
  MISSING: "SCRATCH_ARTIFACT_MISSING",
  NONCE_MISMATCH: "SCRATCH_ARTIFACT_NONCE_MISMATCH",
  CONTENT_INVALID: "SCRATCH_ARTIFACT_CONTENT_INVALID",
  TOO_LARGE: "SCRATCH_ARTIFACT_TOO_LARGE",
  UNSAFE_PATH: "SCRATCH_ARTIFACT_UNSAFE_PATH",
  SYMLINK: "SCRATCH_ARTIFACT_SYMLINK_BLOCKED",
  PROVIDER_ERROR: "SCRATCH_PROVIDER_ERROR",
  TIMEOUT: "SCRATCH_TIMEOUT",
  ABORTED: "SCRATCH_UNSAFE_ABORTED",
  UNKNOWN: "SCRATCH_UNKNOWN_SAFE_FAILURE",
});

export const SCRATCH_SMOKE_MAX_BYTES = 16 * 1024;
export const SCRATCH_SMOKE_EXTENSION = ".txt";
export const SCRATCH_SMOKE_CLASSIFICATION = "scratch_smoke";
export const AGY_MIN_VERSION = Object.freeze([1, 0, 0]);
export const AGY_MAX_VERSION_EXCLUSIVE = Object.freeze([2, 0, 0]);
export const DANGEROUS_PERMISSION_FLAG = "--dangerously-skip-permissions";

export function resolveScratchRoot({ home = os.homedir(), testOverride = null, allowTestOverride = false } = {}) {
  if (testOverride !== null) {
    if (!allowTestOverride) throw new Error("scratch_override_rejected");
    return path.resolve(testOverride);
  }
  return path.join(path.resolve(home), ".gemini", "antigravity-cli", "scratch");
}

export function createScratchArtifactModel({ now = new Date(), randomBytes = crypto.randomBytes } = {}) {
  const runId = `scratch-run-${randomBytes(12).toString("hex")}`;
  const nonce = randomBytes(16).toString("hex");
  const expectedFilename = `FORGEX_AGY_SCRATCH_SMOKE_${runId}_${nonce}.txt`;
  return Object.freeze({
    provider: "agy",
    run_id: runId,
    nonce,
    expected_filename: expectedFilename,
    expected_extension: SCRATCH_SMOKE_EXTENSION,
    expected_max_bytes: SCRATCH_SMOKE_MAX_BYTES,
    expected_content_marker: "ForgeX AGY scratch import smoke completed.",
    created_at: now.toISOString(),
    classification: SCRATCH_SMOKE_CLASSIFICATION,
  });
}

export function expectedSmokeContent(model) {
  return `${model.expected_content_marker}\nrun_id=${model.run_id}\nnonce=${model.nonce}`;
}

export function buildScratchInstruction(model) {
  return [
    "Create exactly one file in the Antigravity CLI scratch directory.",
    "", "File name:", model.expected_filename, "", "File content must be exactly:",
    expectedSmokeContent(model), "", "Do not modify any other files.",
    "Do not create files in the current working directory.",
    "Do not create files in the project directory.", "Do not run build commands.",
    "Do not install dependencies.", "Do not access the network.",
  ].join("\n");
}

export function assertSafeAgyArgs(args) {
  if (!Array.isArray(args) || args.length !== 2 || args[0] !== "-p" || typeof args[1] !== "string" || !args[1]) {
    throw new Error("scratch_invocation_rejected");
  }
  if (args.some((item) => item.includes(DANGEROUS_PERMISSION_FLAG))) throw new Error("dangerous_permission_flag_rejected");
  return true;
}

export function isBoundedAgyVersion(version) {
  const match = /^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$/.exec(String(version || "").trim());
  if (!match) return false;
  const value = match.slice(1, 4).map(Number);
  return compareVersion(value, AGY_MIN_VERSION) >= 0 && compareVersion(value, AGY_MAX_VERSION_EXCLUSIVE) < 0;
}

export function validateScratchRoot(root) {
  const requested = path.resolve(root);
  if (!fs.existsSync(requested)) throw new Error("scratch_root_missing");
  const linkStat = fs.lstatSync(requested);
  if (linkStat.isSymbolicLink() || !linkStat.isDirectory()) throw new Error("scratch_root_unsafe");
  const real = fs.realpathSync.native(requested);
  if (!samePath(requested, real)) throw new Error("scratch_root_unsafe");
  return real;
}

export function expectedArtifactPath(scratchRoot, model) {
  const expectedPattern = new RegExp(`^FORGEX_AGY_SCRATCH_SMOKE_${escapeRegExp(model.run_id)}_${escapeRegExp(model.nonce)}\\.txt$`);
  if (!expectedPattern.test(model.expected_filename) || path.extname(model.expected_filename) !== model.expected_extension) {
    throw new Error("expected_filename_invalid");
  }
  const candidate = path.resolve(scratchRoot, model.expected_filename);
  if (!isInside(scratchRoot, candidate)) throw new Error("scratch_artifact_path_escape");
  return candidate;
}

export function inspectExactScratchArtifact(scratchRoot, model) {
  let root;
  let candidate;
  try {
    root = validateScratchRoot(scratchRoot);
    candidate = expectedArtifactPath(root, model);
  } catch {
    return failure(SCRATCH_CLASSIFICATIONS.UNSAFE_PATH);
  }
  if (!fs.existsSync(candidate)) return failure(SCRATCH_CLASSIFICATIONS.MISSING);
  let linkStat;
  try { linkStat = fs.lstatSync(candidate); } catch { return failure(SCRATCH_CLASSIFICATIONS.MISSING); }
  if (linkStat.isSymbolicLink()) return failure(SCRATCH_CLASSIFICATIONS.SYMLINK);
  if (!linkStat.isFile()) return failure(SCRATCH_CLASSIFICATIONS.UNSAFE_PATH);
  if (path.extname(candidate).toLowerCase() !== SCRATCH_SMOKE_EXTENSION) return failure(SCRATCH_CLASSIFICATIONS.UNSAFE_PATH);
  if (linkStat.size > model.expected_max_bytes) return failure(SCRATCH_CLASSIFICATIONS.TOO_LARGE, { size_bytes: linkStat.size });
  let real;
  try { real = fs.realpathSync.native(candidate); } catch { return failure(SCRATCH_CLASSIFICATIONS.UNSAFE_PATH); }
  if (!isInside(root, real) || !samePath(candidate, real)) return failure(SCRATCH_CLASSIFICATIONS.UNSAFE_PATH);
  let content;
  try { content = fs.readFileSync(candidate, "utf8"); } catch { return failure(SCRATCH_CLASSIFICATIONS.UNKNOWN); }
  const parsed = parseSmokeContent(content);
  if (!parsed) return failure(SCRATCH_CLASSIFICATIONS.CONTENT_INVALID, { size_bytes: linkStat.size });
  if (parsed.nonce !== model.nonce) return failure(SCRATCH_CLASSIFICATIONS.NONCE_MISMATCH, { size_bytes: linkStat.size });
  if (content !== expectedSmokeContent(model) || parsed.run_id !== model.run_id) {
    return failure(SCRATCH_CLASSIFICATIONS.CONTENT_INVALID, { size_bytes: linkStat.size, nonce_verified: true });
  }
  return {
    classification: SCRATCH_CLASSIFICATIONS.PASS,
    found: true,
    nonce_verified: true,
    size_verified: true,
    symlink_blocked: true,
    containment_verified: true,
    size_bytes: linkStat.size,
    content_hash: crypto.createHash("sha256").update(content).digest("hex"),
  };
}

export function createScratchReviewRecord(model, artifact, workspaceRootHash, now = new Date()) {
  if (artifact.classification !== SCRATCH_CLASSIFICATIONS.PASS) throw new Error("scratch_review_requires_pass");
  const createdAt = now.toISOString();
  const expiresAt = new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString();
  return {
    review_id: `bridge-review-${crypto.randomBytes(16).toString("hex")}`,
    provider_id: "agy",
    workspace_root_hash: workspaceRootHash,
    status: "pending",
    created_at: createdAt,
    expires_at: expiresAt,
    changed_files: [],
    summary: "AGY scratch smoke artifact imported for review; no workspace diff and no apply authority.",
    decision: null,
    artifact_source: "agy_scratch",
    artifact_type: "scratch_smoke",
    artifact_metadata: {
      filename: model.expected_filename,
      content_hash: artifact.content_hash,
      size_bytes: artifact.size_bytes,
      classification: SCRATCH_CLASSIFICATIONS.PASS,
    },
  };
}

export function appendScratchReview(reviewPath, record) {
  const parent = path.dirname(path.resolve(reviewPath));
  fs.mkdirSync(parent, { recursive: true });
  fs.appendFileSync(path.resolve(reviewPath), `${JSON.stringify(record)}\n`, { encoding: "utf8" });
  return record.review_id;
}

export function hashWorkspaceIdentity(workspaceRoot) {
  return crypto.createHash("sha256").update(path.resolve(workspaceRoot)).digest("hex");
}

export function sanitizedScratchResult(overrides = {}) {
  return {
    provider: "agy", artifact_source: "agy_scratch", artifact_type: "scratch_smoke",
    execution_count: 0, scratch_artifact_found: false, nonce_verified: false,
    size_verified: false, symlink_reparse_blocked: true, path_containment_verified: false,
    active_workspace_unchanged: true, managed_workspace_unchanged: true,
    marker_unchanged: true, review_artifact_created: false, automatic_apply: false,
    automatic_build: false, automatic_flash: false, ...overrides,
  };
}

function parseSmokeContent(content) {
  const lines = content.split("\n");
  if (lines.length !== 3 || lines[0] !== "ForgeX AGY scratch import smoke completed.") return null;
  if (!lines[1].startsWith("run_id=") || !lines[2].startsWith("nonce=")) return null;
  return { run_id: lines[1].slice(7), nonce: lines[2].slice(6) };
}

function failure(classification, values = {}) {
  return { classification, found: classification !== SCRATCH_CLASSIFICATIONS.MISSING, nonce_verified: false, size_verified: false, symlink_blocked: true, containment_verified: false, ...values };
}
function isInside(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}
function samePath(left, right) { return path.resolve(left).toLowerCase() === path.resolve(right).toLowerCase(); }
function escapeRegExp(value) { return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
function compareVersion(left, right) {
  for (let i = 0; i < 3; i += 1) { if (left[i] !== right[i]) return left[i] < right[i] ? -1 : 1; }
  return 0;
}
