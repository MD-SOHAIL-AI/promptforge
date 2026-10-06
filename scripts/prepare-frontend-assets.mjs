import { cp, mkdir, readFile, rm, lstat, realpath, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(root, "frontend/node_modules/monaco-editor");
const destination = path.join(root, "frontend/public/vendor/monaco");
const expectedParent = path.join(root, "frontend/public/vendor");
await mkdir(expectedParent, { recursive: true });
if (await realpath(expectedParent) !== expectedParent || path.dirname(destination) !== expectedParent) {
  throw new Error("FRONTEND_ASSET_DESTINATION_UNSAFE");
}
try {
  if ((await lstat(destination)).isSymbolicLink()) throw new Error("FRONTEND_ASSET_LINK_UNSAFE");
} catch (error) { if (error.code !== "ENOENT") throw error; }
const { version } = JSON.parse(await readFile(path.join(source, "package.json"), "utf8"));
// This exact generated asset directory is the only recursive removal target.
await rm(destination, { recursive: true, force: true });
await mkdir(destination);
await cp(path.join(source, "min/vs"), path.join(destination, "vs"), { recursive: true });
await cp(path.join(source, "LICENSE"), path.join(destination, "LICENSE"));
await cp(path.join(source, "ThirdPartyNotices.txt"), path.join(destination, "ThirdPartyNotices.txt"));
await writeFile(path.join(destination, "version.json"), JSON.stringify({ package: "monaco-editor", version }) + "\n");
console.log(`Prepared local Monaco ${version} assets for offline editing.`);
