const fs = require("node:fs");
const path = require("node:path");
const pointer = JSON.parse(fs.readFileSync(path.join(__dirname, ".forgex", "release-stage.json"), "utf8"));
const stage = path.resolve(pointer.stage);
const expected = path.join(__dirname, ".forgex", "release-staging") + path.sep;
if (!stage.startsWith(expected) || !fs.existsSync(path.join(stage, "forgex", "release-manifest.json"))) {
  throw new Error("RELEASE_STAGE_INVALID");
}
const unsignedTest = process.env.FORGEX_UNSIGNED_TEST_BUILD === "1";
const manifest = JSON.parse(fs.readFileSync(path.join(stage, "forgex", "release-manifest.json"), "utf8"));
const bundledExecutables = Object.keys(manifest.files).filter(file => file.endsWith(".exe"));
// Use npm's checksum-verified Electron distribution. Builder 26.15.3's ZIP
// extraction uses a directory rename that repeatedly fails on this Windows host.
// Check the exact installed version before using the supported unpacked input.
const electronVersion = require("./package.json").devDependencies.electron;
const electronDist = path.join(__dirname, "node_modules", "electron", "dist");
if (fs.readFileSync(path.join(electronDist, "version"), "utf8").trim().replace(/^v/, "") !== electronVersion) {
  throw new Error("RELEASE_ELECTRON_RUNTIME_VERSION_MISMATCH");
}
module.exports = {
  appId: "com.forgex.desktop", productName: "ForgeX", asar: true,
  electronVersion, electronDist,
  directories: { output: ".forgex/installers" },
  files: ["dist/electron/**", "package.json"],
  extraResources: [
    { from: path.join(stage, "forgex"), to: "forgex" },
    // Electron Builder filters a root node_modules directory from a general
    // resource mapping. Copy the standalone server's root dependencies too.
    { from: path.join(stage, "forgex", "node_modules"), to: "forgex/node_modules" },
  ],
  afterPack: async context => {
    const { scanPackage } = await import("./scripts/scan-packaged-content.mjs");
    const result = await scanPackage(path.join(context.appOutDir, "resources", "forgex"));
    if (!result.passed) throw new Error(`PACKAGED_CONTENT_REJECTED: ${result.errors.join("; ")}`);
  },
  forceCodeSigning: !unsignedTest,
  win: { target: [{ target: "nsis", arch: ["x64"] }],
    icon: "assets/forgex.ico",
    artifactName: "ForgeX-${version}-windows-x64-setup.${ext}",
    // Preserve the reviewed upstream runtime bytes and their manifest hashes.
    // The ForgeX shell and NSIS installer receive the publisher's signature.
    signExts: bundledExecutables.map(file => "!" + path.join("resources", "forgex", file)),
    signExecutable: !unsignedTest },
  nsis: { oneClick: false, perMachine: false, allowElevation: false,
    allowToChangeInstallationDirectory: true, deleteAppDataOnUninstall: false,
    createDesktopShortcut: true, createStartMenuShortcut: true },
};
