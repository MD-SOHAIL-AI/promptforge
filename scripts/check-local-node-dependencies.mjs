import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

function matchesPlatform(values, current) {
  if (!values?.length) return true;
  if (values.includes(`!${current}`)) return false;
  const allowed = values.filter(value => !value.startsWith("!"));
  return !allowed.length || allowed.includes(current) || allowed.includes("any");
}

// Inspect package files directly: no npm registry requests or package mutations.
export function localDependencyProblems(directory, platform = process.platform, arch = process.arch) {
  const problems = [];
  let manifest, lock;
  try {
    manifest = JSON.parse(fs.readFileSync(path.join(directory, "package.json"), "utf8"));
    lock = JSON.parse(fs.readFileSync(path.join(directory, "package-lock.json"), "utf8"));
    if (!lock.packages?.[""]) return ["A modern package-lock.json is required."];
  } catch (error) {
    return [`Cannot read dependency manifests: ${error.message}`];
  }
  for (const group of ["dependencies", "devDependencies", "optionalDependencies"]) {
    const expected = Object.entries(manifest[group] ?? {}).sort();
    const locked = Object.entries(lock.packages[""][group] ?? {}).sort();
    if (JSON.stringify(expected) !== JSON.stringify(locked)) {
      problems.push(`package-lock.json does not match package.json ${group}.`);
    }
  }
  for (const [relative, entry] of Object.entries(lock.packages)) {
    if (!relative.startsWith("node_modules/") ||
        !matchesPlatform(entry.os, platform) || !matchesPlatform(entry.cpu, arch)) continue;
    const installedPath = path.join(directory, relative, "package.json");
    // An absent optional package is valid. Electron and the desktop build also
    // check the native binaries actually required on this machine.
    if (entry.optional && !fs.existsSync(installedPath)) continue;
    try {
      const installed = JSON.parse(fs.readFileSync(installedPath, "utf8"));
      const expectedVersion = entry.link ? lock.packages[entry.resolved]?.version : entry.version;
      if (!expectedVersion || installed.version !== expectedVersion) {
        problems.push(`${relative}: expected ${expectedVersion ?? "a locked version"}, found ${installed.version}.`);
      }
      const defaultBinName = (installed.name ?? relative).split("/").at(-1);
      const binaries = typeof installed.bin === "string" ? [[defaultBinName, installed.bin]] : Object.entries(installed.bin ?? {});
      for (const [binName, binary] of binaries) {
        if (!fs.existsSync(path.join(directory, relative, binary))) {
          problems.push(`${relative}: missing executable ${binary}.`);
        }
        const modulesPrefix = relative.slice(0, relative.lastIndexOf("node_modules/"));
        const shim = path.join(directory, modulesPrefix, "node_modules", ".bin", binName + (platform === "win32" ? ".cmd" : ""));
        if (!fs.existsSync(shim)) problems.push(`${relative}: missing command shim ${binName}.`);
      }
    } catch {
      problems.push(`${relative}: missing or unreadable package.json.`);
    }
  }
  return problems;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const directory = path.resolve(process.argv[2] ?? ".");
  const problems = localDependencyProblems(directory);
  if (problems.length) {
    console.log(problems.slice(0, 12).join("\n"));
    if (problems.length > 12) console.log(`... and ${problems.length - 12} other missing or mismatched dependencies.`);
    process.exitCode = 1;
  } else {
    console.log("All required Node packages are already installed at their locked versions.");
  }
}
