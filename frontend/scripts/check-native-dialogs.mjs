import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const browserObjectCalls = ["prompt", "confirm", "alert"].map((name) => `window\\.${name}`);
const globalCalls = ["prompt", "confirm", "alert"].map((name) => `\\b${name}\\(`);
const forbidden = new RegExp([...browserObjectCalls, ...globalCalls].join("|"));
const extensions = new Set([".ts", ".tsx"]);

function walk(dir) {
  const entries = readdirSync(dir, { withFileTypes: true });
  return entries.flatMap((entry) => {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".next") return [];
      return walk(full);
    }
    return extensions.has(entry.name.slice(entry.name.lastIndexOf("."))) ? [full] : [];
  });
}

function validateWorkspaceRelativePath(value) {
  const path = value.trim().replaceAll("\\", "/");
  if (!path) return "Enter a path.";
  if (path.startsWith("/") || /^[a-zA-Z]:\//.test(path)) return "Use a path inside the active project.";
  const parts = path.split("/").filter(Boolean);
  if (parts.length === 0) return "Enter a path.";
  if (parts.some((part) => part === "..")) return "Parent directory segments are not allowed.";
  if (parts.some((part) => part === ".")) return "Current directory segments are not allowed.";
  return null;
}

const violations = [];
for (const file of walk(root)) {
  const content = readFileSync(file, "utf8");
  if (forbidden.test(content)) violations.push(file);
}

const validationCases = [
  ["", false],
  ["   ", false],
  ["../bad.cpp", false],
  ["src/../bad.cpp", false],
  ["C:/tmp/bad.cpp", false],
  ["/tmp/bad.cpp", false],
  ["src/test.cpp", true],
  ["include", true],
];

const validationFailures = validationCases.filter(([value, expected]) => {
  return (validateWorkspaceRelativePath(value) === null) !== expected;
});

if (violations.length > 0 || validationFailures.length > 0) {
  console.error(JSON.stringify({ violations, validationFailures }, null, 2));
  process.exit(1);
}

console.log("Native dialog source check passed.");
