import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createReleaseShell } from "./release-shell.mjs";

test("release shell contains compiled Electron files without development dependencies", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "forgex-release-shell-test-"));
  await mkdir(path.join(root, "dist", "electron"), { recursive: true });
  await writeFile(path.join(root, "package.json"), JSON.stringify({
    name: "forgex-desktop", version: "1.2.3", description: "desktop", main: "dist/electron/main.js",
    devDependencies: { electron: "44.4.5" },
  }));
  await writeFile(path.join(root, "dist", "electron", "main.js"), "require('electron');\n");
  const shell = await createReleaseShell(root);
  assert.deepEqual((await readdir(shell)).sort(), ["dist", "package.json"]);
  assert.equal(await readFile(path.join(shell, "dist", "electron", "main.js"), "utf8"), "require('electron');\n");
  assert.deepEqual(JSON.parse(await readFile(path.join(shell, "package.json"), "utf8")), {
    name: "forgex-desktop", version: "1.2.3", description: "desktop",
    main: "dist/electron/main.js", private: true,
  });
});
