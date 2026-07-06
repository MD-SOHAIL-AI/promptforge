import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";

import { detectInstalledVersion, locateAgy } from "./qa-agy-generic-live-core.mjs";
import {
  TRUST_STATUS_OPERATOR_ATTESTED_REQUIRED,
  guardTrustedWorkspace,
  prepareTrustedWorkspace,
  trustedWorkspacePath,
} from "./qa-agy-trusted-workspace-core.mjs";

const repositoryRoot = path.resolve(import.meta.dirname, "..");
const activeWorkspace = path.join(repositoryRoot, "workspace", "forgex-apply-test");
const prepare = process.argv.includes("--prepare");
const verify = process.argv.includes("--verify");

if (prepare === verify) fail("BLOCKED_UNKNOWN_SAFE_REASON");

try {
  const result = prepare
    ? prepareTrustedWorkspace(repositoryRoot, activeWorkspace)
    : guardTrustedWorkspace(repositoryRoot, activeWorkspace, trustedWorkspacePath(repositoryRoot, activeWorkspace));
  const installation = locateAgy(spawnSync);
  const version = installation.installed ? detectInstalledVersion(installation.resolvedPath, spawnSync) : null;
  if (prepare) {
    console.log("PREPARED");
    console.log("A ForgeX-managed AGY workspace has been prepared.");
    console.log("Open AGY manually in the managed workspace shown below and approve/trust this workspace only.");
    console.log("Do not trust your real project.");
    console.log(`Local-only managed workspace: ${result.workspaceRoot}`);
    console.log("After trust is complete, rerun:");
    console.log("npm.cmd run qa:agy-trust-workspace -- --verify");
    console.log("No AGY edit was executed.");
  } else {
    console.log(TRUST_STATUS_OPERATOR_ATTESTED_REQUIRED);
    console.log(`Trusted workspace root: ${result.ok ? "present" : "missing"}`);
    console.log(`Marker: ${result.ok ? "present" : "missing"}`);
    console.log(`AGY installation: ${installation.installed ? "installed" : "not_installed"}`);
    console.log(`AGY version: ${version || "not_detected"}`);
    console.log(`AGY authentication: ${process.env.FORGEX_AGY_AUTHENTICATED === "1" ? "attested" : "not_attested"}`);
    console.log("AGY does not expose a safe machine-readable per-folder trust check; attest only after manual approval.");
  }
} catch (error) {
  fail(error instanceof Error && /^[a-z0-9_]+$/i.test(error.message) ? error.message : "BLOCKED_UNKNOWN_SAFE_REASON");
}

function fail(reason) {
  console.error(reason);
  process.exit(2);
}
