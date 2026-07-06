const SAFE_CODEX_ENV_KEYS = [
  "PATH", "Path", "PATHEXT", "SystemRoot", "WINDIR", "ComSpec", "TEMP", "TMP",
  "USERPROFILE", "USERNAME", "USERDOMAIN", "LOGONSERVER", "SESSIONNAME", "HOME",
  "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "ProgramData",
  "ProgramFiles", "ProgramFiles(x86)", "CommonProgramFiles", "OneDrive", "SystemDrive",
  "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "OS", "CI", "FORGEX_TEST_MODE",
  "FORGEX_ENABLE_CODEX_SMOKE",
];
const FORBIDDEN_ENV_NAME = /KEY|TOKEN|SECRET|PASSWORD|COOKIE|CREDENTIAL|AUTH/i;

export function buildCodexSafeUserEnv(env = process.env) {
  return Object.fromEntries(SAFE_CODEX_ENV_KEYS
    .filter((name) => !FORBIDDEN_ENV_NAME.test(name) && typeof env[name] === "string")
    .map((name) => [name, env[name]]));
}

export function buildMinimalSafeEnv(env = process.env) {
  const names = ["PATH", "Path", "PATHEXT", "SystemRoot", "WINDIR", "ComSpec", "TEMP", "TMP"];
  return Object.fromEntries(names.filter((name) => typeof env[name] === "string").map((name) => [name, env[name]]));
}

export function buildInheritedMinusSecretsEnv(env = process.env) {
  let removed = 0;
  const values = {};
  for (const [name, value] of Object.entries(env)) {
    if (FORBIDDEN_ENV_NAME.test(name)) { removed += 1; continue; }
    if (typeof value === "string") values[name] = value;
  }
  return { env: values, removed };
}

export { SAFE_CODEX_ENV_KEYS };
