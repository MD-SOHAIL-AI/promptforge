import { once } from "node:events";
import { execute, normalizeError } from "./engine.mjs";

// Third-party SDK logging must never corrupt the NDJSON channel or expose
// credentials. Python owns user-facing diagnostics.
for (const method of ["log", "info", "debug", "warn", "error"]) console[method] = () => {};

async function emit(value) {
  if (!process.stdout.write(JSON.stringify(value) + "\n")) await once(process.stdout, "drain");
}

async function main() {
const controller = new AbortController();
let input;
for (const signal of ["SIGTERM", "SIGINT"]) process.on(signal, () => controller.abort());
try {
  let body = "";
  for await (const chunk of process.stdin) {
    body += chunk;
    if (Buffer.byteLength(body) > 8_388_608) throw new Error("Model request exceeds limit");
  }
  input = JSON.parse(body);
  const timer = setTimeout(() => {
    const error = new Error("Provider attempt total deadline exceeded");
    error.stage = "attempt_total";
    controller.abort(error);
  }, input.policy?.total_ms ?? 300_000);
  try { await execute(input, emit, controller.signal); }
  finally { clearTimeout(timer); }
} catch (error) {
  const normalized = normalizeError(error, input?.provider_id);
  const secrets = [input?.api_key];
  function collect(value) {
    for (const [key, item] of Object.entries(value ?? {})) {
      if (item && typeof item === "object") collect(item);
      else if (/key|token|secret|password|authorization|cookie/i.test(key) && typeof item === "string" && item.length >= 6) secrets.push(item);
    }
  }
  collect(input?.options);
  try { collect(JSON.parse(input?.api_key)); } catch { /* Plain API key. */ }
  for (const value of Object.values(input?.headers ?? {})) if (typeof value === "string" && value.length >= 6) secrets.push(value);
  function redact(value) {
    if (typeof value === "string") {
      for (const secret of secrets.filter(Boolean)) value = value.split(secret).join("<redacted>");
      return value;
    }
    if (Array.isArray(value)) return value.map(redact);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redact(item)]));
    return value;
  }
  if (normalized.response_headers) normalized.response_headers = Object.fromEntries(Object.entries(normalized.response_headers).map(([key, value]) => [key, /authorization|key|token|secret|cookie/i.test(key) ? "<redacted>" : value]));
  await emit({ type: "error", error: redact(normalized) });
  process.exitCode = 1;
}
}
void main();
