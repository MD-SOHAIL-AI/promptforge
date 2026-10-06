import { NextResponse, type NextRequest } from "next/server";
import { backendProxyUrl } from "./lib/backend-proxy";

export function middleware(request: NextRequest) {
  // Resolve at server startup, including in the packaged standalone server.
  const backend = process.env.FORGEX_BACKEND_URL ?? process.env.FORGEX_API_URL
    ?? `http://127.0.0.1:${process.env.FORGEX_BACKEND_PORT ?? "8000"}`;
  return NextResponse.rewrite(backendProxyUrl(request.url, backend));
}

export const config = { matcher: ["/api/forgex/:path*", "/api/promptforge/:path*"] };
