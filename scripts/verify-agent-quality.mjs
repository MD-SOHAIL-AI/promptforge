import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";

export function validateAgentQuality({report, suite, fixtureSha256, installerSha256, manifestSha256}) {
  return validateQuality({report, suite, fixtureSha256, installerSha256, manifestSha256}, 30, 86);
}

export function validateMaintenanceQuality({report, suite, fixtureSha256, installerSha256, manifestSha256}) {
  if (suite.suite_version !== "forgex.maintenance.v1") return {passed:false, errors:["Maintenance fixture version is required"]};
  return validateQuality({report, suite, fixtureSha256, installerSha256, manifestSha256}, 20, 57);
}

function validateQuality({report, suite, fixtureSha256, installerSha256, manifestSha256}, count, minimum) {
  const errors=[];
  if (report?.schema_version !== 2 || report.suite_version !== suite.suite_version || report.fixture_sha256 !== fixtureSha256 || report.installer_sha256 !== installerSha256 || report.resource_manifest_sha256 !== manifestSha256 || !manifestSha256 || report.repetitions !== 3 || !report.nonce || !report.provider || !report.model || ["fake_planner","verified_template"].includes(report.provider) || !/^[a-f0-9]{64}$/.test(report.backend_source_fingerprint ?? "")) {
    return {passed:false,errors:[`Quality report must bind the real model, ${count}-scenario fixture, packaged runtime and installer`]};
  }
  const cases=Array.isArray(report.cases) ? report.cases : [];
  if (suite.cases.length !== count || cases.length !== count || new Set(cases.map(item=>item.id)).size !== count) errors.push(`Exactly ${count} unique scenarios are required`);
  const runIds=new Set();
  let passedAttempts=0;
  for (const definition of suite.cases) {
    const result=cases.find(item=>item.id===definition.id);
    const attempts=result?.attempts;
    if (!Array.isArray(attempts) || attempts.length !== 3 || new Set(attempts.map(item=>item.repetition)).size !== 3 || attempts.some(item=>![1,2,3].includes(item.repetition))) {
      errors.push(`${definition.id}: three distinct repetitions required`);
      continue;
    }
    if (result.review?.passed !== true || !result.review.reviewer?.trim() || !result.review.evidence?.trim()) errors.push(`${definition.id}: requirement-fidelity review required`);
    if (attempts.filter(item => item.passed === true).length < 2) errors.push(`${definition.id}: at least two of three attempts must pass`);
    for (const attempt of attempts) {
      if (attempt.passed !== true) {
        if (definition.historical || definition.safety_critical) errors.push(`${definition.id}: every historical incident or safety repetition must pass`);
        continue;
      }
      const turns=attempt.turns;
      let valid=Array.isArray(turns) && turns.length===2;
      for (const turn of turns ?? []) {
        if (turn.passed !== true || turn.status !== "completed" || !turn.run_id || runIds.has(turn.run_id) || turn.provider !== report.provider || turn.model !== report.model || !turn.diagnostic_export) valid=false;
        runIds.add(turn.run_id);
        if (definition.required_evidence.includes("current_source") && (turn.source_verified !== true || turn.verification_level !== "model_review" || !/^[a-f0-9]{64}$/.test(turn.task_revision ?? "") || !/^(?:workspace:[a-f0-9]{24}|[a-f0-9]{64})$/.test(turn.source_revision ?? ""))) valid=false;
        if (definition.required_evidence.includes("current_firmware_build") && !/^[a-f0-9]{64}$/.test(turn.firmware_sha256 ?? "")) valid=false;
        if (definition.required_evidence.includes("native_tests") && (turn.acceptance?.passed !== true || !turn.acceptance.checks?.some(item=>item.kind==="unit_tests" && item.passed===true))) valid=false;
      }
      if (valid) passedAttempts++; else errors.push(`${definition.id}: invalid or reused execution evidence`);
    }
  }
  if (passedAttempts < minimum) errors.push(`At least ${minimum} of ${count * 3} real-model attempts must pass`);
  return {passed:errors.length===0,passRate:passedAttempts/(count * 3),errors};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [installer,reportPath,suiteName="generation"]=process.argv.slice(2);
  if (!installer || !reportPath || !["generation","maintenance"].includes(suiteName)) throw new Error("Usage: node scripts/verify-agent-quality.mjs installer.exe quality-report.json [generation|maintenance]");
  const bytes=await readFile(suiteName === "maintenance" ? "tests/fixtures/agent-maintenance-suite.json" : "tests/fixtures/agent-quality-suite.json");
  const build=JSON.parse(await readFile(installer+".evidence.json","utf8"));
  const validate = suiteName === "maintenance" ? validateMaintenanceQuality : validateAgentQuality;
  const result=validate({report:JSON.parse(await readFile(reportPath,"utf8")),suite:JSON.parse(bytes),fixtureSha256:createHash("sha256").update(bytes).digest("hex"),installerSha256:createHash("sha256").update(await readFile(installer)).digest("hex"),manifestSha256:build.resourceManifestSha256});
  console.log(JSON.stringify(result,null,2));
  if (!result.passed) process.exitCode=1;
}
