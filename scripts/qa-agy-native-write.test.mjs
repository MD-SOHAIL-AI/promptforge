import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  DANGEROUS_PERMISSION_FLAG, GENERIC_SMOKE_NAME, MAX_NATIVE_ATTEMPTS, NATIVE_SMOKE_CONTENT,
  NATIVE_SMOKE_NAME, assertSafeNativeArgs, classifyNativeResult, compareNativeBaselines,
  classifyAuthProbe, nativeBaseline, nativeFollowupDecision, sanitizedInvocationMatrix, sanitizedNativeResult, validateInvocationMatrix,
} from "./qa-agy-native-write-core.mjs";

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-agy-native-"));
  fs.writeFileSync(path.join(root, ".forgex-agy-trusted-workspace.json"), "marker\n");
  fs.writeFileSync(path.join(root, "base.txt"), "base\n");
  return root;
}
function baseResult(overrides = {}) {
  return { status: 0, signal: null, errorCode: null, stdout: "", stderr: "", diff: { created: [], modified: [], deleted: [], changedCount: 0, markerUnchanged: true }, smokeExists: false, smokeMatches: false, activeUnchanged: true, ...overrides };
}

test("native invocation uses direct fixed print argv", () => assert.equal(assertSafeNativeArgs(["-p", "runtime"]), true));
test("native invocation blocks dangerous permission flags", () => assert.throws(() => assertSafeNativeArgs(["-p", DANGEROUS_PERMISSION_FLAG]), /dangerous/));
test("native invocation blocks interactive and arbitrary argv", () => assert.throws(() => assertSafeNativeArgs(["--prompt-interactive", "runtime"]), /invocation/));
test("native no-change is classified safely", () => assert.equal(classifyNativeResult(baseResult()), "NATIVE_NO_CHANGES"));
test("native permission output is classified in memory", () => assert.equal(classifyNativeResult(baseResult({ stderr: "workspace permission approval required" })), "NATIVE_PERMISSION_BLOCKED"));
test("native authentication output is classified in memory", () => assert.equal(classifyNativeResult(baseResult({ stderr: "login authentication required" })), "NATIVE_AUTH_BLOCKED"));
test("native unsupported invocation is classified", () => assert.equal(classifyNativeResult(baseResult({ stderr: "unknown command" })), "NATIVE_INVOCATION_UNSUPPORTED"));
test("native provider failure is classified", () => assert.equal(classifyNativeResult(baseResult({ status: 1, stderr: "provider failed" })), "NATIVE_PROVIDER_ERROR"));
test("native pass requires exact created smoke file", () => assert.equal(classifyNativeResult(baseResult({ diff: { created: [NATIVE_SMOKE_NAME], modified: [], deleted: [], changedCount: 1, markerUnchanged: true }, smokeExists: true, smokeMatches: true })), "NATIVE_WRITE_PASS"));
test("native pass rejects wrong content", () => assert.equal(classifyNativeResult(baseResult({ diff: { created: [NATIVE_SMOKE_NAME], modified: [], deleted: [], changedCount: 1, markerUnchanged: true }, smokeExists: true })), "NATIVE_UNKNOWN_SAFE_FAILURE"));
test("native pass rejects active workspace mutation", () => assert.equal(classifyNativeResult(baseResult({ diff: { created: [NATIVE_SMOKE_NAME], modified: [], deleted: [], changedCount: 1, markerUnchanged: true }, smokeExists: true, smokeMatches: true, activeUnchanged: false })), "NATIVE_UNKNOWN_SAFE_FAILURE"));
test("native baseline tracks relative hashes and marker separately", () => { const root = fixture(); const value = nativeBaseline(root); assert.ok(value.markerHash); assert.deepEqual(value.files.map((item) => item.relative_path), ["base.txt"]); });
test("native baseline detects created modified and deleted files", () => { const root = fixture(); const before = nativeBaseline(root); fs.writeFileSync(path.join(root, "base.txt"), "changed\n"); fs.writeFileSync(path.join(root, NATIVE_SMOKE_NAME), NATIVE_SMOKE_CONTENT); const after = nativeBaseline(root); fs.unlinkSync(path.join(root, "base.txt")); const final = nativeBaseline(root); assert.deepEqual(compareNativeBaselines(before, after).modified, ["base.txt"]); assert.deepEqual(compareNativeBaselines(after, final).deleted, ["base.txt"]); });
test("native baseline confirms both smoke files can be absent", () => { const root = fixture(); const names = nativeBaseline(root).files.map((item) => item.relative_path); assert.equal(names.includes(NATIVE_SMOKE_NAME), false); assert.equal(names.includes(GENERIC_SMOKE_NAME), false); });
test("native matrix caps total attempts", () => { assert.equal(validateInvocationMatrix(Array(MAX_NATIVE_ATTEMPTS).fill(["-p", "runtime"])), true); assert.throws(() => validateInvocationMatrix(Array(MAX_NATIVE_ATTEMPTS + 1).fill(["-p", "runtime"])), /limit/); });
test("native matrix validates every attempt", () => assert.throws(() => validateInvocationMatrix([["-p", "ok"], ["exec", "bad"]]), /invocation/));
test("sanitized native result contains no prompt or output", () => { const value = sanitizedNativeResult("NATIVE_NO_CHANGES", { created: [], modified: [], deleted: [], changedCount: 0, markerUnchanged: true }, 1, false, true); const text = JSON.stringify(value); assert.doesNotMatch(text, /prompt|stdout|stderr|instruction/i); });
test("smoke content fixture is exact", () => assert.equal(NATIVE_SMOKE_CONTENT, "ForgeX native AGY minimal write reproduction completed.\n"));
test("auth status unavailable is explicit", () => assert.equal(classifyAuthProbe({ available: false }), "NATIVE_AUTH_STATUS_UNAVAILABLE"));
test("auth blocked output is classified in memory", () => assert.equal(classifyAuthProbe({ available: true, status: 1, stderr: "login required" }), "NATIVE_AUTH_BLOCKED"));
test("auth ready fake safe output is classified", () => assert.equal(classifyAuthProbe({ available: true, status: 0, stdout: "session ready" }), "NATIVE_AUTH_READY"));
test("official invocation matrix is sanitized and bounded", () => { const matrix = sanitizedInvocationMatrix(); assert.equal(matrix.length, 5); assert.equal(matrix[0].variant, "agy_print"); assert.doesNotMatch(JSON.stringify(matrix), /prompt|stdout|stderr|path/i); });
test("native write pass requires a ForgeX generic rerun", () => assert.deepEqual(nativeFollowupDecision("NATIVE_WRITE_PASS"), { rerun_generic: true, outcome: "generic_rerun_required", comparison_diagnostics: [] }));
test("native pass with generic failure emits bounded comparison diagnostics", () => { const value = nativeFollowupDecision("NATIVE_WRITE_PASS", "GENERIC_NO_CHANGES"); assert.equal(value.outcome, "native_pass_generic_failed"); assert.equal(value.comparison_diagnostics.length, 8); assert.doesNotMatch(JSON.stringify(value), /stdout|stderr|absolute|content/i); });
