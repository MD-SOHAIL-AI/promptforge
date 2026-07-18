import type { NextConfig } from "next";
import { resolve } from "node:path";

const backendPort = process.env.FORGEX_BACKEND_PORT ?? process.env.PROMPTFORGE_BACKEND_PORT ?? "8000";
const backendUrl = process.env.PROMPTFORGE_API_URL ?? `http://127.0.0.1:${backendPort}`;
const devWatchIgnored = [
  "**/.git/**",
  "**/.promptforge/**",
  "**/workspace/**",
  "**/dist/**",
  "**/promptforge-promo-video/**",
];

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: resolve(process.cwd(), ".."),
  webpack(config, { dev }) {
    if (dev) {
      const existingIgnored = config.watchOptions?.ignored;
      const ignored = Array.isArray(existingIgnored)
        ? existingIgnored
        : existingIgnored
          ? [existingIgnored]
          : [];
      config.watchOptions = {
        ...config.watchOptions,
        ignored: [...ignored, ...devWatchIgnored],
      };
    }
    return config;
  },
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
