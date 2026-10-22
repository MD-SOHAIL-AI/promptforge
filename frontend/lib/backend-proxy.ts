/** Resolve only the app's loopback backend; retain the request path and query. */
export function backendProxyUrl(requestUrl: string, backendUrl: string): URL {
  const target = new URL(backendUrl);
  if (target.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(target.hostname)
      || target.username || target.password || target.pathname !== "/" || target.search || target.hash) {
    throw new Error("FORGEX_BACKEND_PROXY_TARGET_INVALID");
  }
  const incoming = new URL(requestUrl);
  const match = incoming.pathname.match(/^\/api\/(?:forgex|promptforge)(\/.*|$)/);
  if (!match) throw new Error("FORGEX_BACKEND_PROXY_PATH_INVALID");
  target.pathname = match[1] || "/";
  target.search = incoming.search;
  return target;
}

type ProxyOptions = {
  fetcher?: typeof fetch;
  healthTimeoutMs?: number;
  headersTimeoutMs?: number;
  onFailure?: (event: Record<string, unknown>) => void;
  onHealth?: (event: Record<string, unknown>) => void;
};

const HOP_HEADERS = ["connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
  "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"];

async function untilAborted<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  let abort = () => {};
  const stopped = new Promise<never>((_, reject) => {
    abort = () => reject(signal.reason ?? new DOMException("Request cancelled", "AbortError"));
    signal.addEventListener("abort", abort, {once: true});
    if (signal.aborted) abort();
  });
  try { return await Promise.race([operation, stopped]); }
  finally { signal.removeEventListener("abort", abort); }
}

function transportCause(error: unknown): Record<string, unknown> {
  const value = error && typeof error === "object" ? error as {name?: string; cause?: {code?: string}} : {};
  const names = ["TypeError", "AbortError", "TimeoutError", "Error"];
  const codes = ["ECONNREFUSED", "ECONNRESET", "ENOTFOUND", "EHOSTUNREACH", "ETIMEDOUT", "UND_ERR_CONNECT_TIMEOUT", "UND_ERR_SOCKET"];
  // Error messages and stacks can contain credentials. Report only known labels.
  return {cause_name: names.includes(value.name ?? "") ? value.name : "Error",
    ...(codes.includes(value.cause?.code ?? "") ? {cause_code: value.cause!.code} : {})};
}

type HealthResult = {body: Record<string, unknown> | null; ok: boolean; durationMs: number};
type HealthProbe = {id: string; result: Promise<HealthResult>};
const healthProbes = new WeakMap<typeof fetch, Map<string, HealthProbe>>();

function backendHealth(target: URL, fetcher: typeof fetch, timeoutMs: number, requestId: string): HealthProbe {
  let pending = healthProbes.get(fetcher);
  if (!pending) { pending = new Map(); healthProbes.set(fetcher, pending); }
  const key = `${target.origin}:${timeoutMs}`;
  const existing = pending.get(key);
  if (existing) return existing;
  const id = `${requestId}:proxy-health`;
  const started = performance.now();
  const signal = AbortSignal.timeout(timeoutMs);
  const result = untilAborted((async () => {
    const response = await fetcher(new URL("/health", target), {cache: "no-store", redirect: "manual",
      headers: {"X-Request-ID": id}, signal});
    const parsed = await response.json().catch(() => null);
    return {body: parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null,
      ok: response.ok, durationMs: Math.round(performance.now() - started)};
  })(), signal).finally(() => { if (pending!.get(key)?.result === result) pending!.delete(key); });
  const probe = {id, result};
  // Share in-flight probes only. Every later request verifies identity again.
  if (pending.size < 128) pending.set(key, probe);
  return probe;
}

