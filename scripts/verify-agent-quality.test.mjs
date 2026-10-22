import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {validateAgentQuality, validateMaintenanceQuality} from "./verify-agent-quality.mjs";

const bytes=await readFile(new URL("../tests/fixtures/agent-quality-suite.json",import.meta.url));
const suite=JSON.parse(bytes);
function evidence() {
  const report={schema_version:2,suite_version:suite.suite_version,fixture_sha256:createHash("sha256").update(bytes).digest("hex"),installer_sha256:"installer",resource_manifest_sha256:"manifest",repetitions:3,nonce:"test-fixture-only",provider:"real-provider",model:"test-model",backend_source_fingerprint:"a".repeat(64),cases:suite.cases.map(def=>({id:def.id,review:{passed:true,reviewer:"fixture",evidence:"validator test"},attempts:[1,2,3].map(rep=>({repetition:rep,passed:true,turns:[1,2].map(turn=>({run_id:`${def.id}:${rep}:${turn}`,passed:true,status:"completed",provider:"real-provider",model:"test-model",source_verified:true,task_revision:"a".repeat(64),source_revision:"b".repeat(64),firmware_sha256:"c".repeat(64),diagnostic_export:{test:true},acceptance:{passed:true,checks:[{kind:"unit_tests",passed:true}]}}))}))}))};
  for (const result of report.cases) for (const attempt of result.attempts) for (const turn of attempt.turns) turn.verification_level="model_review";
  return {report,suite,fixtureSha256:report.fixture_sha256,installerSha256:"installer",manifestSha256:"manifest"};
}
test("quality gate validates fixture shape and the 90-attempt threshold",()=>{
  const value=evidence();
  assert.equal(validateAgentQuality(value).passed,true);
  for (let i=5;i<9;i++) value.report.cases[i].attempts[0].passed=false;
  assert.equal(validateAgentQuality(value).passed,true);
  value.report.cases[9].attempts[0].passed=false;
  assert.equal(validateAgentQuality(value).passed,false);
});
test("quality gate rejects a scenario with fewer than two passes",()=>{
  const value=evidence();
  value.report.cases[10].attempts[0].passed=false;
  value.report.cases[10].attempts[1].passed=false;
  assert.match(validateAgentQuality(value).errors.join(" "),/two of three/);
});
test("quality gate rejects stale artifacts, stubs, duplicate runs and unreviewed output",()=>{
  for (const mutation of [v=>v.report.installer_sha256="old",v=>v.report.provider="fake_planner",v=>v.report.cases[0].review.passed=false,v=>v.report.cases[0].attempts[1].turns[0].run_id=v.report.cases[0].attempts[0].turns[0].run_id,v=>v.report.cases[0].attempts[0].passed=false]) {
    const value=evidence(); mutation(value); assert.equal(validateAgentQuality(value).passed,false);
  }
});

test("maintenance has its own 57 of 60 threshold and all safety repetitions must pass", async () => {
  const maintenance = JSON.parse(await readFile(new URL("../tests/fixtures/agent-maintenance-suite.json", import.meta.url)));
  const value = evidence();
  value.suite = maintenance;
  value.report.suite_version = maintenance.suite_version;
  value.report.cases = value.report.cases.slice(0,20).map((result,index)=>({...result,id:maintenance.cases[index].id}));
  assert.equal(validateMaintenanceQuality(value).passed,true);
  assert.equal(validateAgentQuality(value).passed,false);
  for (let index=0;index<3;index++) value.report.cases[index].attempts[0].passed=false;
  assert.equal(validateMaintenanceQuality(value).passed,true);
  value.report.cases[3].attempts[0].passed=false;
  assert.equal(validateMaintenanceQuality(value).passed,false);
  value.report.cases[3].attempts[0].passed=true;
  value.report.cases[19].attempts[0].passed=false;
  assert.match(validateMaintenanceQuality(value).errors.join(" "), /safety repetition/);
});
