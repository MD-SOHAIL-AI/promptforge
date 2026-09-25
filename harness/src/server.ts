import { createHash, timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import path from "node:path";

import { HARNESS_PROTOCOL, HARNESS_RUNTIME, isJsonObject, type RpcRequest, type RpcResponse } from "./contracts";
import { SessionEventStore } from "./event-store";
import { IdempotencyError, IdempotencyStore } from "./idempotency";
import { HarnessRuntime, RpcMethodError } from "./runtime";

const port = integer(process.env.FORGEX_HARNESS_PORT, 8765);
const host = "127.0.0.1";
const token = process.env.FORGEX_HARNESS_TOKEN?.trim();
const dataRoot = process.env.FORGEX_HARNESS_DATA_DIR?.trim() || path.resolve(".forgex", "harness");
if (!token || token.length < 32) throw new Error("FORGEX_HARNESS_TOKEN must contain at least 32 characters");
const harnessToken: string = token;

const runtime = new HarnessRuntime(new SessionEventStore(path.join(dataRoot, "sessions")));
const idempotency = new IdempotencyStore(path.join(dataRoot, "idempotency"));

async function main(): Promise<void> {
await runtime.initialize();
await idempotency.initialize();

const server = createServer(async (request, response) => {
  securityHeaders(response);
  if (request.method === "GET" && request.url === "/health") {
    if (!authorized(request, harnessToken)) return json(response, 401, { error: "HARNESS_AUTH_REQUIRED" });
    return json(response, 200, { status: "healthy", service: "forgex-harness", runtime: HARNESS_RUNTIME, protocol: HARNESS_PROTOCOL, pid: process.pid });
  }
  if (request.method !== "POST" || request.url !== "/rpc") return json(response, 404, { error: "NOT_FOUND" });
  if (!authorized(request, harnessToken)) return json(response, 401, { error: "HARNESS_AUTH_REQUIRED" });
  let rpc: RpcRequest | undefined;
  try {
    const body = await readBody(request, 1_048_576);
    const value: unknown = JSON.parse(body);
    if (!isJsonObject(value) || value.protocol !== HARNESS_PROTOCOL || typeof value.id !== "string" || typeof value.method !== "string" || !isJsonObject(value.params)
      || (value.idempotencyKey !== undefined && typeof value.idempotencyKey !== "string")) {
      throw new RpcMethodError("RPC_REQUEST_INVALID", "Invalid RPC envelope");
    }
    rpc = value as unknown as RpcRequest;
    const invoke = () => runtime.invoke(rpc!.method, rpc!.params);
    const result = rpc.idempotencyKey
      ? await idempotency.execute(rpc.idempotencyKey, requestFingerprint(rpc.method, rpc.params), invoke)
      : await invoke();
    const reply: RpcResponse = { protocol: HARNESS_PROTOCOL, id: rpc.id, ok: true, result };
    return json(response, 200, reply);
  } catch (error) {
    const known = error instanceof RpcMethodError || error instanceof IdempotencyError;
    const reply: RpcResponse = {
      protocol: HARNESS_PROTOCOL,
      id: rpc?.id ?? "invalid",
      ok: false,
      error: {
        code: known ? error.code : "RPC_INTERNAL_ERROR",
        message: known ? error.message : "Harness request failed",
        retryable: error instanceof RpcMethodError ? error.retryable : false,
      },
    };
    return json(response, known ? 400 : 500, reply);
  }
});

server.listen(port, host, () => process.stdout.write(`[forgex-harness] ready http://${host}:${port}\n`));
for (const signal of ["SIGINT", "SIGTERM"] as const) process.on(signal, () => server.close(() => process.exit(0)));
}

void main().catch((error) => {
  process.stderr.write(`[forgex-harness] startup failed: ${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});

function authorized(request: IncomingMessage, expected: string): boolean {
  const value = request.headers.authorization;
  if (!value?.startsWith("Bearer ")) return false;
  const actual = Buffer.from(value.slice(7), "utf8");
  const wanted = Buffer.from(expected, "utf8");
  return actual.length === wanted.length && timingSafeEqual(actual, wanted);
}

async function readBody(request: IncomingMessage, maxBytes: number): Promise<string> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > maxBytes) throw new RpcMethodError("RPC_REQUEST_TOO_LARGE", "RPC body exceeds limit");
    chunks.push(buffer);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function json(response: ServerResponse, status: number, value: unknown): void {
  const payload = JSON.stringify(value);
  response.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(payload) });
  response.end(payload);
}

function securityHeaders(response: ServerResponse): void {
  response.setHeader("Cache-Control", "no-store");
  response.setHeader("X-Content-Type-Options", "nosniff");
}

function integer(raw: string | undefined, fallback: number): number {
  const value = Number.parseInt(raw ?? "", 10);
  if (!Number.isInteger(value) || value < 1 || value > 65535) return fallback;
  return value;
}

function requestFingerprint(method: string, params: unknown): string {
  return createHash("sha256").update(canonical({ method, params }), "utf8").digest("hex");
}

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
    .join(",")}}`;
}
