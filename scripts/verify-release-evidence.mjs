import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import {validateAgentQuality, validateMaintenanceQuality} from "./verify-agent-quality.mjs";
import {validateReliabilityMatrix} from "./verify-runtime-reliability.mjs";

export function validateReleaseEvidence({ sha256, build, hardware, machines, prompts, fixtureSha256 }) {
  const errors = [];
  const promptCases = ["smoke-exclusion", "modular-monitor", "bh1750-dependency", "simple-lighting", "cloud-monitor"];
  if (!prompts || prompts.schema_version !== 1 || prompts.passed !== true || prompts.installer_sha256 !== sha256 || !build.resourceManifestSha256 || prompts.resource_manifest_sha256 !== build.resourceManifestSha256 || !fixtureSha256 || prompts.fixture_sha256 !== fixtureSha256 || !prompts.nonce || !/^[a-f0-9]{64}$/.test(prompts.backend_source_fingerprint ?? "")) {
    errors.push("Recorded prompt qualification must match this installer, runtime manifest and regression fixtures");
  } else {
    const runs = new Set();
    for (const id of promptCases) {
      const item = prompts.cases?.find(candidate => candidate.id === id);
      if (item?.review?.passed !== true || typeof item.review.reviewer !== "string" || !item.review.reviewer.trim() || typeof item.review.evidence !== "string" || !item.review.evidence.trim()) errors.push(`${id}: recorded requirement-fidelity review required`);
      if (!item || item.passed !== true || item.turns?.length !== 2 || item.turns.some(turn => {
        const invalid = turn.passed !== true || turn.status !== "completed" || ["fake_planner", "verified_template"].includes(turn.provider) || !turn.provider || !turn.model || !turn.run_id || turn.source_verified !== true || turn.verification_level !== "model_review" || !/^(?:workspace:[a-f0-9]{24}|[a-f0-9]{64})$/.test(turn.source_revision ?? "") || turn.source_revision !== turn.requirement_revision || !/^[a-f0-9]{64}$/.test(turn.task_revision ?? "") || !/^[a-f0-9]{64}$/.test(turn.firmware_sha256 ?? "") || turn.toolchain?.version !== "6.2.0" || runs.has(turn.run_id);
        runs.add(turn.run_id);
        return invalid;
      })) errors.push(`${id}: real generation and follow-up build evidence required`);
    }
  }
  if (build.schemaVersion !== 1 || build.sha256 !== sha256 || build.signed !== true || build.testOnly !== false) errors.push("Signed build evidence does not match this installer");
  if (hardware.success !== true || hardware.release?.installer_sha256 !== sha256 || hardware.target?.platformio_environment !== "esp32dev") errors.push("Hardware evidence is missing, failed, or belongs to another installer");
  if (!Array.isArray(hardware.builds) || hardware.builds.length < 20 || hardware.builds.some(item => item.success !== true || !/^[a-f0-9]{64}$/.test(item.firmware_sha256 ?? ""))) errors.push("20 verified consecutive builds are required");
  const cycles = hardware.flash_cycles;
  if (!Array.isArray(cycles) || cycles.length < 10 || new Set(cycles.map(item => item.nonce)).size !== cycles.length || cycles.some(item => !item.nonce || item.success !== true || item.upload_returncode !== 0 || item.serial?.success !== true || item.serial.matched_nonce !== item.nonce || !/^[a-f0-9]{64}$/.test(item.firmware_sha256 ?? ""))) errors.push("10 unique nonce flash/serial cycles are required");
  if (machines.schemaVersion !== 1 || machines.installer_sha256 !== sha256 || !machines.operator || !machines.completed_at) errors.push("Clean-machine report does not match this installer");
  const cases = Array.isArray(machines.cases) ? machines.cases : [];
  for (const os of ["windows-10-x64", "windows-11-x64"]) {
    for (const scenario of ["clean-install-no-python-node", "upgrade-preserves-data", "uninstall-preserves-data", "usb-unplug-reconnect", "offline-model-recovery", "backend-crash-resume"]) {
      if (!cases.some(item => item.os === os && item.scenario === scenario && item.passed === true && item.evidence)) errors.push(`${os}: ${scenario} evidence required`);
    }
  }
  return { passed: errors.length === 0, errors };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [installer, hardwarePath, machinesPath, promptsPath, qualityPath, maintenancePath, reliabilityPath] = process.argv.slice(2);
  if (!installer || !hardwarePath || !machinesPath || !promptsPath || !qualityPath || !maintenancePath || !reliabilityPath) throw new Error("Usage: npm run verify:release -- installer.exe hardware.json clean-machines.json agent-prompts.json agent-quality.json agent-maintenance.json runtime-reliability.json");
  const sha256 = createHash("sha256").update(await readFile(installer)).digest("hex");
  const build = JSON.parse(await readFile(installer + ".evidence.json", "utf8"));
  const hardware = JSON.parse(await readFile(hardwarePath, "utf8"));
  const machines = JSON.parse(await readFile(machinesPath, "utf8"));
  const prompts = JSON.parse(await readFile(promptsPath, "utf8"));
  const fixtureSha256 = createHash("sha256").update(await readFile("tests/fixtures/beta-agent-prompts.json")).digest("hex");
  const result = validateReleaseEvidence({ sha256, build, hardware, machines, prompts, fixtureSha256 });
  const qualityBytes = await readFile("tests/fixtures/agent-quality-suite.json");
  const quality = validateAgentQuality({report:JSON.parse(await readFile(qualityPath,"utf8")),suite:JSON.parse(qualityBytes),fixtureSha256:createHash("sha256").update(qualityBytes).digest("hex"),installerSha256:sha256,manifestSha256:build.resourceManifestSha256});
  result.errors.push(...quality.errors);
  result.passed = result.passed && quality.passed;
  const maintenanceBytes = await readFile("tests/fixtures/agent-maintenance-suite.json");
  const maintenance = validateMaintenanceQuality({report:JSON.parse(await readFile(maintenancePath,"utf8")),suite:JSON.parse(maintenanceBytes),fixtureSha256:createHash("sha256").update(maintenanceBytes).digest("hex"),installerSha256:sha256,manifestSha256:build.resourceManifestSha256});
  result.errors.push(...maintenance.errors);
  result.passed = result.passed && maintenance.passed;
  const incidentBytes = await readFile("tests/fixtures/runtime-incident-smart-monitor.json");
  const targetBytes = await readFile("tests/fixtures/agent-target-suite.json");
  const runtimePolicy = JSON.parse(await readFile("docs/runtime-policy.generated.json", "utf8"));
  const reliability = validateReliabilityMatrix({bundle:JSON.parse(await readFile(reliabilityPath,"utf8")),
    installerSha256:sha256,manifestSha256:build.resourceManifestSha256,
    incidentFixture:JSON.parse(incidentBytes),incidentFixtureSha256:createHash("sha256").update(incidentBytes).digest("hex"),
    generationSuite:JSON.parse(qualityBytes),generationFixtureSha256:createHash("sha256").update(qualityBytes).digest("hex"),
    maintenanceSuite:JSON.parse(maintenanceBytes),maintenanceFixtureSha256:createHash("sha256").update(maintenanceBytes).digest("hex"),
    targetSuite:JSON.parse(targetBytes),targetFixtureSha256:createHash("sha256").update(targetBytes).digest("hex"),profiles:runtimePolicy.profiles});
  result.errors.push(...reliability.errors);
  result.passed = result.passed && reliability.passed;
  if (result.passed) {
    await new Promise((resolve, reject) => {
      const child = spawn("powershell.exe", ["-NoProfile", "-File", "scripts/verify-installer-signature.ps1", "-Installer", path.resolve(installer)], { stdio: "inherit", windowsHide: true });
      child.on("error", reject);
      child.on("close", code => code === 0 ? resolve() : reject(new Error("Installer signature validation failed")));
    });
  }
  console.log(JSON.stringify(result, null, 2));
  if (!result.passed) process.exitCode = 1;
}
