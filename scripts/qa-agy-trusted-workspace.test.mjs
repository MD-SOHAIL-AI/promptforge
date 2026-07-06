import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  TRUSTED_MARKER,
  guardTrustedWorkspace,
  prepareTrustedWorkspace,
  trustedWorkspacePath,
} from "./qa-agy-trusted-workspace-core.mjs";

function fixture() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-agy-trust-"));
  fs.mkdirSync(path.join(repo, ".git"));
  const active = path.join(repo, "workspace", "forgex-apply-test");
  fs.mkdirSync(path.join(active, "src"), { recursive: true });
  fs.writeFileSync(path.join(active, "QA_NOT_REAL_PROJECT.txt"), "QA only\n");
  fs.writeFileSync(path.join(active, "src", "main.cpp"), "base\n");
  return { repo, active, trusted: trustedWorkspacePath(repo, active) };
}

test("prepare creates marked contained workspace and copies the active baseline", () => {
  const value = fixture();
  const result = prepareTrustedWorkspace(value.repo, value.active, {});
  assert.equal(result.workspaceRoot, value.trusted);
  assert.equal(fs.existsSync(path.join(value.trusted, TRUSTED_MARKER)), true);
  assert.equal(fs.readFileSync(path.join(value.trusted, "src", "main.cpp"), "utf8"), "base\n");
});

test("prepare resets stale files and excludes ignored directories", () => {
  const value = fixture();
  fs.mkdirSync(path.join(value.active, "node_modules", "pkg"), { recursive: true });
  fs.writeFileSync(path.join(value.active, "node_modules", "pkg", "index.js"), "ignored\n");
  prepareTrustedWorkspace(value.repo, value.active, {});
  fs.writeFileSync(path.join(value.trusted, "stale.txt"), "stale\n");
  prepareTrustedWorkspace(value.repo, value.active, {});
  assert.equal(fs.existsSync(path.join(value.trusted, "stale.txt")), false);
  assert.equal(fs.existsSync(path.join(value.trusted, "node_modules")), false);
});

test("verify requires the marker and exact managed location", () => {
  const value = fixture();
  prepareTrustedWorkspace(value.repo, value.active, {});
  fs.unlinkSync(path.join(value.trusted, TRUSTED_MARKER));
  assert.throws(() => guardTrustedWorkspace(value.repo, value.active, value.trusted), /marker_missing/);
  assert.throws(() => guardTrustedWorkspace(value.repo, value.active, value.repo), /not_prepared/);
});

test("verify rejects a symlink escape when symlinks are available", (context) => {
  const value = fixture();
  prepareTrustedWorkspace(value.repo, value.active, {});
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-agy-outside-"));
  try { fs.symlinkSync(outside, path.join(value.trusted, "escape"), "junction"); }
  catch { context.skip("symlink creation unavailable"); return; }
  assert.throws(() => guardTrustedWorkspace(value.repo, value.active, value.trusted), /symlink/);
});
