import { cp, mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

// Keep Electron Builder's dependency walk out of the development workspace.
// The compiled shell imports only Node/Electron built-ins and relative modules;
// the standalone server and frontend are copied through extraResources.
export async function createReleaseShell(projectDir = process.cwd()) {
  const root = path.resolve(projectDir);
  const metadata = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));
  const stagingRoot = path.join(root, ".forgex");
  await mkdir(stagingRoot, { recursive: true });
  const shellDir = await mkdtemp(path.join(stagingRoot, "release-shell-"));
  await mkdir(path.join(shellDir, "dist"));
  await cp(path.join(root, "dist", "electron"), path.join(shellDir, "dist", "electron"), { recursive: true });
  await writeFile(path.join(shellDir, "package.json"), JSON.stringify({
    name: metadata.name,
    version: metadata.version,
    description: metadata.description,
    main: metadata.main,
    private: true,
  }, null, 2) + "\n");
  return shellDir;
}
