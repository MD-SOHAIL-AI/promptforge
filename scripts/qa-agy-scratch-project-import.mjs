import { spawnSync } from "node:child_process";
import process from "node:process";

const index = process.argv.indexOf("--source");
const source = index >= 0 ? process.argv[index + 1] : undefined;
if (!source) {
  printResult({
    classification: "AGY_SCRATCH_SOURCE_MISSING", source_inside_scratch_root: false,
    source_name: "unknown", file_count: 0, total_bytes: 0, blocked_files_count: 0,
    managed_sandbox_created: false, created_file_count: 0, modified_file_count: 0,
    deleted_file_count: 0, review_created: false, review_id: null,
    active_workspace_unchanged: true, auto_apply: false, auto_build: false, auto_flash: false,
  });
  process.exit(2);
}

const child = spawnSync("python", ["-m", "backend.bridges.agy_scratch_project_import_cli", "--source", source], {
  cwd: process.cwd(), encoding: "utf8", input: "", shell: false, windowsHide: true,
  timeout: 60_000, maxBuffer: 128 * 1024,
});
let result;
try {
  result = JSON.parse(String(child.stdout || "").trim());
} catch {
  result = {
    classification: "AGY_SCRATCH_UNKNOWN_SAFE_FAILURE", source_inside_scratch_root: false,
    source_name: "unknown", file_count: 0, total_bytes: 0, blocked_files_count: 0,
    managed_sandbox_created: false, created_file_count: 0, modified_file_count: 0,
    deleted_file_count: 0, review_created: false, review_id: null,
    active_workspace_unchanged: true, auto_apply: false, auto_build: false, auto_flash: false,
  };
}
printResult(result);
process.exit(child.status === 0 && result.classification === "AGY_SCRATCH_IMPORT_PASS" ? 0 : 1);

function printResult(value) {
  const safe = {
    classification: String(value.classification || "AGY_SCRATCH_UNKNOWN_SAFE_FAILURE"),
    source_inside_scratch_root: Boolean(value.source_inside_scratch_root),
    source_name: String(value.source_name || "unknown").replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 128),
    file_count: Number(value.file_count || 0), total_bytes: Number(value.total_bytes || 0),
    blocked_files_count: Number(value.blocked_files_count || 0),
    managed_sandbox_created: Boolean(value.managed_sandbox_created),
    created_file_count: Number(value.created_file_count || 0),
    modified_file_count: Number(value.modified_file_count || 0),
    deleted_file_count: Number(value.deleted_file_count || 0),
    review_created: Boolean(value.review_created), review_id: value.review_id ? String(value.review_id) : null,
    active_workspace_unchanged: Boolean(value.active_workspace_unchanged),
    auto_apply: false, auto_build: false, auto_flash: false,
  };
  console.log(safe.classification);
  console.log(JSON.stringify(safe));
}
