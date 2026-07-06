import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const workspaceRoot = path.resolve(root, "workspace");
const target = path.resolve(workspaceRoot, "forgex-apply-test");
const reset = process.argv.includes("--reset");

if (!target.startsWith(`${workspaceRoot}${path.sep}`)) {
  throw new Error("Refusing to create QA workspace outside the repository workspace directory.");
}

if (fs.existsSync(target)) {
  if (!reset) {
    console.error("QA workspace already exists. Re-run with: npm.cmd run qa:create-apply-workspace -- --reset");
    process.exit(1);
  }
  fs.rmSync(target, { recursive: true, force: true });
}

fs.mkdirSync(path.join(target, "src"), { recursive: true });
write("README.md", "# ForgeX Apply QA\n\nThis is a throwaway workspace.\n");
write("platformio.ini", "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n");
write("src/main.cpp", "#include <Arduino.h>\n\nvoid setup() {\n}\n\nvoid loop() {\n}\n");
write("src/delete_me.cpp", "int delete_me_value = 1;\n");
write("QA_NOT_REAL_PROJECT.txt", "This workspace is disposable and exists only for ForgeX patch apply QA.\n");

console.log("Created throwaway apply QA workspace: workspace/forgex-apply-test");

function write(relativePath, content) {
  fs.writeFileSync(path.join(target, relativePath), content, "utf-8");
}
