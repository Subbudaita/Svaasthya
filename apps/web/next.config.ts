import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The shell reads global data files (e.g. data/pillars.json) from the repo root.
  experimental: { externalDir: true },
  outputFileTracingRoot: path.join(__dirname, "../.."),
  // Linting arrives with the real UI in Phase 7; `npm run typecheck` covers types now.
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
