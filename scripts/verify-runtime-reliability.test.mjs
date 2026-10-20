import assert from "node:assert/strict";
import test from "node:test";
import {readFile} from "node:fs/promises";
import {configurationHash, validateReliabilityMatrix} from "./verify-runtime-reliability.mjs";

const load = async name => JSON.parse(await readFile(new URL(`../tests/fixtures/${name}`, import.meta.url), "utf8"));
const generation = await load("agent-quality-suite.json");
const maintenance = await load("agent-maintenance-suite.json");
const targets = await load("agent-target-suite.json");
const incident = await load("runtime-incident-smart-monitor.json");
const fingerprint = "a".repeat(64), installer = "b".repeat(64), manifest = "c".repeat(64), fixture = "d".repeat(64);
const profiles = {standard:{elapsed_seconds:900,tool_calls_used:100,requests:120,output_tokens:245760,input_bytes:8000000}};

function evidence() {
  let run = 0;
  const models = [
    {id:"original",provider:incident.provider_id,model:incident.model_id,access_tier:"free",access_evidence:"Validator fixture: explicit test-account declaration",endpoint_sha256:fingerprint,execution_profile:"standard"},
    {id:"paid",provider:"fixture-paid-provider",model:"fixture-model",access_tier:"paid",access_evidence:"Validator fixture: explicit paid-account declaration",endpoint_sha256:manifest,execution_profile:"standard"},
  ];
  function turn(model, definition={}) {
    return {run_id:`validator-fixture-${++run}`,passed:true,status:"completed",provider:model.provider,model:model.model,
      actual_provider:model.provider,actual_model:model.model,backend_source_fingerprint:fingerprint,
      resource_manifest_sha256:manifest,installer_sha256:installer,execution_profile:"standard",limits:{...profiles.standard},
      consumed:{elapsed_seconds:10,tool_calls_used:3,requests:2,output_tokens:64,input_bytes:256},
      source_verified:true,verification_level:"model_review",source_revision:fingerprint,requirement_revision:fingerprint,
      build_revision:fingerprint,task_revision:fixture,review_task_revision:fixture,firmware_sha256:manifest,
      diagnostic_export:{validator_fixture:true},acceptance:{passed:true,checks:[{kind:"unit_tests",passed:true}]},
      board:definition.platformio_board,environment:definition.environment};
  }
  function report(model, suite) {
    return {schema_version:2,suite_version:suite.suite_version,fixture_sha256:fixture,installer_sha256:installer,
      resource_manifest_sha256:manifest,backend_source_fingerprint:fingerprint,endpoint_sha256:model.endpoint_sha256,
      execution_profile:"standard",nonce:"validator-fixture-only",repetitions:3,provider:model.provider,model:model.model,
      cases:suite.cases.map(definition=>({id:definition.id,review:{passed:true,reviewer:"Validator fixture",evidence:"Synthetic unit-test evidence; never release qualification"},
        attempts:[1,2,3].map(repetition=>({repetition,passed:true,turns:[turn(model,definition),turn(model,definition)]}))}))};
  }
  const config = {schema_version:1,models};
  const bundle = {schema_version:1,config,config_sha256:configurationHash(config),installer_sha256:installer,
    resource_manifest_sha256:manifest,backend_source_fingerprint:fingerprint,incident_fixture_sha256:fixture,
    models:models.map(model=>({id:model.id,endpoint_sha256:model.endpoint_sha256,generation:report(model,generation),
      maintenance:report(model,maintenance),targets:report(model,targets)})),
    incident:[1,2,3].map(()=>({...turn(models[0]),request_sha256:incident.request_sha256,continued_from:null,
      firmware_hash_valid:true,completion_review_passed:true,health_failures:0,health_p95_ms:35}))};
  return {bundle,installerSha256:installer,manifestSha256:manifest,incidentFixture:incident,
    incidentFixtureSha256:fixture,generationSuite:generation,generationFixtureSha256:fixture,
    maintenanceSuite:maintenance,maintenanceFixtureSha256:fixture,targetSuite:targets,targetFixtureSha256:fixture,profiles};
}

test("per-model gate requires separate generation, maintenance, targets and original-incident evidence",()=>{
  const result=validateReliabilityMatrix(evidence());
  assert.equal(result.passed,true,result.errors.join("\n"));
  assert.equal(result.qualifiedModels.length,2);
});

test("free and paid results cannot substitute for one another or reuse a run",()=>{
  const value=evidence();
  value.bundle.models[1].generation=structuredClone(value.bundle.models[0].generation);
  assert.equal(validateReliabilityMatrix(value).passed,false);
  const reused=evidence();
  reused.bundle.models[1].maintenance.cases[0].attempts[0].turns[0].run_id=reused.bundle.models[0].generation.cases[0].attempts[0].turns[0].run_id;
  assert.match(validateReliabilityMatrix(reused).errors.join(" "),/execution evidence/);
});

test("release refuses inflated budgets, stale review, a proxy endpoint, absent paid access or a failed target",()=>{
  for (const mutate of [
    value=>{value.bundle.models[0].generation.cases[0].attempts[0].turns[0].limits.elapsed_seconds=2700;},
    value=>{value.bundle.models[0].generation.cases[0].attempts[0].turns[0].review_task_revision="stale";},
    value=>{value.bundle.models[0].generation.endpoint_sha256="proxy";},
    value=>{value.bundle.config.models[1].access_tier="free";value.bundle.config_sha256=configurationHash(value.bundle.config);},
    value=>{value.bundle.models[1].targets.cases[0].attempts[0].passed=false;},
    value=>{value.bundle.incident[0].completion_review_passed=false;},
    value=>{value.bundle.incident[0].continued_from="earlier-attempt";},
    value=>{value.bundle.incident[0].request_sha256="modified-request";},
    value=>{value.bundle.incident[0].consumed.requests=0;},
  ]) {const value=evidence();mutate(value);assert.equal(validateReliabilityMatrix(value).passed,false);}
});

test("malformed or missing evidence is a failed gate without crashing",()=>{
  for (const mutate of [
    value=>{value.bundle.models[0].generation.cases[0]=null;},
    value=>{value.bundle.config.models[0]=null;value.bundle.config_sha256=configurationHash(value.bundle.config);},
    value=>{value.bundle.models[0]=null;},
    value=>{value.bundle.incident[0]=null;},
    value=>{value.bundle.models[1].maintenance=null;},
  ]) {const value=evidence();mutate(value);assert.equal(validateReliabilityMatrix(value).passed,false);}
});
