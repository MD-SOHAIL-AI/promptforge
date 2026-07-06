import type { NextConfig } from "next";
import { resolve } from "node:path";

const backendPort = process.env.FORGEX_BACKEND_PORT ?? process.env.PROMPTFORGE_BACKEND_PORT ?? "8000";
const backendUrl = process.env.PROMPTFORGE_API_URL ?? `http://127.0.0.1:${backendPort}`;

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: resolve(process.cwd(), ".."),
  async rewrites() {
    return [
      {
        source: "/api/promptforge/:path*",
        destination: `${backendUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
