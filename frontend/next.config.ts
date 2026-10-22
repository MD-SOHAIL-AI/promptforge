import type { NextConfig } from "next";
import { resolve } from "node:path";

const backendPort = process.env.FORGEX_BACKEND_PORT ?? process.env.PROMPTFORGE_BACKEND_PORT ?? "8000";
const backendUrl = process.env.FORGEX_API_URL ?? process.env.PROMPTFORGE_API_URL ?? `http://127.0.0.1:${backendPort}`;
const configuredBuildCpus = Number(process.env.FORGEX_BUILD_CPUS ?? 2);
const buildCpus = Number.isInteger(configuredBuildCpus) && configuredBuildCpus >= 1 && configuredBuildCpus <= 4
  ? configuredBuildCpus : 2;

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: { cpus: buildCpus },
  distDir: process.env.FORGEX_NEXT_DIST_DIR || ".next",
  outputFileTracingRoot: resolve(process.cwd(), ".."),
  async headers() {
    if (process.env.NODE_ENV !== "production") return [];
    const policy = ["default-src 'self'", "script-src 'self' 'unsafe-inline'",
      "style-src 'self' 'unsafe-inline'", "img-src 'self' data: blob:", "font-src 'self' data:",
      "connect-src 'self' http://127.0.0.1:* http://localhost:* ws://127.0.0.1:* ws://localhost:*",
      "worker-src 'self' blob:", "object-src 'none'", "base-uri 'self'", "frame-ancestors 'none'"];
    return [{ source: "/:path*", headers: [{ key: "Content-Security-Policy", value: policy.join("; ") }] }];
  },
  async rewrites() {
    return [
      {
        source: "/api/forgex/:path*",
        destination: `${backendUrl}/:path*`,
      },
      // Temporary backward-compatible alias for pre-V4 renderer code.
      {
        source: "/api/promptforge/:path*",
        destination: `${backendUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
