import test from "node:test";
import assert from "node:assert/strict";
import { verificationLevels, verificationFailureMessage } from "./verification-state.ts";

test("passing requirement review does not hide risk-review failure", () => {
  const run = {classification:"CHANGESET_REVIEW_REQUIRED", requirement_report:{valid:true, verification_level:"model_review",
    risk_review:{verdict:"block",failure_code:"RISK_REVIEW_TRUNCATED"}}};
  const levels = verificationLevels(run);
  assert.equal(levels.find(row => row.label === "Requirement fidelity")?.status,"Reviewed");
  assert.equal(levels.find(row => row.label === "Change risk review")?.status,"Needs review");
  assert.match(verificationFailureMessage(run) ?? "", /could not finish/);
});

test("optional tests are not shown as failed verification", () => {
  const levels = verificationLevels({build_verified:true, requirement_report:{references_valid:true, valid:false, verification_level:"source_reference", checks:[]}});
  const status = (label: string) => levels.find(row => row.label === label)?.status;
  assert.equal(status("Firmware compilation"), "Built");
  assert.equal(status("Requirement fidelity"), "Not started");
  assert.equal(status("Logic tests"), "Not requested");
  assert.equal(status("Simulation"), "Not requested");
  assert.equal(status("Device behavior"), "Not tested");
});

test("requested execution and completion checks stay pending until evidence exists", () => {
  const run = {task_state:{requirements:Array.from({length:18}, (_, i) => ({id:`req-${i}`})), acceptance_checks:[{kind:"unit_tests"}]}, requirement_report:{checks:[], acceptance:{checks:[]}}};
  const levels = verificationLevels(run);
  assert.equal(levels.find(row => row.label === "Logic tests")?.status, "Unverified");
  assert.equal(levels.find(row => row.label === "Completion review")?.status, "Not started");
});

test("completion issues are visible despite a passing first review", () => {
  const run = {classification:"AGENT_REVIEW_REQUIRED", requirement_report:{valid:true, verification_level:"model_review", completion_review:{passed:false, issues:["Upload instructions omit a header."]}}};
  const levels = verificationLevels(run);
  assert.equal(levels.find(row => row.label === "Requirement fidelity")?.status, "Reviewed");
  assert.equal(levels.find(row => row.label === "Completion review")?.status, "Issues found");
  assert.match(verificationFailureMessage(run) ?? "", /completion review/i);
});

test("HTTP 200 does not turn citation rejection into a provider failure", () => {
  const run = {classification:"REQUIREMENTS_UNVERIFIED", provider_diagnostics:{http_status:200,last_failure_origin:null}, requirement_report:{references_valid:false}};
  assert.match(verificationFailureMessage(run) ?? "", /citation validation failed/i);
  assert.doesNotMatch(verificationFailureMessage(run) ?? "", /provider|model request/i);
  assert.match(verificationFailureMessage({...run,requirement_report:{references_valid:true}}) ?? "", /unmet/i);
});
