import { lstat, readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ZIP_END_SIGNATURE = 0x06054b50;
const ZIP_CENTRAL_SIGNATURE = 0x02014b50;
const ZIP_MAX_COMMENT_LENGTH = 0xffff;

const FORBIDDEN_DIRECTORY_RULES = new Map([
  ["node_modules", ["dependency_tree", "dependency installation directory"]],
  [".next", ["frontend_build", "Next.js build output"]],
  ["dist", ["distribution_output", "compiled distribution output"]],
  ["hardware-signoff-reports", ["release_evidence_output", "hardware sign-off report output"]],
  [".venv", ["virtual_environment", "Python virtual environment"]],
  ["venv", ["virtual_environment", "Python virtual environment"]],
  ["virtualenv", ["virtual_environment", "Python virtual environment"]],
  [".pio", ["firmware_build", "PlatformIO build state"]],
]);

const CACHE_DIRECTORY_NAMES = new Set([
  ".cache",
  "cache",
  "caches",
  "__pycache__",
  ".pytest_cache",
  ".mypy_cache",
  ".ruff_cache",
  ".eslintcache",
  ".parcel-cache",
  ".turbo",
]);

const FIRMWARE_EXTENSIONS = new Set([".bin", ".elf", ".hex", ".map", ".uf2"]);
const ALLOWED_ENV_TEMPLATES = new Set([".env.example", ".env.sample", ".env.template"]);
const ALLOWED_WORKSPACE_FILES = new Set([
  "workspace/.gitkeep",
  "workspace/artifacts/.gitkeep",
  "workspace/builds/.gitkeep",
  "workspace/logs/.gitkeep",
  "workspace/projects/.gitkeep",
]);

const SOURCE_TREE_LOCAL_DIRECTORIES = new Set([
  ".forgex",
  ".pytest_cache",
  ".venv",
  "dist",
  "hardware-signoff-reports",
  "node_modules",
]);

function normalizedPath(value) {
  return String(value).replaceAll("\\", "/").replace(/^\.\/+/, "").replace(/^\/+/, "");
}

function violation(code, description, releasePath) {
  return { code, description, path: normalizedPath(releasePath) };
}

/**
 * Return the first release-policy violation for a relative path, or null.
 * Matching is case-insensitive because the certified release platform is Windows.
 */
export function classifyReleasePath(releasePath, { isDirectory = false, isSymbolicLink = false } = {}) {
  const original = String(releasePath);
  const normalized = normalizedPath(original);
  const segments = normalized.split("/").filter(Boolean);
  const lowerSegments = segments.map((segment) => segment.toLowerCase());
  const lowerPath = lowerSegments.join("/");
  const basename = lowerSegments.at(-1) ?? "";

  if (/^[a-z]:[\\/]/i.test(original) || original.startsWith("/") || segments.includes("..")) {
    return violation("unsafe_path", "absolute or parent-traversing archive path", original);
  }

  if (isSymbolicLink) {
    return violation("symbolic_link", "symbolic links are not allowed in Windows release content", normalized);
  }

  for (const segment of lowerSegments) {
    const rule = FORBIDDEN_DIRECTORY_RULES.get(segment);
    if (rule) {
      return violation(rule[0], rule[1], normalized);
    }
    if (CACHE_DIRECTORY_NAMES.has(segment) || /^\.(?:pytest|mypy|ruff|tox|nox)_cache$/.test(segment)) {
      return violation("cache", "generated cache directory", normalized);
    }
  }

  if (basename === ".env" || (basename.startsWith(".env.") && !ALLOWED_ENV_TEMPLATES.has(basename))) {
    return violation("environment_file", "local environment or secret-bearing configuration", normalized);
  }

  if (lowerSegments[0] === "workspace" && !isDirectory) {
    if (!ALLOWED_WORKSPACE_FILES.has(lowerPath)) {
      return violation("workspace_runtime", "runtime workspace data", normalized);
    }
  }

  if (!isDirectory && FIRMWARE_EXTENSIONS.has(path.posix.extname(basename))) {
    return violation("firmware_output", "generated firmware or linker output", normalized);
  }

  if (!isDirectory && basename.endsWith(".zip") && basename.includes("review")) {
    return violation("nested_review_archive", "nested review ZIP", normalized);
  }

  return null;
}

function isSourceTreeLocalArtifact(releasePath, { isDirectory = false } = {}) {
  const normalized = normalizedPath(releasePath);
  const lowerSegments = normalized.split("/").filter(Boolean).map((segment) => segment.toLowerCase());
  const lowerPath = lowerSegments.join("/");
  const basename = lowerSegments.at(-1) ?? "";

  if (lowerSegments.length === 0) {
    return false;
  }

  if (lowerSegments.some((segment) => CACHE_DIRECTORY_NAMES.has(segment))) {
    return true;
  }

  if (lowerSegments.some((segment) => FORBIDDEN_DIRECTORY_RULES.has(segment))) {
    return true;
  }

  if (SOURCE_TREE_LOCAL_DIRECTORIES.has(lowerSegments[0])) {
    return true;
  }

  if (basename === ".env" || (basename.startsWith(".env.") && !ALLOWED_ENV_TEMPLATES.has(basename))) {
    return true;
  }

  if (!isDirectory && lowerSegments[0] === "workspace" && !ALLOWED_WORKSPACE_FILES.has(lowerPath)) {
    return true;
  }

  if (!isDirectory && FIRMWARE_EXTENSIONS.has(path.posix.extname(basename))) {
    return true;
  }

  if (!isDirectory && basename.endsWith(".zip") && basename.includes("review")) {
    return true;
  }

  return false;
}

async function scanDirectory(root, { sourceTreeMode = false } = {}) {
  const findings = [];

  async function walk(currentDirectory, relativeDirectory = "") {
    const entries = await readdir(currentDirectory, { withFileTypes: true });
    entries.sort((left, right) => left.name.localeCompare(right.name));

    for (const entry of entries) {
      const relativePath = relativeDirectory
        ? path.posix.join(relativeDirectory, entry.name)
        : entry.name;
      const symbolicLink = entry.isSymbolicLink();
      if (
        sourceTreeMode
        && !symbolicLink
        && isSourceTreeLocalArtifact(relativePath, { isDirectory: entry.isDirectory() })
      ) {
        continue;
      }

      const finding = classifyReleasePath(relativePath, {
        isDirectory: entry.isDirectory(),
        isSymbolicLink: symbolicLink,
      });

      if (finding) {
        findings.push(finding);
      }

      if (entry.isDirectory() && !symbolicLink && !finding) {
        await walk(path.join(currentDirectory, entry.name), relativePath);
      }
    }
  }

  await walk(root);
  return findings;
}

function findEndOfCentralDirectory(buffer) {
  const minimumOffset = Math.max(0, buffer.length - 22 - ZIP_MAX_COMMENT_LENGTH);
  for (let offset = buffer.length - 22; offset >= minimumOffset; offset -= 1) {
    if (buffer.readUInt32LE(offset) !== ZIP_END_SIGNATURE) {
      continue;
    }
    const commentLength = buffer.readUInt16LE(offset + 20);
    if (offset + 22 + commentLength === buffer.length) {
      return offset;
    }
  }
  throw new Error("ZIP end-of-central-directory record was not found");
}

/** Read entry names from a standard (non-ZIP64) ZIP central directory. */
export function parseZipEntryNames(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 22) {
    throw new Error("release archive is not a valid ZIP file");
  }

  const endOffset = findEndOfCentralDirectory(buffer);
  const diskNumber = buffer.readUInt16LE(endOffset + 4);
  const centralDisk = buffer.readUInt16LE(endOffset + 6);
  const diskEntryCount = buffer.readUInt16LE(endOffset + 8);
  const totalEntryCount = buffer.readUInt16LE(endOffset + 10);
  const centralSize = buffer.readUInt32LE(endOffset + 12);
  const centralOffset = buffer.readUInt32LE(endOffset + 16);

  if (diskNumber !== 0 || centralDisk !== 0 || diskEntryCount !== totalEntryCount) {
    throw new Error("multi-disk ZIP archives are not supported by the release scanner");
  }
  if (totalEntryCount === 0xffff || centralSize === 0xffffffff || centralOffset === 0xffffffff) {
    throw new Error("ZIP64 archives are not supported; scan the release staging directory instead");
  }
  if (centralOffset + centralSize > endOffset || centralOffset > buffer.length) {
    throw new Error("ZIP central directory points outside the archive");
  }

  const names = [];
  let cursor = centralOffset;
  for (let index = 0; index < totalEntryCount; index += 1) {
    if (cursor + 46 > buffer.length || buffer.readUInt32LE(cursor) !== ZIP_CENTRAL_SIGNATURE) {
      throw new Error(`invalid ZIP central-directory entry at index ${index}`);
    }

    const flags = buffer.readUInt16LE(cursor + 8);
    const nameLength = buffer.readUInt16LE(cursor + 28);
    const extraLength = buffer.readUInt16LE(cursor + 30);
    const commentLength = buffer.readUInt16LE(cursor + 32);
    const nextCursor = cursor + 46 + nameLength + extraLength + commentLength;
    if (nameLength === 0 || nextCursor > buffer.length) {
      throw new Error(`invalid ZIP entry lengths at index ${index}`);
    }

    const encoding = (flags & 0x0800) !== 0 ? "utf8" : "latin1";
    const name = buffer.subarray(cursor + 46, cursor + 46 + nameLength).toString(encoding);
    if (name.includes("\0")) {
      throw new Error(`ZIP entry at index ${index} contains a NUL byte`);
    }
    names.push(name);
    cursor = nextCursor;
  }

  if (cursor !== centralOffset + centralSize) {
    throw new Error("ZIP central-directory size does not match its entries");
  }
  return names;
}

