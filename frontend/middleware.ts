import { NextResponse, type NextRequest } from "next/server";
import { proxyBackendRequest } from "./lib/backend-proxy";

export async function middleware(request: NextRequest) {
  // Resolve at server startup, including in the packaged standalone server.
  const backend = process.env.FORGEX_BACKEND_URL ?? process.env.FORGEX_API_URL
    ?? process.env.PROMPTFORGE_API_URL
    ?? `http://127.0.0.1:${process.env.FORGEX_BACKEND_PORT ?? process.env.PROMPTFORGE_BACKEND_PORT ?? "8000"}`;
  const response = await proxyBackendRequest(request, backend, {
    onFailure: event => console.error(JSON.stringify(event)),
    onHealth: event => { if (Number(event.duration_ms) >= 250) console.warn(JSON.stringify(event)); },
  });
  return new NextResponse(response.body, {status: response.status, headers: response.headers});
}

export const config = { runtime: "nodejs", matcher: ["/api/forgex/:path*", "/api/promptforge/:path*"] };
