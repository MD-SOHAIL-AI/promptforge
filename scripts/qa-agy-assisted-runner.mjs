import { spawnSync } from "node:child_process";
import process from "node:process";

const confirmed = process.argv.includes("--confirm-real-agy");
const templateIndex = process.argv.indexOf("--template");
const template = templateIndex >= 0 ? process.argv[templateIndex + 1] : undefined;
if (!confirmed) finish(failure("AGY_ASSISTED_UNSAFE_ABORTED"), 2);
if (process.env.FORGEX_ENABLE_AGY_ASSISTED_RUNNER !== "1" || process.env.FORGEX_ENABLE_AGY_SCRATCH_IMPORT !== "1") finish(failure("AGY_ASSISTED_UNSAFE_ABORTED"), 2);
if (template !== "esp32-platformio-blink") finish(failure("AGY_ASSISTED_UNSAFE_ABORTED"), 2);

const child = spawnSync("python", ["-m", "backend.bridges.agy_assisted_runner_cli", "--template", template], {
  cwd: process.cwd(), encoding: "utf8", input: "", shell: false, windowsHide: true,
  timeout: 360_000, maxBuffer: 128 * 1024,
});
let result;
try { result = JSON.parse(String(child.stdout || "").trim()); }
catch { result = failure("AGY_ASSISTED_UNKNOWN_SAFE_FAILURE"); }
finish(result, child.status === 0 && result.classification === "AGY_ASSISTED_IMPORT_PASS" ? 0 : 1);

function failure(classification) {
  return {
    classification, agy_found: false, agy_version: null, run_id: "not_started",
    expected_folder_name: "not_generated", expected_folder_found: false,
    file_count: 0, total_bytes: 0, project_type: "Unknown", review_created: false,
    review_id: null, active_workspace_unchanged: true,
    manual_import_fallback_available: false, auto_apply: false, auto_build: false, auto_flash: false,
  };
}

function finish(value, code) {
  const safe = {
    classification: String(value.classification || "AGY_ASSISTED_UNKNOWN_SAFE_FAILURE"),
    agy_found: Boolean(value.agy_found),
    agy_version: typeof value.agy_version === "string" ? value.agy_version.slice(0, 80) : null,
    run_id: String(value.run_id || "unknown").replace(/[^A-Za-z0-9-]/g, "_").slice(0, 80),
    expected_folder_name: String(value.expected_folder_name || "unknown").replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 128),
    expected_folder_found: Boolean(value.expected_folder_found),
    file_count: Number(value.file_count || 0), total_bytes: Number(value.total_bytes || 0),
    project_type: String(value.project_type || "Unknown").slice(0, 40),
    review_created: Boolean(value.review_created), review_id: value.review_id ? String(value.review_id) : null,
    active_workspace_unchanged: Boolean(value.active_workspace_unchanged),
    manual_import_fallback_available: Boolean(value.manual_import_fallback_available),
    auto_apply: false, auto_build: false, auto_flash: false,
  };
  console.log(safe.classification);
  console.log(JSON.stringify(safe));
  process.exit(code);
}
