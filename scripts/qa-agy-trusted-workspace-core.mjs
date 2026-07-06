import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const TRUSTED_MARKER = ".forgex-agy-trusted-workspace.json";
export const TRUSTED_MARKER_VALUE = Object.freeze({ owner: "ForgeX", purpose: "agy-qa-trusted-workspace", version: 1 });
export const TRUST_STATUS_OPERATOR_ATTESTED_REQUIRED = "TRUST_STATUS_OPERATOR_ATTESTED_REQUIRED";
const IGNORED = new Set([".git", ".pio", ".promptforge", ".next", ".forgex", "build", "dist", "node_modules"]);

export function trustedWorkspacePath(repositoryRoot, activeWorkspace) {
  const digest = crypto.createHash("sha256").update(path.resolve(activeWorkspace).toLowerCase()).digest("hex").slice(0, 24);
  return path.join(repositoryRoot, ".promptforge", "agy-trusted-workspaces", digest);
}

export function prepareTrustedWorkspace(repositoryRoot, activeWorkspace, env = process.env) {
  const active = guardActiveWorkspace(repositoryRoot, activeWorkspace);
  const destination = trustedWorkspacePath(repositoryRoot, active);
  const managed = path.dirname(destination);
  fs.mkdirSync(managed, { recursive: true });
  if (!fs.existsSync(destination)) fs.mkdirSync(destination);
  else guardTrustedWorkspace(repositoryRoot, active, destination, env, { markerRequired: true });
  safeReset(destination, active, managed);
  fs.writeFileSync(path.join(destination, TRUSTED_MARKER), `${JSON.stringify(TRUSTED_MARKER_VALUE)}\n`, { encoding: "utf8", flag: "wx" });
  return guardTrustedWorkspace(repositoryRoot, active, destination, env);
}

export function guardActiveWorkspace(repositoryRoot, activeWorkspace) {
  const repo = fs.realpathSync.native(repositoryRoot);
  const requested = path.resolve(activeWorkspace);
  if (!fs.existsSync(requested) || fs.lstatSync(requested).isSymbolicLink()) throw new Error("active_workspace_missing_or_invalid");
  const active = fs.realpathSync.native(requested);
  const relative = path.relative(repo, active).replaceAll("\\", "/");
  if (relative !== "workspace/forgex-apply-test") throw new Error("active_workspace_not_allowlisted");
  if (!fs.statSync(active).isDirectory()) throw new Error("active_workspace_not_directory");
  return active;
}

export function guardTrustedWorkspace(repositoryRoot, activeWorkspace, requestedWorkspace, env = process.env, options = {}) {
  const repo = fs.realpathSync.native(repositoryRoot);
  const active = guardActiveWorkspace(repo, activeWorkspace);
  const expected = trustedWorkspacePath(repo, active);
  const requested = path.resolve(requestedWorkspace);
  if (requested !== expected || !fs.existsSync(requested)) throw new Error("trusted_workspace_not_prepared");
  if (fs.lstatSync(requested).isSymbolicLink()) throw new Error("trusted_workspace_symlink_rejected");
  const workspace = fs.realpathSync.native(requested);
  if (workspace !== requested) throw new Error("trusted_workspace_symlink_escape_rejected");
  const managed = fs.realpathSync.native(path.dirname(requested));
  if (path.dirname(workspace) !== managed || workspace === managed) throw new Error("trusted_workspace_containment_rejected");
  const forbidden = sensitiveRoots(repo, active, env);
  if (forbidden.has(workspace.toLowerCase())) throw new Error("trusted_workspace_sensitive_root_rejected");
  if (pathsOverlap(workspace, active)) throw new Error("trusted_workspace_active_overlap_rejected");
  rejectSymlinks(workspace);
  const marker = path.join(workspace, TRUSTED_MARKER);
  if (options.markerRequired !== false) {
    if (!fs.existsSync(marker) || fs.lstatSync(marker).isSymbolicLink() || !fs.statSync(marker).isFile()) throw new Error("trusted_workspace_marker_missing");
    let value;
    try { value = JSON.parse(fs.readFileSync(marker, "utf8")); } catch { throw new Error("trusted_workspace_marker_invalid"); }
    if (JSON.stringify(value) !== JSON.stringify(TRUSTED_MARKER_VALUE)) throw new Error("trusted_workspace_marker_invalid");
  }
  return { ok: true, repositoryRoot: repo, activeWorkspace: active, workspaceRoot: workspace, trustStatus: TRUST_STATUS_OPERATOR_ATTESTED_REQUIRED };
}

function safeReset(destination, source, managed) {
  const resolved = fs.realpathSync.native(destination);
  if (path.dirname(resolved) !== fs.realpathSync.native(managed)) throw new Error("trusted_workspace_delete_containment_rejected");
  rejectSymlinks(resolved);
  for (const entry of fs.readdirSync(resolved, { withFileTypes: true })) {
    const target = path.join(resolved, entry.name);
    if (path.dirname(path.resolve(target)) !== resolved || entry.isSymbolicLink()) throw new Error("trusted_workspace_delete_target_rejected");
    fs.rmSync(target, { recursive: entry.isDirectory(), force: false });
  }
  copyWorkspace(source, resolved);
}

function copyWorkspace(source, destination) {
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    if (entry.isSymbolicLink() || (entry.isDirectory() && IGNORED.has(entry.name.toLowerCase()))) continue;
    const from = path.join(source, entry.name);
    const to = path.join(destination, entry.name);
    if (entry.isDirectory()) {
      fs.mkdirSync(to);
      copyWorkspace(from, to);
    } else if (entry.isFile()) {
      fs.copyFileSync(from, to, fs.constants.COPYFILE_EXCL);
    } else {
      throw new Error("active_workspace_entry_rejected");
    }
  }
}

function rejectSymlinks(root) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const child = path.join(root, entry.name);
    if (entry.isSymbolicLink()) throw new Error("trusted_workspace_symlink_rejected");
    if (entry.isDirectory()) rejectSymlinks(child);
    else if (!entry.isFile()) throw new Error("trusted_workspace_entry_rejected");
  }
}

function sensitiveRoots(repo, active, env) {
  const home = path.resolve(os.homedir());
  return new Set([
    repo, active, home, path.join(home, "Desktop"), path.parse(repo).root,
    env.OneDrive, env.OneDriveConsumer, env.OneDriveCommercial,
  ].filter(Boolean).map((item) => path.resolve(item).toLowerCase()));
}

function pathsOverlap(first, second) {
  const relative = path.relative(first, second);
  const reverse = path.relative(second, first);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative)) || reverse === "" || (!reverse.startsWith("..") && !path.isAbsolute(reverse));
}
