import test from "node:test";
import assert from "node:assert/strict";
import { canApplyAgentChanges, configurationRepairMessage, formatRetryDelay, modelPauseMessage, recoveryRequestKey } from "./agent-recovery.ts";

test("apply requires a reviewable completed run and its pending change set", () => {
  const run = {status:"completed", classification:"CHANGESET_REVIEW_REQUIRED", change_set_id:"a"};
  const changes = {change_set_id:"a",status:"pending"};
  assert.equal(canApplyAgentChanges(run, changes), true);
  for (const status of ["draft", "applied", "discarded"]) {
    assert.equal(canApplyAgentChanges(run, {...changes,status}), false);
  }
  assert.equal(canApplyAgentChanges(run, {...changes,change_set_id:"old"}), false);
  assert.equal(canApplyAgentChanges(run, null), false);
  assert.equal(canApplyAgentChanges({...run,status:"failed"},changes), false);
  assert.equal(canApplyAgentChanges({...run,classification:"CHANGESET_APPLIED"},changes), false);
});

test("retry delays are readable", () => {
  assert.equal(formatRetryDelay(5000), "about 5 seconds");
  assert.equal(formatRetryDelay(600000), "about 10 minutes");
  assert.equal(formatRetryDelay(86400000), "about 1 day");
});

test("configuration failures including saved legacy runs request repair rather than provider retry", () => {
  for (const build_result of [{error_code: "BUILD_CONFIGURATION_MISSING"}, {error_code: "BUILD_CONFIGURATION_INVALID"},
    {error_code: "DEPENDENCY_VALIDATION_FAILED", message: "Project is missing platformio.ini"}]) {
    assert.match(configurationRepairMessage({build_result}) ?? "", /Continue generation.*requested board/);
  }
  assert.equal(configurationRepairMessage({build_result: {error_code: "DEPENDENCY_REGISTRY_UNAVAILABLE"}}), null);
});

test("paused model message guarantees no silent switch", () => {
  const message = modelPauseMessage({ status:"paused_model_unavailable", provider_diagnostics:{ outbound_request_count:1, request_reached_provider:true, retry_after_ms:5000 } });
  assert.match(message ?? "", /did not switch providers/);
});

test("retired models require another selection and preserve saved work", () => {
  const message = modelPauseMessage({status: "failed", classification: "MODEL_RETIRED"});
  assert.match(message ?? "", /retired.*Choose another/);
  assert.match(message ?? "", /saved work is preserved/);
  assert.doesNotMatch(message ?? "", /temporarily|Retry the same/);
});

test("recovery keys distinguish explicit selections and remain bounded", async () => {
  const selected = {provider_id:"ollama",model_id:"x".repeat(256)};
  const plain = await recoveryRequestKey("resume", "run", "c".repeat(64));
  const first = await recoveryRequestKey("resume", "run", "c".repeat(64), selected);
  assert.equal(first, await recoveryRequestKey("resume", "run", "c".repeat(64), selected));
  assert.notEqual(first, plain);
  assert.notEqual(first, await recoveryRequestKey("resume", "run", "c".repeat(64), {...selected,model_id:"another"}));
  assert.ok(first.length < 128);
});
