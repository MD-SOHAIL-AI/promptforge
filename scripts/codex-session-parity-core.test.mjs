import test from "node:test";
import assert from "node:assert/strict";

import { classifySessionParity, launcherKind, parseCodexStatus } from "./codex-session-parity-core.mjs";
import { buildCodexSafeUserEnv, buildInheritedMinusSecretsEnv } from "./codex-safe-user-env.mjs";

test("parser classifies official signed-in phrases", () => {
  assert.equal(parseCodexStatus("Logged in using ChatGPT", 0).authStatus, "signed_in");
  assert.equal(parseCodexStatus("Authenticated using ChatGPT", 0).authStatus, "signed_in");
});
test("parser checks negative phrases before positive phrases", () => {
  assert.equal(parseCodexStatus("Not logged in", 0).authStatus, "signed_out");
  assert.equal(parseCodexStatus("Login required", 1).authStatus, "signed_out");
});
test("parser leaves empty and unknown output unknown", () => {
  assert.equal(parseCodexStatus("", 0).authStatus, "unknown");
  assert.equal(parseCodexStatus("account state unavailable", 0).authStatus, "unknown");
});
test("session classifications are deterministic", () => {
  const values = (statuses) => Object.entries(statuses).map(([runner_name, auth_status]) => ({ runner_name, auth_status }));
  assert.equal(classifySessionParity(values({ shared_aligned_runner: "signed_out", direct_codex_runner: "signed_out", resolved_executable_runner: "signed_out", windows_cmd_parity_runner: "signed_in" })), "CODEX_SESSION_PARITY_CMD_ONLY_SIGNED_IN");
  assert.equal(classifySessionParity(values({ shared_aligned_runner: "signed_out", direct_codex_runner: "signed_in", resolved_executable_runner: "signed_in", windows_cmd_parity_runner: "signed_out" })), "CODEX_SESSION_PARITY_DIRECT_ONLY_SIGNED_IN");
  assert.equal(classifySessionParity(values({ a: "signed_out", b: "signed_out" })), "CODEX_SESSION_PARITY_ALL_SIGNED_OUT");
  assert.equal(classifySessionParity(values({ a: "signed_in", b: "signed_out" })), "CODEX_SESSION_PARITY_MISMATCH");
});
test("launcher diagnostics expose kind without requiring a path", () => {
  assert.equal(launcherKind("C:/tools/codex.exe"), "exe");
  assert.equal(launcherKind("C:/tools/codex.cmd"), "cmd_shim");
  assert.equal(launcherKind("C:/tools/codex.ps1"), "ps1_shim");
});
test("environment profiles remove secret-like names and preserve safe user names", () => {
  const source = { PATH: "x", USERPROFILE: "u", USERNAME: "n", OPENAI_API_KEY: "forbidden", SESSION_TOKEN: "forbidden" };
  const safe = buildCodexSafeUserEnv(source);
  const inherited = buildInheritedMinusSecretsEnv(source);
  assert.deepEqual(safe, { PATH: "x", USERPROFILE: "u", USERNAME: "n" });
  assert.deepEqual(inherited.env, { PATH: "x", USERPROFILE: "u", USERNAME: "n" });
  assert.equal(inherited.removed, 2);
});