/** Forward once to an identified backend and preserve streaming responses. */
export async function proxyBackendRequest(request: Request, backendUrl: string, options: ProxyOptions = {}): Promise<Response> {
  const fetcher = options.fetcher ?? fetch;
  const requestId = request.headers.get("x-request-id") ?? `proxy-http-${crypto.randomUUID()}`;
  const mutation = !["GET", "HEAD", "OPTIONS"].includes(request.method);
  let dispatched = false;
  const started = performance.now();
  let phase = "target";
  let probeId: string | undefined;
  let identity: Record<string, unknown> = {};
  const failure = (code: string, message: string, status = 503, details: Record<string, unknown> = {}) => {
    const diagnostic = {request_dispatched: dispatched, phase, duration_ms: Math.round(performance.now() - started),
      health_request_id: probeId, ...identity, ...details};
    const event = {event: "proxy.backend.failed", timestamp: new Date().toISOString(), request_id: requestId, method: request.method,
      path: new URL(request.url).pathname, error_code: code, ...diagnostic};
    options.onFailure?.(event);
    return Response.json({code, message, details: diagnostic, request_id: requestId},
      {status, headers: {"Cache-Control": "no-store", "X-Request-ID": requestId}});
  };
  let target: URL;
  try {
    target = backendProxyUrl(request.url, backendUrl);
  } catch {
    return failure("BACKEND_PROXY_TARGET_INVALID", "The configured ForgeX backend address is invalid.");
  }
  if (request.signal.aborted) return failure("REQUEST_CANCELLED", "The request was cancelled.", 499, {failure_reason: "caller_cancelled"});
  phase = "health";
  try {
    // No credentials or mutation bodies go to an unrelated listener.
    const probe = backendHealth(target, fetcher, options.healthTimeoutMs ?? 2000, requestId);
    probeId = probe.id;
    const health = await untilAborted(probe.result, request.signal);
    const body = health.body;
    if (body?.service !== "forgex-backend") {
      return failure("BACKEND_IDENTITY_MISMATCH", "The configured port is not serving a ForgeX backend. Reconnect or restart ForgeX.", 502,
        {failure_reason: "identity_mismatch", health_duration_ms: health.durationMs});
    }
    identity = {backend_service: "forgex-backend"};
    if (typeof body.source_fingerprint === "string" && /^[a-zA-Z0-9._:-]{1,128}$/.test(body.source_fingerprint)) {
      identity.backend_source_fingerprint = body.source_fingerprint;
    }
    if (!health.ok) return failure("BACKEND_UNAVAILABLE", "The ForgeX backend is not ready. Saved work is preserved; reconnect to refresh its status.", 503,
      {failure_reason: "health_rejected", health_duration_ms: health.durationMs});
    options.onHealth?.({event: "proxy.backend.health", timestamp: new Date().toISOString(), request_id: requestId,
      health_request_id: probeId, duration_ms: health.durationMs, ...identity});
    if (target.pathname === "/health" && request.method === "GET") {
      return Response.json(body, {headers: {"Cache-Control": "no-store", "X-Request-ID": requestId}});
    }
  } catch (error) {
    if (request.signal.aborted) return failure("REQUEST_CANCELLED", "The request was cancelled.", 499, {failure_reason: "caller_cancelled"});
    const cause = transportCause(error);
    return failure("BACKEND_UNAVAILABLE", "The ForgeX backend is unavailable. Saved work is preserved; reconnect or restart ForgeX.", 503,
      {...cause, failure_reason: cause.cause_name === "TimeoutError" ? "health_timeout" : "health_transport_error"});
  }

  phase = "response_headers";
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.headersTimeoutMs ?? 15000);
  try {
    const headers = new Headers(request.headers);
    for (const name of HOP_HEADERS) headers.delete(name);
    headers.set("X-Request-ID", requestId);
    const init = {method: request.method, headers, cache: "no-store", redirect: "manual",
      signal: AbortSignal.any([request.signal, controller.signal]), ...(!["GET", "HEAD"].includes(request.method)
        ? {body: request.body, duplex: "half"} : {})} as RequestInit;
    dispatched = true;
    const response = await untilAborted(fetcher(target, init), init.signal as AbortSignal);
    const responseHeaders = new Headers(response.headers);
    for (const name of [...HOP_HEADERS, "content-encoding"]) responseHeaders.delete(name);
    responseHeaders.set("X-Request-ID", requestId);
    responseHeaders.set("Cache-Control", "no-store");
    // The header deadline is cleared below: it must not terminate SSE bodies.
    return new Response(response.body, {status: response.status, headers: responseHeaders});
  } catch (error) {
    const details = {...transportCause(error), failure_reason: request.signal.aborted ? "caller_cancelled" :
      controller.signal.aborted ? "headers_timeout" : "request_transport_error"};
    return mutation && dispatched
      ? failure("REQUEST_RESULT_UNKNOWN", "The backend connection ended after dispatch. Inspect the saved result before retrying this operation.", 503, details)
      : request.signal.aborted ? failure("REQUEST_CANCELLED", "The request was cancelled.", 499, details)
      : failure("BACKEND_UNAVAILABLE", "The ForgeX backend connection ended. Reconnect to retrieve saved work.", 503, details);
  } finally {
    clearTimeout(timeout);
  }
}
