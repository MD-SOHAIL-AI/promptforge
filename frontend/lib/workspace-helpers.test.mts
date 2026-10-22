import assert from "node:assert/strict";
import test from "node:test";

import {
  activeEnvironmentName,
  baudFromProject,
  hasPlatformIO,
  languageFor,
  latestProject,
  normalizeWorkspaceRelativePath,
  projectBoardId,
  tabStorageKey,
  toFile,
  validateWorkspaceRelativePath,
} from "./workspace-helpers.ts";
import type { ProjectResponse } from "../types/index.ts";

function project(overrides: Partial<ProjectResponse> = {}): ProjectResponse {
  return {
    id: "project-1",
    name: "Blink",
    path: "workspace/blink",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    target_board: "ESP32",
    framework: "PlatformIO",
    project_type: "platformio",
    metadata: {},
    ...overrides,
  };
}

test("workspace helper maps file language and editor tabs", () => {
  assert.equal(languageFor("src/main.cpp"), "cpp");
  assert.equal(languageFor("platformio.ini"), "ini");
  assert.equal(languageFor("README.md"), "markdown");
  assert.equal(languageFor("data/config.json"), "json");
  assert.equal(toFile("p1", "src/main.cpp", "code").language, "cpp");
  assert.equal(toFile("p1", "src/main.cpp", "generated", "cpp", "a".repeat(64)).revision, "a".repeat(64));
  assert.equal(tabStorageKey("p1"), "promptforge.tabs.p1");
});

test("workspace helper validates relative project paths", () => {
  assert.equal(validateWorkspaceRelativePath("src/main.cpp"), null);
  assert.equal(validateWorkspaceRelativePath(""), "Enter a path.");
  assert.equal(validateWorkspaceRelativePath("C:/tmp/file.cpp"), "Use a path inside the active project.");
  assert.equal(validateWorkspaceRelativePath("../secret.txt"), "Parent directory segments are not allowed.");
  assert.equal(normalizeWorkspaceRelativePath("\\src\\main.cpp"), "src/main.cpp");
});

test("workspace helper derives project hardware defaults", () => {
  const current = project({
    metadata: {
      active_environment: "esp32dev",
      platformio: {
        environments: [
          { name: "native" },
          { name: "esp32dev", board: "esp32dev", monitor_speed: 57600, upload_speed: 921600 },
        ],
      },
    },
  });

  assert.equal(activeEnvironmentName(current), "esp32dev");
  assert.equal(projectBoardId(current), "esp32dev");
  assert.equal(baudFromProject(current, "monitor_speed", 115200), 57600);
  assert.equal(hasPlatformIO(current), true);
  assert.equal(hasPlatformIO(project({ project_type: "generic" })), false);
});

test("workspace helper selects the latest project by update timestamp", () => {
  const oldProject = project({ id: "old", updated_at: "2026-01-01T00:00:00Z" });
  const newProject = project({ id: "new", updated_at: "2026-01-03T00:00:00Z" });
  assert.equal(latestProject([oldProject, newProject])?.id, "new");
});
