import assert from "node:assert/strict";
import test from "node:test";
import { validateReleaseEvidence } from "./verify-release-evidence.mjs";
const sha256 = "a".repeat(64);
function completeEvidence() {
  return { sha256, fixtureSha256: sha256, build: { schemaVersion: 1, sha256, resourceManifestSha256: sha256, signed: true, testOnly: false },
    prompts: { schema_version: 1, passed: true, installer_sha256: sha256, fixture_sha256: sha256, resource_manifest_sha256: sha256, backend_source_fingerprint: sha256, nonce: "new-test-run",
      cases: ["smoke-exclusion", "modular-monitor", "bh1750-dependency", "simple-lighting", "cloud-monitor"].map(id => ({ id, passed: true, review: { passed: true, reviewer: "reviewer", evidence: "requirement review log" }, turns: [1, 2].map(index => ({ run_id: `${id}-${index}`, passed: true, status: "completed", provider: "openai", model: "model", source_verified: true, verification_level: "model_review", source_revision: "workspace:" + "a".repeat(24), requirement_revision: "workspace:" + "a".repeat(24), task_revision: sha256, firmware_sha256: sha256, toolchain: { version: "6.2.0" } })) })) },
    hardware: { success: true, release: { installer_sha256: sha256 }, target: { platformio_environment: "esp32dev" },
      builds: Array.from({ length: 20 }, () => ({ success: true, firmware_sha256: sha256 })),
      flash_cycles: Array.from({ length: 10 }, (_, i) => ({ nonce: `nonce-${i}`, success: true, upload_returncode: 0, firmware_sha256: sha256, serial: { success: true, matched_nonce: `nonce-${i}` } })) },
    machines: { schemaVersion: 1, installer_sha256: sha256, operator: "reviewer", completed_at: "2026-09-26", cases:
      ["windows-10-x64", "windows-11-x64"].flatMap(os => ["clean-install-no-python-node", "upgrade-preserves-data", "uninstall-preserves-data", "usb-unplug-reconnect", "offline-model-recovery", "backend-crash-resume"].map(scenario => ({ os, scenario, passed: true, evidence: "recorded test log" }))) } };
}
test("release gate accepts only complete evidence for the same signed artifact", () => {
  assert.equal(validateReleaseEvidence(completeEvidence()).passed, true);
});
test("release gate rejects unsigned builds, nonce reuse and missing machine coverage", () => {
  const evidence = completeEvidence();
  evidence.build.testOnly = true;
  evidence.hardware.flash_cycles[1].nonce = evidence.hardware.flash_cycles[0].nonce;
  evidence.machines.cases.pop();
  const result = validateReleaseEvidence(evidence);
  assert.equal(result.passed, false);
  assert.equal(result.errors.length, 3);
});
test("release gate rejects serial success without the cycle's matching nonce", () => {
  const evidence = completeEvidence();
  evidence.hardware.flash_cycles[0].serial.matched_nonce = "old-boot";
  assert.equal(validateReleaseEvidence(evidence).passed, false);
});
test("release gate rejects missing, mismatched, mocked or stale prompt qualification", () => {
  for (const mutate of [value => delete value.prompts, value => value.prompts.fixture_sha256 = "b".repeat(64), value => value.prompts.cases[0].turns[0].provider = "fake_planner", value => value.prompts.cases[0].turns[0].requirement_revision = "stale", value => { delete value.prompts.cases[0].turns[0].source_revision; delete value.prompts.cases[0].turns[0].requirement_revision; }]) {
    const evidence = completeEvidence();
    mutate(evidence);
    assert.equal(validateReleaseEvidence(evidence).passed, false);
  }
});

test("release gate requires request-fidelity review in addition to compiling", () => {
  const evidence = completeEvidence();
  evidence.prompts.cases[0].review.passed = false;
  assert.equal(validateReleaseEvidence(evidence).passed, false);
  evidence.prompts.cases[0].review.passed = "false";
  assert.equal(validateReleaseEvidence(evidence).passed, false);
});

test("release gate rejects legacy quote-only verification", () => {
  const evidence = completeEvidence();
  evidence.prompts.cases[0].turns[0].verification_level = "source_reference";
  assert.equal(validateReleaseEvidence(evidence).passed, false);
});
