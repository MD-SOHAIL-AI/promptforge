import {createHash} from "node:crypto";
import {validateAgentQuality, validateMaintenanceQuality} from "./verify-agent-quality.mjs";

const hash = value => /^[a-f0-9]{64}$/.test(value ?? "");
const identity = value => `${value?.provider}/${value?.model}`;
const real = value => typeof value === "string" && value.trim() && !["fake_planner", "verified_template"].includes(value);
const text = value => typeof value === "string" && !!value.trim();
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === "object"
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
export const configurationHash = config => createHash("sha256").update(JSON.stringify(canonical(config))).digest("hex");

export function validateReliabilityMatrix({bundle, installerSha256, manifestSha256, incidentFixture, incidentFixtureSha256,
  generationSuite, generationFixtureSha256, maintenanceSuite, maintenanceFixtureSha256, targetSuite, targetFixtureSha256, profiles}) {
  const errors = [];
  profiles ??= {};
  const config = bundle?.config;
  if (!bundle || bundle.schema_version !== 1 || !config || config.schema_version !== 1
      || bundle.config_sha256 !== configurationHash(config) || bundle.installer_sha256 !== installerSha256
      || bundle.resource_manifest_sha256 !== manifestSha256 || !hash(bundle.backend_source_fingerprint)
      || bundle.incident_fixture_sha256 !== incidentFixtureSha256) {
    return {passed:false, errors:["Reliability evidence must bind the configuration, exact installer, runtime and incident fixture"], qualifiedModels:[]};
  }
  const models = config.models;
  if (!Array.isArray(models) || models.length < 2 || models.length > 16
      || models.some(item => !item || typeof item !== "object" || Array.isArray(item))
      || new Set(models.map(item => item?.id)).size !== models.length
      || new Set(models.map(identity)).size !== models.length) {
    return {passed:false, errors:["Distinct explicitly selected free and paid model configurations are required"], qualifiedModels:[]};
  }
  for (const tier of ["free", "paid"]) {
    if (!models.some(item => item.access_tier === tier && text(item.access_evidence))) errors.push(`Verified ${tier}-access configuration is required; catalog price is insufficient`);
  }
  if (!models.some(item => item.provider === incidentFixture.provider_id && item.model === incidentFixture.model_id
      && item.execution_profile === "standard")) errors.push("The original NVIDIA model and standard budget must be included");
  const results = Array.isArray(bundle.models) ? bundle.models : [];
  if (results.length !== models.length || results.some(item => !item || typeof item !== "object")
      || new Set(results.map(item => item?.id)).size !== results.length) return {passed:false,errors:["Exactly one report set per configured model is required"],qualifiedModels:[]};
  const runs = new Set();
  const qualifiedModels = [];
  function turnErrors(turn, definition, model, label) {
    const faults = [];
    if (!turn || turn.passed !== true || turn.status !== "completed" || !text(turn.run_id)
        || runs.has(turn.run_id) || turn.provider !== model.provider || turn.model !== model.model
        || turn.actual_provider !== model.provider || turn.actual_model !== model.model
        || turn.backend_source_fingerprint !== bundle.backend_source_fingerprint
        || turn.resource_manifest_sha256 !== manifestSha256 || turn.execution_profile !== model.execution_profile) faults.push("current model/runtime execution evidence");
    if (turn?.run_id) runs.add(turn.run_id);
    if (definition.required_evidence?.includes("current_source") && (turn?.source_verified !== true || turn.verification_level !== "model_review"
        || !hash(turn.task_revision) || turn.review_task_revision !== turn.task_revision
        || turn.requirement_revision !== turn.source_revision || turn.build_revision !== turn.source_revision)) faults.push("matching source, build and requirement revisions");
    if (definition.required_evidence?.includes("current_firmware_build") && !hash(turn?.firmware_sha256)) faults.push("verified firmware bytes");
    if (definition.platformio_board && (turn?.board !== definition.platformio_board || turn.environment !== definition.environment)) faults.push("requested target/environment");
    const limits = profiles[model.execution_profile];
    if (!limits || Object.entries(limits).some(([key, value]) => turn?.limits?.[key] !== value
        || typeof turn?.consumed?.[key] !== "number" || !Number.isFinite(turn.consumed[key])
        || turn.consumed[key] < 0 || turn.consumed[key] > value)) faults.push("unchanged bounded cumulative usage");
    if (!(turn?.consumed?.requests >= 1)) faults.push("a charged real model attempt");
    if (faults.length) errors.push(`${label}: missing ${faults.join(", ")}`);
  }
  function checkReport(report, model, suite, fixtureHash, kind) {
    if (!report || report.provider !== model.provider || report.model !== model.model
        || report.backend_source_fingerprint !== bundle.backend_source_fingerprint
        || report.endpoint_sha256 !== model.endpoint_sha256
        || report.execution_profile !== model.execution_profile) {
      errors.push(`${model.id}/${kind}: model/profile/runtime mismatch`); return;
    }
    const common = {report,suite,fixtureSha256:fixtureHash,installerSha256,manifestSha256};
    if (!Array.isArray(report.cases) || report.cases.some(item => !item || typeof item !== "object"
        || !Array.isArray(item.attempts) || item.attempts.some(attempt => !attempt || typeof attempt !== "object"
          || attempt.turns != null && !Array.isArray(attempt.turns)))) {
      errors.push(`${model.id}/${kind}: malformed scenario evidence`); return;
    }
    if (kind !== "targets") {
      try {
        const result = kind === "generation" ? validateAgentQuality(common) : validateMaintenanceQuality(common);
        errors.push(...result.errors.map(error => `${model.id}/${kind}: ${error}`));
      } catch {
        errors.push(`${model.id}/${kind}: malformed quality evidence`); return;
      }
    } else if (report.schema_version !== 2 || report.suite_version !== suite.suite_version || report.fixture_sha256 !== fixtureHash
        || report.installer_sha256 !== installerSha256 || report.resource_manifest_sha256 !== manifestSha256 || report.repetitions !== 3) {
      errors.push(`${model.id}/targets: target-suite provenance mismatch`);
    }
    const cases = Array.isArray(report.cases) ? report.cases : [];
    if (cases.length !== suite.cases.length || new Set(cases.map(item => item.id)).size !== cases.length) errors.push(`${model.id}/${kind}: scenario set mismatch`);
    for (const definition of suite.cases) {
      const result = cases.find(item => item.id === definition.id);
      if (!result) {errors.push(`${model.id}/${kind}/${definition.id}: missing scenario`); continue;}
      if (kind === "targets" && (result.review?.passed !== true || !text(result.review.reviewer) || !text(result.review.evidence)
          || result.attempts?.length !== 3 || new Set(result.attempts?.map(item => item.repetition)).size !== 3
          || result.attempts.some(item => ![1,2,3].includes(item.repetition) || item.passed !== true || item.turns?.length !== 2))) errors.push(`${model.id}/${definition.id}: three reviewed generation and follow-up successes required`);
      for (const attempt of result.attempts ?? []) {
        if (attempt.passed !== true) continue;
        for (const turn of attempt.turns ?? []) turnErrors(turn, definition, model, `${model.id}/${kind}/${definition.id}/${attempt.repetition}`);
      }
    }
  }
  for (const model of models) {
    const start = errors.length;
    if (!text(model.id) || !real(model.provider) || !text(model.model) || !hash(model.endpoint_sha256)
        || !["free", "paid"].includes(model.access_tier) || !text(model.access_evidence) || !profiles[model.execution_profile]) {
      errors.push(`${model.id ?? "model"}: explicit model, endpoint, access evidence and bounded profile required`); continue;
    }
    const result = results.find(item => item.id === model.id);
    if (!result || result.endpoint_sha256 !== model.endpoint_sha256) {errors.push(`${model.id}: endpoint-bound report set missing`); continue;}
    checkReport(result.generation, model, generationSuite, generationFixtureSha256, "generation");
    checkReport(result.maintenance, model, maintenanceSuite, maintenanceFixtureSha256, "maintenance");
    checkReport(result.targets, model, targetSuite, targetFixtureSha256, "targets");
    if (errors.length === start) qualifiedModels.push({provider:model.provider,model:model.model,profile:model.execution_profile});
  }
  const incident = bundle.incident;
  if (!Array.isArray(incident) || incident.length !== 3) errors.push("Three fresh original-incident attempts are required");
  else for (const [index, attempt] of incident.entries()) {
    if (!attempt || typeof attempt !== "object") {errors.push(`original incident/${index+1}: malformed evidence`); continue;}
    const incidentModel = {provider:incidentFixture.provider_id,model:incidentFixture.model_id,execution_profile:"standard"};
    turnErrors(attempt, {required_evidence:["current_source","current_firmware_build"]}, incidentModel, `original incident/${index+1}`);
    if (attempt.installer_sha256 !== installerSha256 || attempt.continued_from || attempt.request_sha256 !== incidentFixture.request_sha256
        || attempt.firmware_hash_valid !== true || attempt.completion_review_passed !== true
        || attempt.health_failures !== 0 || !Number.isFinite(attempt.health_p95_ms) || attempt.health_p95_ms > 250) errors.push(`original incident/${index+1}: exact request, fresh complete build/review and responsive backend required`);
  }
  return {passed:errors.length===0,errors,qualifiedModels};
}