async function scanZip(archivePath) {
  const entryNames = parseZipEntryNames(await readFile(archivePath));
  return entryNames
    .map((entryName) => classifyReleasePath(entryName, { isDirectory: entryName.endsWith("/") }))
    .filter(Boolean);
}

export async function scanReleaseTarget(target, { sourceTreeMode = false } = {}) {
  const absoluteTarget = path.resolve(target);
  const targetStat = await lstat(absoluteTarget);
  let findings;

  if (targetStat.isDirectory()) {
    findings = await scanDirectory(absoluteTarget, { sourceTreeMode });
  } else if (targetStat.isFile() && path.extname(absoluteTarget).toLowerCase() === ".zip") {
    if (sourceTreeMode) {
      throw new Error("--source-tree can only be used with a directory target");
    }
    findings = await scanZip(absoluteTarget);
  } else {
    throw new Error("release scan target must be a directory or .zip archive");
  }

  return {
    target: absoluteTarget,
    findings: findings.sort((left, right) => left.path.localeCompare(right.path)),
  };
}

function isMainModule() {
  return process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
}

async function main() {
  const args = process.argv.slice(2);
  const sourceTreeMode = args.includes("--source-tree");
  const targets = args.filter((arg) => arg !== "--source-tree");
  if (targets.length !== 1) {
    console.error("Usage: npm run scan:release -- [--source-tree] <staging-directory-or-zip>");
    process.exitCode = 2;
    return;
  }

  try {
    const report = await scanReleaseTarget(targets[0], { sourceTreeMode });
    if (report.findings.length === 0) {
      console.log(`Release content scan passed: ${report.target}`);
      return;
    }

    console.error(`Release content scan failed with ${report.findings.length} violation(s):`);
    for (const finding of report.findings.slice(0, 100)) {
      console.error(`- [${finding.code}] ${finding.path}: ${finding.description}`);
    }
    if (report.findings.length > 100) {
      console.error(`- ... ${report.findings.length - 100} additional violation(s) omitted`);
    }
    process.exitCode = 1;
  } catch (error) {
    console.error(`Release content scan could not run: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 2;
  }
}

if (isMainModule()) {
  await main();
}
