import path from "node:path";

export function assertContained(target, parent) {
  const relative = path.relative(path.resolve(parent), path.resolve(target));
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("QA cleanup target is outside the contained QA root.");
  }
}

export function packagedFlagMatrix() {
  return [
    { name: "defaults", patchApply: false, rollbackRestore: false },
    { name: "apply_only", patchApply: true, rollbackRestore: false },
    { name: "restore_only", patchApply: false, rollbackRestore: true },
    { name: "apply_and_restore", patchApply: true, rollbackRestore: true },
  ];
}

export function qaFixturesAllowed(env) {
  return env.FORGEX_QA_MODE === "1";
}
